"""Find, check and propose real-world photos for one Hannah concept target.

WP-H3 of docs/plans/2026-10-19-hannah-concept-photo-curation-plan.md:

1. gather candidates from the Wikidata image, the Commons category (and its
   subcategories when it is small), Commons search on the search terms, and
   the English Wikipedia lead image, in that order (Wikimedia only, Q2);
2. drop what cannot be a usable photo without asking a model: not a bitmap,
   too small, an extreme shape, or a title that names a diagram or symbol;
3. ask the vision model whether each remaining image is a photograph of a
   real example of the concept, and score it;
4. propose the best two, leaving a steward to approve or reject (WP-H4).

Every image Hannah has looked at is kept with its reasons, so a later run
never checks it again and threshold changes need no new model calls. Outside
calls are injected (`http_get_json`, `fetch_bytes`, `complete_vision`) so tests
need neither the network nor a model.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
import re
import time
from typing import Any, Callable
import urllib.parse
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from .hannah_targets import USER_AGENT, parse_first_json_object, readable_name, short_definition, ConceptScope
from .models.schema import HannahConceptPhoto, HannahConceptTarget


COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
THUMB_WIDTH = 800
MAX_GATHERED_PER_CONCEPT = 40
# A category can hold hundreds of files; leave room for the search results after it.
MAX_CATEGORY_FILES = 20
MAX_VISION_CHECKS_PER_CONCEPT = 12
SMALL_CATEGORY_FILES = 6
MIN_SIDE_PX = 400
MAX_ASPECT = 3.0
ACCEPTED_MIME = ("image/jpeg", "image/png", "image/webp")
MAX_IMAGE_BYTES = 8 * 1024 * 1024
PHOTOS_PER_CONCEPT = 2
# Proposals still go to a steward, so this is low; auto-approval (WP-H6) has its own threshold.
PROPOSE_MIN_SCORE = 0.35
# Images whose title and description share no word with the search terms are
# checked last, and only this many per run; the rest are filtered unseen. A
# broad Commons category ("Machines", "Pipes") otherwise spends the whole
# allowance on unrelated files (2026-10-20 trial).
MAX_UNMATCHED_CHECKS = 3
_GENERIC_WORDS = {"the", "and", "for", "with", "type", "industrial", "equipment", "system", "unit", "general"}
NO_CANDIDATES_COOLDOWN = timedelta(days=30)
SEARCHED_COOLDOWN = timedelta(days=30)
ERROR_RETRY_AFTER = timedelta(hours=6)
DEFAULT_MAX_VISION_CALLS_PER_DAY = 500
# Words in a file title that mean the file is not a photograph of the thing.
_TITLE_NOISE = re.compile(
    r"\b(diagram|schematic|symbol|symbols|logo|icon|chart|patent|blueprint|sketch|drawing|map|pictogram|graph)\b"
)
_TAGS = re.compile(r"<[^>]+>")
_IMAGEINFO = {
    "prop": "imageinfo",
    "iiprop": "url|size|mime|extmetadata",
    "iiurlwidth": str(THUMB_WIDTH),
    "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl|ImageDescription",
}

HttpGetJsonFn = Callable[[str, dict[str, str]], dict[str, Any]]
FetchBytesFn = Callable[[str], tuple[bytes, str]]
CompleteVisionFn = Callable[[list[dict[str, Any]]], str]


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


@dataclass
class TargetView:
    """What a run needs to know about a concept, from a target row or a dry-run preview."""

    concept_id: str
    concept_code: str
    name: str
    definition: str
    search_terms: list[str]
    wikidata_image: str | None = None
    commons_category: str | None = None
    wikipedia_url: str | None = None
    target: HannahConceptTarget | None = None


@dataclass
class Candidate:
    source: str
    source_url: str
    image_url: str
    thumb_url: str
    title: str
    author: str | None
    license_label: str | None
    license_url: str | None
    width: int | None
    height: int | None
    mime: str | None
    description: str | None = None
    status: str = "candidate"
    filter_reasons: list[str] = field(default_factory=list)
    verdict: dict[str, Any] = field(default_factory=dict)
    score: float | None = None
    sha256: str | None = None


def _plain(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(html.unescape(_TAGS.sub(" ", value)).split())
    return cleaned or None


def _meta(imageinfo: dict[str, Any], key: str) -> str | None:
    return _plain(((imageinfo.get("extmetadata") or {}).get(key) or {}).get("value"))


def candidate_from_commons_page(page: dict[str, Any], source: str) -> Candidate | None:
    infos = page.get("imageinfo") or []
    if not infos:
        return None
    info = infos[0]
    image_url = str(info.get("url") or "").strip()
    if not image_url:
        return None
    return Candidate(
        source=source,
        source_url=str(info.get("descriptionurl") or image_url),
        image_url=image_url,
        thumb_url=str(info.get("thumburl") or image_url),
        title=str(page.get("title") or "").removeprefix("File:"),
        author=_meta(info, "Artist"),
        license_label=_meta(info, "LicenseShortName"),
        license_url=_meta(info, "LicenseUrl"),
        width=int(info["width"]) if info.get("width") else None,
        height=int(info["height"]) if info.get("height") else None,
        mime=info.get("mime"),
        description=(_meta(info, "ImageDescription") or "")[:300] or None,
    )


def _pages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    pages = (payload.get("query") or {}).get("pages") or {}
    values = pages.values() if isinstance(pages, dict) else pages
    return sorted((page for page in values if isinstance(page, dict)), key=lambda page: page.get("index", 0))


def from_commons_files(titles: list[str], source: str, http_get_json: HttpGetJsonFn) -> list[Candidate]:
    if not titles:
        return []
    payload = http_get_json(COMMONS_API, {"action": "query", "titles": "|".join(titles[:50]), **_IMAGEINFO})
    return [candidate for page in _pages(payload) if (candidate := candidate_from_commons_page(page, source))]


def from_commons_category(category: str, http_get_json: HttpGetJsonFn, pause: Callable[[], None]) -> list[Candidate]:
    def files_in(name: str, limit: int) -> list[Candidate]:
        payload = http_get_json(
            COMMONS_API,
            {
                "action": "query",
                "generator": "categorymembers",
                "gcmtitle": f"Category:{name}",
                "gcmtype": "file",
                "gcmlimit": str(limit),
                **_IMAGEINFO,
            },
        )
        return [c for page in _pages(payload) if (c := candidate_from_commons_page(page, "commons_category"))]

    found = files_in(category, MAX_CATEGORY_FILES)
    if len(found) >= SMALL_CATEGORY_FILES:
        return found
    pause()
    subcategories = http_get_json(
        COMMONS_API,
        {"action": "query", "list": "categorymembers", "cmtitle": f"Category:{category}", "cmtype": "subcat", "cmlimit": "5"},
    )
    for member in (subcategories.get("query") or {}).get("categorymembers") or []:
        name = str(member.get("title") or "").removeprefix("Category:")
        if name:
            pause()
            found.extend(files_in(name, 8))
    return found[:MAX_CATEGORY_FILES]


def from_commons_search(terms: list[str], http_get_json: HttpGetJsonFn, pause: Callable[[], None]) -> list[Candidate]:
    found: list[Candidate] = []
    for term in terms[:2]:
        payload = http_get_json(
            COMMONS_API,
            {
                "action": "query",
                "generator": "search",
                "gsrnamespace": "6",
                "gsrsearch": f"{term} filetype:bitmap",
                "gsrlimit": "10",
                **_IMAGEINFO,
            },
        )
        found.extend(c for page in _pages(payload) if (c := candidate_from_commons_page(page, "commons_search")))
        pause()
    return found


def from_wikipedia_lead(wikipedia_url: str, http_get_json: HttpGetJsonFn, pause: Callable[[], None]) -> list[Candidate]:
    title = urllib.parse.unquote(urllib.parse.urlparse(wikipedia_url).path.rsplit("/", 1)[-1]).replace("_", " ")
    if not title:
        return []
    payload = http_get_json(WIKIPEDIA_API, {"action": "query", "prop": "pageimages", "piprop": "name", "titles": title})
    names = [str(page.get("pageimage")) for page in _pages(payload) if page.get("pageimage")]
    if not names:
        return []
    pause()
    # A lead image held only on English Wikipedia (often non-free) is not on Commons and drops out here.
    return from_commons_files([f"File:{name}" for name in names], "wikipedia_lead", http_get_json)


def gather_candidates(
    view: TargetView, http_get_json: HttpGetJsonFn, pause: Callable[[], None]
) -> tuple[list[Candidate], list[str]]:
    """Candidates in source order, unique by image URL, plus any source errors."""
    steps: list[tuple[str, Callable[[], list[Candidate]]]] = []
    if view.wikidata_image:
        steps.append(("wikidata_image", lambda: from_commons_files([f"File:{view.wikidata_image}"], "wikidata_image", http_get_json)))
    if view.commons_category:
        steps.append(("commons_category", lambda: from_commons_category(view.commons_category, http_get_json, pause)))
    if view.search_terms:
        steps.append(("commons_search", lambda: from_commons_search(view.search_terms, http_get_json, pause)))
    if view.wikipedia_url:
        steps.append(("wikipedia_lead", lambda: from_wikipedia_lead(view.wikipedia_url, http_get_json, pause)))

    gathered: list[Candidate] = []
    seen: set[str] = set()
    errors: list[str] = []
    for name, step in steps:
        try:
            found = step()
        except Exception as exc:  # one failed source must not lose the others
            errors.append(f"{name}: {type(exc).__name__}")
            continue
        finally:
            pause()
        for candidate in found:
            if candidate.image_url not in seen and len(gathered) < MAX_GATHERED_PER_CONCEPT:
                seen.add(candidate.image_url)
                gathered.append(candidate)
    return gathered, errors


def _words(value: str) -> set[str]:
    words = set()
    for word in re.findall(r"[a-z]+", value.lower()):
        if len(word) > 4 and word.endswith("s"):
            word = word[:-1]
        if len(word) >= 3 and word not in _GENERIC_WORDS:
            words.add(word)
    return words


def distinctive_words(terms: list[str], commons_category: str | None) -> set[str]:
    """The search-term words that tell this concept apart.

    Words in a broader Commons category's name ("filter" for "Filters") match
    every file in it, so they do not count, unless nothing else is left: then
    the category is the concept itself ("Ball valves").
    """
    wanted = set().union(*(_words(term) for term in terms)) if terms else set()
    narrowed = wanted - _words(commons_category or "")
    return narrowed or wanted


def text_relevance(candidate: Candidate, words: set[str]) -> int:
    """Distinct distinctive words found in the file's title and description."""
    return len(words & _words(f"{candidate.title} {candidate.description or ''}"))


def prefilter_reasons(candidate: Candidate) -> list[str]:
    reasons: list[str] = []
    if (candidate.mime or "").lower() not in ACCEPTED_MIME:
        reasons.append("not_a_photo_format")
    if candidate.width and candidate.height:
        if min(candidate.width, candidate.height) < MIN_SIDE_PX:
            reasons.append("too_small")
        if max(candidate.width, candidate.height) / max(1, min(candidate.width, candidate.height)) > MAX_ASPECT:
            reasons.append("extreme_shape")
    if _TITLE_NOISE.search(candidate.title.lower()):
        reasons.append("title_names_a_drawing")
    return reasons


VISION_SYSTEM_PROMPT = (
    "You check candidate images for an engineering symbol catalogue. The catalogue wants a real photograph of a "
    "real example of the item a symbol stands for, to show users what the item looks like. You are given the "
    "item's name, definition and search terms, and one image. Judge only what the image shows. Reply with one "
    "JSON object only: {\"is_photograph\": true|false, \"shows_concept\": \"yes\"|\"partial\"|\"no\", "
    "\"depicts\": \"a few words\", \"is_diagram_or_symbol\": true|false, \"text_or_watermark_heavy\": true|false, "
    "\"quality\": 1-5, \"confidence\": 0.0-1.0, \"reason\": \"one sentence\"}. \"partial\" means the item is "
    "present but small, cut away, part of a larger installation, or a close relative of the item. Renders, "
    "drawings, catalogue cut-outs on white and screenshots are not photographs. The image's own caption is "
    "given too; it can help you tell what the item is, but it may be wrong, and only the image decides whether "
    "this is a photograph."
)


def vision_messages(
    view: TargetView, image: bytes, content_type: str, caption: str | None = None
) -> list[dict[str, Any]]:
    data_url = f"data:{content_type};base64,{base64.b64encode(image).decode('ascii')}"
    item = {"name": view.name, "definition": short_definition(view.definition), "search_terms": view.search_terms}
    return [
        {"role": "system", "content": VISION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": json.dumps({"item": item, "image_caption": caption or ""}, ensure_ascii=False)},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]


def parse_verdict(output_text: str) -> dict[str, Any]:
    raw = parse_first_json_object(output_text)

    def number(key: str, low: float, high: float) -> float | None:
        try:
            return max(low, min(high, float(raw.get(key))))
        except (TypeError, ValueError):
            return None

    shows = str(raw.get("shows_concept") or "").strip().lower()
    return {
        "is_photograph": raw.get("is_photograph") is True,
        "shows_concept": shows if shows in ("yes", "partial", "no") else "no",
        "depicts": str(raw.get("depicts") or "")[:200],
        "is_diagram_or_symbol": raw.get("is_diagram_or_symbol") is True,
        "text_or_watermark_heavy": raw.get("text_or_watermark_heavy") is True,
        "quality": number("quality", 1, 5),
        "confidence": number("confidence", 0, 1),
        "reason": str(raw.get("reason") or "")[:500],
    }


def score_verdict(verdict: dict[str, Any]) -> tuple[float | None, list[str]]:
    """A score in [0, 1], or None with the hard-reject reasons."""
    reasons = []
    if not verdict["is_photograph"]:
        reasons.append("vision:not_a_photograph")
    if verdict["is_diagram_or_symbol"]:
        reasons.append("vision:diagram_or_symbol")
    if verdict["shows_concept"] == "no":
        reasons.append("vision:does_not_show_the_item")
    if reasons:
        return None, reasons
    score = (verdict["confidence"] or 0.0) * ((verdict["quality"] or 1.0) / 5.0)
    if verdict["shows_concept"] == "partial":
        score *= 0.7
    if verdict["text_or_watermark_heavy"]:
        score *= 0.8
    return round(score, 4), []


def default_fetch_bytes(url: str) -> tuple[bytes, str]:
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        payload = response.read(MAX_IMAGE_BYTES + 1)
    if len(payload) > MAX_IMAGE_BYTES:
        raise ValueError("image too large")
    return payload, content_type


def check_candidates(
    view: TargetView,
    candidates: list[Candidate],
    *,
    fetch_bytes: FetchBytesFn,
    complete_vision: CompleteVisionFn,
    max_vision_calls: int,
    pause: Callable[[], None],
) -> dict[str, int]:
    """Pre-filter, then vision-check up to the per-concept and budget limits, in place."""
    counts = {"filtered": 0, "vision_calls": 0, "vision_errors": 0, "unchecked": 0}
    seen_digests: set[str] = set()
    allowance = min(MAX_VISION_CHECKS_PER_CONCEPT, max(0, max_vision_calls))
    for candidate in candidates:
        candidate.filter_reasons = prefilter_reasons(candidate)
        if candidate.filter_reasons:
            candidate.status = "filtered"
            counts["filtered"] += 1
    # The Wikidata image first, then the best text matches, then (a few) the rest.
    open_candidates = [candidate for candidate in candidates if candidate.status != "filtered"]
    words = distinctive_words(view.search_terms, view.commons_category)
    relevance = {id(candidate): text_relevance(candidate, words) for candidate in open_candidates}
    open_candidates.sort(
        key=lambda candidate: (candidate.source != "wikidata_image", -relevance[id(candidate)])
    )
    unmatched_checks = 0
    for candidate in open_candidates:
        if candidate.source != "wikidata_image" and relevance[id(candidate)] == 0:
            if unmatched_checks >= MAX_UNMATCHED_CHECKS:
                candidate.status, candidate.filter_reasons = "filtered", ["no_term_match"]
                counts["filtered"] += 1
                continue
            unmatched_checks += 1
        if counts["vision_calls"] >= allowance:
            counts["unchecked"] += 1
            continue
        try:
            image, content_type = fetch_bytes(candidate.thumb_url)
            pause()
        except Exception as exc:
            candidate.verdict = {"error": f"download failed: {type(exc).__name__}"}
            counts["vision_errors"] += 1
            continue
        if not content_type.startswith("image/"):
            candidate.status, candidate.filter_reasons = "filtered", ["not_an_image"]
            counts["filtered"] += 1
            continue
        candidate.sha256 = hashlib.sha256(image).hexdigest()
        if candidate.sha256 in seen_digests:
            candidate.status, candidate.filter_reasons = "filtered", ["duplicate_image"]
            counts["filtered"] += 1
            continue
        seen_digests.add(candidate.sha256)
        counts["vision_calls"] += 1
        try:
            caption = " — ".join(part for part in (candidate.title, candidate.description) if part)
            verdict = parse_verdict(complete_vision(vision_messages(view, image, content_type, caption)))
        except Exception as exc:  # an unreadable reply leaves the image for the next run
            candidate.verdict = {"error": f"vision failed: {type(exc).__name__}"}
            counts["vision_errors"] += 1
            continue
        candidate.verdict = verdict
        candidate.score, reasons = score_verdict(verdict)
        if reasons:
            candidate.status, candidate.filter_reasons = "filtered", reasons
            counts["filtered"] += 1
    return counts


def rank_and_propose(rows: list[dict[str, Any]]) -> None:
    """Mark the best open candidates `proposed`, in place.

    Rows are dicts with `status`, `score` and `rank`. Approved photos keep
    their slots; rejected, removed and filtered rows are never proposed.
    """
    approved = sum(1 for row in rows if row["status"] == "approved")
    slots = max(0, PHOTOS_PER_CONCEPT - approved)
    open_rows = [row for row in rows if row["status"] in ("candidate", "proposed") and row["score"] is not None]
    open_rows.sort(key=lambda row: row["score"], reverse=True)
    for index, row in enumerate(open_rows, start=1):
        row["rank"] = index
        row["status"] = "proposed" if index <= slots and row["score"] >= PROPOSE_MIN_SCORE else "candidate"


def target_view_from_row(session: Session, target: HannahConceptTarget) -> TargetView:
    row = session.execute(
        text(
            "SELECT c.concept_code, c.concept_kind, r.preferred_name, r.definition, r.aliases_json "
            "FROM semantic_concepts c JOIN semantic_concept_revisions r ON r.id = c.current_revision_id "
            "WHERE c.id = :id"
        ),
        {"id": target.semantic_concept_id},
    ).mappings().one()
    scope = ConceptScope(
        concept_id=str(target.semantic_concept_id),
        concept_code=row["concept_code"],
        concept_kind=row["concept_kind"],
        preferred_name=row["preferred_name"],
        definition=row["definition"],
        aliases=[str(alias) for alias in row["aliases_json"] or []],
        symbol_count=0,
    )
    return TargetView(
        concept_id=scope.concept_id,
        concept_code=scope.concept_code,
        name=readable_name(scope),
        definition=scope.definition,
        search_terms=list(target.search_terms_json or []),
        wikidata_image=(target.notes_json or {}).get("wikidata_image"),
        commons_category=target.commons_category,
        wikipedia_url=target.wikipedia_url,
        target=target,
    )


def vision_calls_in_last_day(session: Session, now: datetime) -> int:
    return int(
        session.execute(
            text("SELECT count(*) FROM llm_usage_events WHERE use_case = 'hannah_vision' AND occurred_at_utc >= :since"),
            {"since": now - timedelta(days=1)},
        ).scalar()
        or 0
    )


def curate_concept(
    session: Session,
    view: TargetView,
    *,
    apply: bool,
    http_get_json: HttpGetJsonFn,
    fetch_bytes: FetchBytesFn,
    complete_vision: CompleteVisionFn,
    vision_model: str,
    max_vision_calls: int,
    queue_item_id: str | None = None,
    pause: Callable[[], None] = lambda: time.sleep(1.0),
    now: datetime | None = None,
) -> dict[str, Any]:
    """One search for one concept. Writes candidates and the target state only with `apply`."""
    now = now or utc_now()
    existing: dict[str, HannahConceptPhoto] = {}
    if view.target is not None:
        existing = {
            row.image_url: row
            for row in session.query(HannahConceptPhoto).filter_by(semantic_concept_id=uuid.UUID(view.concept_id)).all()
        }

    gathered, source_errors = gather_candidates(view, http_get_json, pause)
    # An image already decided or checked is never looked at again; one that was
    # left unchecked (budget) or failed (download, unreadable reply) is retried.
    def needs_check(row: HannahConceptPhoto) -> bool:
        verdict = row.vision_verdict_json or {}
        return row.status == "candidate" and row.score is None and (not verdict or "error" in verdict)

    fresh = [
        candidate for candidate in gathered
        if candidate.image_url not in existing or needs_check(existing[candidate.image_url])
    ]
    counts = check_candidates(
        view, fresh, fetch_bytes=fetch_bytes, complete_vision=complete_vision,
        max_vision_calls=max_vision_calls, pause=pause,
    )

    rows: list[dict[str, Any]] = [
        {"key": url, "status": row.status, "score": float(row.score) if row.score is not None else None, "rank": row.rank}
        for url, row in existing.items()
    ]
    by_key = {row["key"]: row for row in rows}
    for candidate in fresh:
        row = by_key.get(candidate.image_url)
        if row is None:
            row = {"key": candidate.image_url, "rank": None}
            rows.append(row)
            by_key[candidate.image_url] = row
        row["status"], row["score"] = candidate.status, candidate.score
    rank_and_propose(rows)

    proposed = sum(1 for row in rows if row["status"] == "proposed")
    approved = sum(1 for row in rows if row["status"] == "approved")
    if source_errors and not gathered:
        target_status, next_eligible = "searched", now + ERROR_RETRY_AFTER
    elif proposed:
        # Steward work comes first, whatever is already approved.
        target_status, next_eligible = "has_proposals", None
    elif approved:
        target_status, next_eligible = "has_photos", (None if approved >= PHOTOS_PER_CONCEPT else now + SEARCHED_COOLDOWN)
    elif not any(row["score"] is not None for row in rows) and counts["unchecked"] == 0 and counts["vision_errors"] == 0:
        target_status, next_eligible = "no_candidates", now + NO_CANDIDATES_COOLDOWN
    else:
        target_status, next_eligible = "searched", now + (ERROR_RETRY_AFTER if counts["unchecked"] or counts["vision_errors"] else SEARCHED_COOLDOWN)

    report = {
        "concept_code": view.concept_code,
        "name": view.name,
        "search_terms": view.search_terms,
        "commons_category": view.commons_category,
        "gathered": len(gathered),
        "already_seen": len(gathered) - len(fresh),
        **counts,
        "source_errors": source_errors,
        "target_status": target_status,
        "proposed": proposed,
        "candidates": [
            {
                "source": candidate.source,
                "title": candidate.title,
                "source_url": candidate.source_url,
                "thumb_url": candidate.thumb_url,
                "author": candidate.author,
                "license": candidate.license_label,
                "status": by_key[candidate.image_url]["status"],
                "rank": by_key[candidate.image_url]["rank"],
                "score": candidate.score,
                "filter_reasons": candidate.filter_reasons,
                "depicts": candidate.verdict.get("depicts"),
                "reason": candidate.verdict.get("reason") or candidate.verdict.get("error"),
            }
            for candidate in fresh
        ],
        "applied": False,
    }
    if not apply:
        return report
    if view.target is None:
        raise RuntimeError("Applying a run needs a hannah_concept_targets row; run hannah-build-targets --apply first.")

    for candidate in fresh:
        row = existing.get(candidate.image_url)
        if row is None:
            row = HannahConceptPhoto(
                id=uuid.uuid4(),
                semantic_concept_id=uuid.UUID(view.concept_id),
                image_url=candidate.image_url,
                first_seen_at=now,
            )
            session.add(row)
            existing[candidate.image_url] = row
        row.source = candidate.source
        row.source_url = candidate.source_url
        row.title = candidate.title or None
        row.author = candidate.author
        row.license_label = candidate.license_label
        row.license_url = candidate.license_url
        row.width, row.height = candidate.width, candidate.height
        row.sha256 = candidate.sha256
        row.vision_model = vision_model if candidate.verdict and "error" not in candidate.verdict else None
        row.vision_verdict_json = candidate.verdict
        row.filter_reasons_json = candidate.filter_reasons
        row.queue_item_id = uuid.UUID(queue_item_id) if queue_item_id else None
        row.last_seen_at = now
    for url, ranked in by_key.items():
        row = existing[url]
        row.status, row.rank = ranked["status"], ranked["rank"]
        if ranked["score"] is not None:
            row.score = ranked["score"]

    target = view.target
    target.status = target_status
    target.attempt_count = (target.attempt_count or 0) + 1
    target.last_attempt_at = now
    target.next_eligible_at = next_eligible
    notes = dict(target.notes_json or {})
    if source_errors:
        notes["last_source_errors"] = source_errors
    else:
        notes.pop("last_source_errors", None)
    target.notes_json = notes
    target.updated_at = now
    session.commit()
    report["applied"] = True
    return report


def max_vision_calls_per_day() -> int:
    import os

    try:
        return max(0, int(os.environ.get("SYMGOV_HANNAH_MAX_VISION_CALLS_PER_DAY", DEFAULT_MAX_VISION_CALLS_PER_DAY)))
    except ValueError:
        return DEFAULT_MAX_VISION_CALLS_PER_DAY


def make_vision_completer(model: str, *, initiator_kind: str, session_factory: Any) -> CompleteVisionFn:
    """The OpenRouter vision call, recorded in the usage ledger as `hannah_vision`."""
    from .services.llm_router import request_llm_completion

    def complete(messages: list[dict[str, Any]]) -> str:
        return request_llm_completion(
            model=model,
            provider="openrouter",
            messages=messages,
            request_kind="vision",
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=1500,
            timeout=90,
            use_case="hannah_vision",
            service_name="symgov-api",
            agent_slug="hannah",
            feature="hannah_vision",
            initiator_kind=initiator_kind,
            session_factory_provider=lambda: session_factory,
        )["outputText"]

    return complete
