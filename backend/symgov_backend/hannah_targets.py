"""Build Hannah's concept targets: what to photograph, and how to search for it.

WP-H2 of docs/plans/2026-10-19-hannah-concept-photo-curation-plan.md. For each
semantic concept that a public Catalog symbol is assigned to, Hannah records:

- whether the concept can be photographed. `physical_equipment` is yes and the
  abstract kinds are no, straight from `concept_kind`; the model decides the
  rest, because the Commons import filed most of its physical things under
  `other` (Chris, 2026-10-19, Q1). A steward's decision is never overwritten;
- one to three plain English search terms. Preferred names are CamelCase codes
  (`SeparatorCycloneType`), so the model writes them from the definition and
  aliases, with a deterministic fallback when it cannot;
- the Wikidata item, Commons category and English Wikipedia page, picked by the
  model from Wikidata's own search results and stored on Hannah's row only
  (Q3), never in `concept_external_references`.

Both outside calls are injected (`complete`, `http_get_json`), so tests run
without the network or a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
import time
from typing import Any, Callable
import urllib.parse
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from .models.schema import HannahConceptTarget


FIXED_KIND_DECISIONS = {
    "physical_equipment": "yes",
    "function": "no",
    "property": "no",
    "state": "no",
    "action": "no",
    "annotation": "no",
    "connection": "no",
    "safety_function": "no",
}
PHOTOGRAPHABLE_VALUES = ("yes", "no", "unsure")
CLASSIFY_BATCH_SIZE = 15
MAX_SEARCH_TERMS = 3
MAX_WIKIDATA_CANDIDATES = 12
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
USER_AGENT = "Symgov-Hannah/0.2 (https://symgov.org; catalogue photo curation)"
# Import boilerplate that follows the useful part of a definition.
_DEFINITION_TAILS = (
    ". Its Wikimedia Commons description",
    ". Taken from the DISC",
    ". Seeded from the DEXPI",
    ". Created from the curated",
)
_QID = re.compile(r"^Q[1-9][0-9]*$")
# `SeparatorCycloneType` -> `Separator Cyclone Type`; `YGlobeValve` -> `Y Globe Valve`.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
# Words, spaces and light punctuation only: "Separator, cyclone type", "Y-GLOBE VALVE", "Burner".
_READABLE_ALIAS = re.compile(r"^[A-Za-z][A-Za-z ,()'/-]*$")

CompleteFn = Callable[[list[dict[str, Any]]], str]
HttpGetJsonFn = Callable[[str, dict[str, str]], dict[str, Any]]


@dataclass
class ConceptScope:
    concept_id: str
    concept_code: str
    concept_kind: str
    preferred_name: str
    definition: str
    aliases: list[str]
    symbol_count: int
    decision: dict[str, Any] = field(default_factory=dict)
    link: dict[str, Any] = field(default_factory=dict)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def load_concept_scope(session: Session) -> list[ConceptScope]:
    """Active concepts with a verified primary assignment on a public Catalog symbol."""
    rows = session.execute(
        text(
            """
            SELECT c.id::text AS concept_id, c.concept_code, c.concept_kind,
                   r.preferred_name, r.definition, r.aliases_json,
                   count(DISTINCT app.governed_symbol_id) AS symbol_count
            FROM active_public_symbol_projections app
            JOIN symbol_semantic_assignments a
              ON a.symbol_revision_id = app.symbol_revision_id
             AND a.assignment_role = 'primary'
             AND a.status = 'verified'
            JOIN semantic_concepts c ON c.id = a.semantic_concept_id AND c.status = 'active'
            JOIN semantic_concept_revisions r ON r.id = c.current_revision_id
            WHERE app.visibility = 'public'
              AND app.owner_organization_id IS NULL
            GROUP BY c.id, c.concept_code, c.concept_kind, r.preferred_name, r.definition, r.aliases_json
            ORDER BY c.concept_code
            """
        )
    ).mappings()
    return [
        ConceptScope(
            concept_id=row["concept_id"],
            concept_code=row["concept_code"],
            concept_kind=row["concept_kind"],
            preferred_name=row["preferred_name"],
            definition=row["definition"],
            aliases=[str(alias) for alias in (row["aliases_json"] or []) if str(alias).strip()],
            symbol_count=int(row["symbol_count"]),
        )
        for row in rows
    ]


def short_definition(definition: str, limit: int = 400) -> str:
    cut = definition
    for tail in _DEFINITION_TAILS:
        index = cut.find(tail)
        if index > 0:
            cut = cut[:index + 1]
    cut = " ".join(cut.split())
    return cut if len(cut) <= limit else cut[: limit - 1].rstrip() + "…"


def _plain_words(value: str) -> str:
    words = re.sub(r"[^A-Za-z0-9]+", " ", value).split()
    return " ".join(words).lower()


def readable_aliases(scope: ConceptScope) -> list[str]:
    """Aliases made of words; DEXPI imports also carry codes such as `X807128`,
    `@30|M00|A50`, `511` or `BLIND_COVER_SHAPE`."""
    return [alias for alias in scope.aliases if _READABLE_ALIAS.match(alias.strip())]


def readable_name(scope: ConceptScope) -> str:
    aliases = readable_aliases(scope)
    return aliases[0] if aliases else _CAMEL_BOUNDARY.sub(" ", scope.preferred_name)


def fallback_search_terms(scope: ConceptScope) -> list[str]:
    """Plain search terms without a model: the readable aliases, then the split preferred name."""
    candidates = [_plain_words(alias) for alias in readable_aliases(scope)]
    candidates.append(_plain_words(_CAMEL_BOUNDARY.sub(" ", scope.preferred_name)))
    terms: list[str] = []
    for term in candidates:
        if len(term) >= 3 and term not in terms:
            terms.append(term)
    return terms[:MAX_SEARCH_TERMS]


def parse_first_json_object(output_text: str) -> dict[str, Any]:
    """The first JSON object in a reply; models sometimes add a second or a preamble."""
    start = output_text.find("{")
    if start < 0:
        raise ValueError("The reply holds no JSON object.")
    decoded, _end = json.JSONDecoder().raw_decode(output_text[start:])
    if not isinstance(decoded, dict):
        raise ValueError("The reply's first JSON value is not an object.")
    return decoded


def _clean_terms(values: Any) -> list[str]:
    terms: list[str] = []
    for value in values if isinstance(values, list) else []:
        term = " ".join(str(value).split()).strip().lower()
        if 3 <= len(term) <= 80 and term not in terms:
            terms.append(term)
    return terms[:MAX_SEARCH_TERMS]


CLASSIFY_SYSTEM_PROMPT = (
    "You help an engineering symbol catalogue find real-world photographs of what its symbols represent. "
    "For each concept you are given, decide whether it is a physical object that a photographer could take a "
    "picture of (a valve, pump, vessel, instrument, fitting, machine) or not (a signal, function, logic, "
    "annotation, line type, state, property). Answer \"yes\", \"no\" or \"unsure\". Then give one to three "
    "plain English search terms a person would type to find a photograph of a real example, most common name "
    "first, lower case, no symbol or drawing words. Reply with one JSON object only: "
    "{\"concepts\": [{\"code\": \"...\", \"photographable\": \"yes|no|unsure\", \"search_terms\": [\"...\"]}]}"
)


def classify_concepts(scopes: list[ConceptScope], complete: CompleteFn) -> dict[str, dict[str, Any]]:
    """Ask the model, in batches, for the photographable answer and search terms.

    A batch whose reply cannot be read is reported per concept, never raised, so
    one bad reply does not stop the run.
    """
    results: dict[str, dict[str, Any]] = {}
    for start in range(0, len(scopes), CLASSIFY_BATCH_SIZE):
        batch = scopes[start:start + CLASSIFY_BATCH_SIZE]
        items = [
            {
                "code": scope.concept_code,
                "name": readable_name(scope),
                "kind": scope.concept_kind,
                "definition": short_definition(scope.definition),
            }
            for scope in batch
        ]
        messages = [
            {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"concepts": items}, ensure_ascii=False)},
        ]
        try:
            reply = parse_first_json_object(complete(messages))
            answers = {
                str(item.get("code")): item
                for item in reply.get("concepts") or []
                if isinstance(item, dict)
            }
        except Exception as exc:  # one unreadable reply must not stop the run
            for scope in batch:
                results[scope.concept_code] = {"error": f"classification failed: {type(exc).__name__}"}
            continue
        for scope in batch:
            answer = answers.get(scope.concept_code)
            if answer is None:
                results[scope.concept_code] = {"error": "classification reply omitted this concept"}
                continue
            photographable = str(answer.get("photographable") or "").strip().lower()
            results[scope.concept_code] = {
                "photographable": photographable if photographable in PHOTOGRAPHABLE_VALUES else "unsure",
                "search_terms": _clean_terms(answer.get("search_terms")),
            }
    return results


def default_http_get_json(url: str, params: dict[str, str]) -> dict[str, Any]:
    import urllib.request

    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode({**params, 'format': 'json'})}",
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def wikidata_queries(terms: list[str]) -> list[str]:
    """The first two terms, then their head nouns.

    Wikidata rarely has an item for a specific type ("anchor agitator",
    "volume control damper") but usually has one for the kind of thing
    ("damper", "humidifier"), which the model may pick when nothing closer
    exists (measured on the 2026-10-20 trial: 4 of 18 linked without them).
    """
    queries = list(terms[:2])
    for term in terms[:2]:
        head = term.split()[-1] if term.split() else ""
        if len(head) >= 4 and head not in queries:
            queries.append(head)
    return queries


def search_wikidata(terms: list[str], http_get_json: HttpGetJsonFn) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for term in wikidata_queries(terms):
        payload = http_get_json(
            WIKIDATA_API,
            {"action": "wbsearchentities", "search": term, "language": "en", "uselang": "en", "type": "item", "limit": "5"},
        )
        for hit in payload.get("search") or []:
            qid = str(hit.get("id") or "")
            if _QID.match(qid) and qid not in seen:
                seen.add(qid)
                found.append({"qid": qid, "label": str(hit.get("label") or ""), "description": str(hit.get("description") or "")})
    return found[:MAX_WIKIDATA_CANDIDATES]


PICK_SYSTEM_PROMPT = (
    "You match an engineering concept to the Wikidata item for the same kind of physical object. You are given "
    "the concept and a list of Wikidata search results. Pick the item that is the same kind of thing (not a "
    "company, product range, scholarly article, process or a broader category unless nothing closer exists). "
    "If none fits, answer null. Reply with one JSON object only: {\"qid\": \"Q...\" or null, \"confidence\": 0.0-1.0}"
)


def pick_wikidata_item(
    scope: ConceptScope, terms: list[str], candidates: list[dict[str, str]], complete: CompleteFn
) -> tuple[str | None, float | None]:
    if not candidates:
        return None, None
    messages = [
        {"role": "system", "content": PICK_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "concept": {
                        "name": readable_name(scope),
                        "search_terms": terms,
                        "definition": short_definition(scope.definition),
                    },
                    "candidates": candidates,
                },
                ensure_ascii=False,
            ),
        },
    ]
    reply = parse_first_json_object(complete(messages))
    qid = reply.get("qid")
    if not isinstance(qid, str) or qid not in {candidate["qid"] for candidate in candidates}:
        return None, None
    try:
        confidence = max(0.0, min(1.0, float(reply.get("confidence"))))
    except (TypeError, ValueError):
        confidence = None
    return qid, confidence


def wikidata_details(qid: str, http_get_json: HttpGetJsonFn) -> dict[str, str | None]:
    payload = http_get_json(
        WIKIDATA_API,
        {"action": "wbgetentities", "ids": qid, "props": "claims|sitelinks", "sitefilter": "enwiki"},
    )
    entity = (payload.get("entities") or {}).get(qid) or {}
    claims = entity.get("claims") or {}

    def first_value(prop: str) -> str | None:
        for statement in claims.get(prop) or []:
            value = ((statement.get("mainsnak") or {}).get("datavalue") or {}).get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    enwiki = ((entity.get("sitelinks") or {}).get("enwiki") or {}).get("title")
    return {
        "image": first_value("P18"),
        "commons_category": first_value("P373"),
        "wikipedia_url": (
            "https://en.wikipedia.org/wiki/" + urllib.parse.quote(enwiki.replace(" ", "_")) if enwiki else None
        ),
    }


def link_concept(
    scope: ConceptScope,
    terms: list[str],
    *,
    complete: CompleteFn,
    http_get_json: HttpGetJsonFn,
    pause: Callable[[], None],
) -> dict[str, Any]:
    try:
        candidates = search_wikidata(terms, http_get_json)
        pause()
        qid, confidence = pick_wikidata_item(scope, terms, candidates, complete)
        if qid is None:
            return {"qid": None, "candidates": len(candidates)}
        details = wikidata_details(qid, http_get_json)
        pause()
        return {"qid": qid, "confidence": confidence, "candidates": len(candidates), **details}
    except Exception as exc:  # one failed lookup must not stop the run
        return {"error": f"wikidata link failed: {type(exc).__name__}"}


def build_targets(
    session: Session,
    *,
    apply: bool,
    complete: CompleteFn | None,
    http_get_json: HttpGetJsonFn | None,
    limit: int | None = None,
    reclassify: bool = False,
    relink: bool = False,
    kind: str | None = None,
    concept_codes: list[str] | None = None,
    pause: Callable[[], None] = lambda: time.sleep(0.5),
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create or refresh Hannah's targets. Dry run unless `apply`.

    `complete=None` skips the model (fixed kinds only, fallback terms, no
    links); `http_get_json=None` skips Wikidata. Existing decisions are kept
    unless `reclassify`; existing links unless `relink`. A steward's decision
    and search terms are never replaced.
    """
    now = now or utc_now()
    scopes = load_concept_scope(session)
    # Before migration 20261020_0074 is deployed a dry run can still preview
    # the targets, as if none existed yet.
    has_table = session.execute(text("SELECT to_regclass('hannah_concept_targets') IS NOT NULL")).scalar()
    if not has_table and apply:
        raise RuntimeError("hannah_concept_targets does not exist; run migration 20261020_0074 first.")
    existing = (
        {str(row.semantic_concept_id): row for row in session.query(HannahConceptTarget).all()}
        if has_table
        else {}
    )

    work: list[ConceptScope] = []
    for scope in scopes:
        target = existing.get(scope.concept_id)
        needs_decision = target is None or (
            target.photographable_basis != "steward" and (reclassify or target.photographable is None)
        )
        needs_link = (target is None or target.wikidata_qid is None or relink) and not (
            target is not None and target.photographable == "no"
        )
        if target is not None and not relink and (target.notes_json or {}).get("wikidata_checked_at"):
            needs_link = False
        if needs_decision or needs_link:
            scope.decision = {"needs_decision": needs_decision, "needs_link": needs_link}
            work.append(scope)
    if kind is not None:
        work = [scope for scope in work if scope.concept_kind == kind]
    if concept_codes is not None:
        wanted = set(concept_codes)
        work = [scope for scope in work if scope.concept_code in wanted]
    if limit is not None:
        work = work[:limit]

    # Fixed "yes" kinds still go to the model, for their search terms; fixed
    # "no" kinds never need either.
    to_classify = [
        scope for scope in work
        if scope.decision["needs_decision"] and FIXED_KIND_DECISIONS.get(scope.concept_kind) != "no"
    ]
    model_answers = classify_concepts(to_classify, complete) if complete is not None and to_classify else {}

    summary: dict[str, Any] = {
        "applied": False,
        "in_scope": len(scopes),
        "worked": len(work),
        "photographable": {"yes": 0, "no": 0, "unsure": 0, "undecided": 0},
        "basis": {"concept_kind": 0, "llm": 0, "steward": 0},
        "linked": 0,
        "unlinked": 0,
        "errors": [],
        "concepts": [],
    }

    for scope in work:
        target = existing.get(scope.concept_id)
        answer = model_answers.get(scope.concept_code, {})
        if answer.get("error"):
            summary["errors"].append({"concept_code": scope.concept_code, "error": answer["error"]})

        photographable = target.photographable if target is not None else None
        basis = target.photographable_basis if target is not None else None
        terms = list(target.search_terms_json or []) if target is not None else []
        if scope.decision["needs_decision"]:
            fixed = FIXED_KIND_DECISIONS.get(scope.concept_kind)
            if fixed is not None:
                photographable, basis = fixed, "concept_kind"
            elif answer.get("photographable"):
                photographable, basis = answer["photographable"], "llm"
            else:
                photographable, basis = None, None
            if basis != "steward":
                terms = answer.get("search_terms") or fallback_search_terms(scope)
        if not terms:
            terms = fallback_search_terms(scope)

        link: dict[str, Any] = {}
        if (
            scope.decision["needs_link"]
            and photographable in ("yes", "unsure")
            and complete is not None
            and http_get_json is not None
        ):
            link = link_concept(scope, terms, complete=complete, http_get_json=http_get_json, pause=pause)
            if link.get("error"):
                summary["errors"].append({"concept_code": scope.concept_code, "error": link["error"]})

        summary["photographable"][photographable or "undecided"] += 1
        if basis:
            summary["basis"][basis] += 1
        if link.get("qid"):
            summary["linked"] += 1
        elif link and not link.get("error"):
            summary["unlinked"] += 1
        summary["concepts"].append(
            {
                "concept_code": scope.concept_code,
                "name": readable_name(scope),
                "kind": scope.concept_kind,
                "symbols": scope.symbol_count,
                "photographable": photographable,
                "basis": basis,
                "search_terms": terms,
                "wikidata_qid": link.get("qid"),
                "commons_category": link.get("commons_category"),
                "wikidata_image": link.get("image"),
                "wikipedia_url": link.get("wikipedia_url"),
                "concept_id": scope.concept_id,
                "definition": scope.definition,
            }
        )

        if not apply:
            continue
        if target is None:
            target = HannahConceptTarget(
                id=uuid.uuid4(),
                semantic_concept_id=uuid.UUID(scope.concept_id),
                status="pending",
                attempt_count=0,
                notes_json={},
                created_at=now,
                updated_at=now,
            )
            session.add(target)
        target.photographable = photographable
        target.photographable_basis = basis
        target.search_terms_json = terms
        if photographable == "no":
            target.status = "excluded"
        elif target.status == "excluded":
            target.status = "pending"
        notes = dict(target.notes_json or {})
        if link and not link.get("error"):
            target.wikidata_qid = link.get("qid")
            target.commons_category = link.get("commons_category")
            target.wikipedia_url = link.get("wikipedia_url")
            target.link_confidence = link.get("confidence")
            notes["wikidata_image"] = link.get("image")
            notes["wikidata_checked_at"] = now.isoformat()
        if answer.get("error") or link.get("error"):
            notes["last_error"] = answer.get("error") or link.get("error")
        else:
            notes.pop("last_error", None)
        target.notes_json = notes
        target.updated_at = now

    if apply:
        session.commit()
        summary["applied"] = True
    return summary


def make_text_completer(model: str, *, initiator_kind: str, session_factory: Any) -> CompleteFn:
    """The OpenRouter text call, recorded in the usage ledger as `hannah_targets`."""
    from .services.llm_router import request_llm_completion

    def complete(messages: list[dict[str, Any]]) -> str:
        return request_llm_completion(
            model=model,
            provider="openrouter",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=2000,
            timeout=90,
            use_case="hannah_targets",
            service_name="symgov-api",
            agent_slug="hannah",
            feature="hannah_targets",
            initiator_kind=initiator_kind,
            session_factory_provider=lambda: session_factory,
        )["outputText"]

    return complete
