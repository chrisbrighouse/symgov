#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import mimetypes
import sys
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if not BACKEND_ROOT.exists():
    BACKEND_ROOT = Path("/data/symgov/backend")
if not BACKEND_ROOT.exists():
    BACKEND_ROOT = Path("/data/.openclaw/workspace/symgov/backend")
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text

from symgov_backend.runtime import RuntimePersistenceBridge


SCHEMA_VERSION = "hannah-curation-v1"
PROMPT_VERSION = "hannah-curation-2026-05-19"
DEFAULT_DURATION_SECONDS = 120
MAX_DURATION_SECONDS = 300
MAX_PHOTOS_PER_SYMBOL = 2
PLACEHOLDER_VALUES = {"", "unknown", "tbd", "todo", "pending", "uncategorized", "general", "n/a", "na", "none"}
LOW_RISK_LICENSE_MARKERS = ("cc0", "public domain", "cc by", "cc-by", "creative commons attribution")
BAD_CANDIDATE_MARKERS = (
    ".pdf",
    ".djvu",
    "document",
    "cover",
    "manual",
    "thesis",
    "report",
    "war crimes",
    "russian military",
    "president of ukraine",
    "zelensky",
    "terrorism",
    "political speech",
    "news conference",
    "address by the president",
)
GENERIC_SYMBOL_TOKENS = {
    "symbol",
    "symbols",
    "general",
    "process",
    "instrumentation",
    "mechanical",
    "piping",
    "position",
    "normally",
    "closed",
    "open",
    "fail",
    "common",
    "equipment",
    "photograph",
    "photo",
    "real",
}
EQUIPMENT_PHOTO_MARKERS = (
    "equipment",
    "industrial",
    "plant",
    "factory",
    "installed",
    "installation",
    "machinery",
    "machine",
    "valve",
    "pump",
    "motor",
    "pipe",
    "piping",
    "instrument",
    "photograph",
    "photo",
)
AUTO_ATTACH_MIN_SCORE = 0.72


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stamp_id(prefix: str, seed: str) -> str:
    digest = hashlib.sha1(f"{prefix}:{seed}:{utc_now()}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def add_trace(trace: list[dict[str, str]], step: str, status: str, detail: str) -> None:
    trace.append({"step": step, "status": status, "detail": detail, "recorded_at": utc_now()})


def reasonable_value(value: str | None) -> bool:
    normalized = " ".join(str(value or "").strip().lower().split())
    if normalized in PLACEHOLDER_VALUES:
        return False
    return len(normalized) >= 3


def symbol_is_eligible(symbol: dict[str, Any]) -> tuple[bool, list[str]]:
    missing = []
    # Loosened: require name and at least one of category/discipline
    if not reasonable_value(symbol.get("name")):
        missing.append("name")
    
    cat = symbol.get("category")
    disc = symbol.get("discipline")
    if not reasonable_value(cat) and not reasonable_value(disc):
        missing.append("category_or_discipline")

    if reasonable_value(cat) and reasonable_value(disc) and str(cat).strip().lower() == str(disc).strip().lower():
        # If they are identical and it's a generic term, it might be low quality, but we'll allow it for now
        pass

    return not missing, missing


def load_eligible_symbols(db_env_file: str | None, limit: int = 200) -> list[dict[str, Any]]:
    bridge = RuntimePersistenceBridge(env_file=db_env_file)
    with bridge.session_scope() as session:
        rows = session.execute(
            text(
                """
                SELECT
                    gs.id::text AS symbol_id,
                    gs.slug AS symbol_slug,
                    gs.canonical_name AS name,
                    gs.category,
                    gs.discipline,
                    sr.id::text AS symbol_revision_id,
                    pp.id::text AS published_page_id,
                    pp.title AS page_title,
                    COALESCE(hs.attempt_count, 0) AS attempt_count,
                    hs.last_attempt_at,
                    count(hp.id) FILTER (WHERE hp.status = 'attached' AND hp.object_key IS NOT NULL) AS photo_count
                FROM published_pages pp
                JOIN publication_packs pk ON pk.id = pp.pack_id
                JOIN symbol_revisions sr ON sr.id = pp.current_symbol_revision_id
                JOIN governed_symbols gs ON gs.id = sr.symbol_id
                LEFT JOIN hannah_symbol_curation_states hs ON hs.symbol_id = gs.id
                LEFT JOIN hannah_photo_candidates hp ON hp.symbol_id = gs.id
                WHERE pk.status = 'published'
                    AND pk.audience = 'public'
                    AND sr.lifecycle_state = 'published'
                GROUP BY gs.id, gs.slug, gs.canonical_name, gs.category, gs.discipline, sr.id, pp.id, pp.title, hs.attempt_count, hs.last_attempt_at
                HAVING count(hp.id) FILTER (WHERE hp.status = 'attached' AND hp.object_key IS NOT NULL) < :max_photos
                ORDER BY hs.last_attempt_at NULLS FIRST, COALESCE(hs.attempt_count, 0), gs.canonical_name
                LIMIT :limit
                """
            ),
            {"max_photos": MAX_PHOTOS_PER_SYMBOL, "limit": limit},
        ).mappings().all()
    return [dict(row) for row in rows]


def commons_api(params: dict[str, str], timeout: int = 18) -> dict[str, Any]:
    query = urllib.parse.urlencode({"format": "json", **params})
    request = urllib.request.Request(
        f"https://commons.wikimedia.org/w/api.php?{query}",
        headers={"User-Agent": "Symgov-Hannah/0.1 catalogue-curation"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def search_commons_images(symbol: dict[str, Any], trace: list[dict[str, str]]) -> list[dict[str, Any]]:
    query = " ".join(
        part
        for part in (
            symbol.get("name"),
            symbol.get("category"),
            symbol.get("discipline"),
            "real equipment photograph -filetype:pdf -cover -document",
        )
        if part
    )
    payload = commons_api(
        {
            "action": "query",
            "generator": "search",
            "gsrnamespace": "6",
            "gsrlimit": "8",
            "gsrsearch": query,
            "prop": "imageinfo",
            "iiprop": "url|mime|extmetadata",
            "iiurlwidth": "512",
        }
    )
    pages = (payload.get("query") or {}).get("pages") or {}
    add_trace(trace, "commons_search", "passed", f"{query}: {len(pages)} candidate images.")
    return [page for page in pages.values() if isinstance(page, dict)]


def search_duckduckgo_images(symbol: dict[str, Any], trace: list[dict[str, str]]) -> list[dict[str, Any]]:
    query = " ".join(
        part
        for part in (
            symbol.get("name"),
            symbol.get("category"),
            symbol.get("discipline"),
            "equipment photograph",
        )
        if part
    )
    add_trace(trace, "duckduckgo_search", "started", f"Query: {query}")
    
    # Simple DuckDuckGo 'Lite' search scraper using stdlib
    try:
        params = urllib.parse.urlencode({"q": query, "kl": "wt-wt"})
        url = f"https://lite.duckduckgo.com/lite/?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Symgov-Hannah/0.1)"})
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8")
        
        # Very crude link extraction from DDG Lite
        # Lite results usually look like <a rel="nofollow" href="...">
        import re
        links = re.findall(r'href="([^"]+)"', html)
        candidates = []
        for link in links:
            if any(ext in link.lower() for ext in (".jpg", ".jpeg", ".png")):
                # In Lite mode, we might get direct image links or page links
                candidates.append({"url": link, "title": "DDG Result"})
        
        add_trace(trace, "duckduckgo_search", "passed", f"Found {len(candidates)} potential links.")
        
        # Construct basic candidate objects if we found anything
        results = []
        for c in candidates[:5]:
             results.append({
                 "imageinfo": [{"url": c["url"], "descriptionshorturl": c["url"]}],
                 "title": c["title"],
                 "pageid": f"ddg-{hashlib.md5(c['url'].encode()).hexdigest()[:8]}",
                 "extmetadata": {
                     "LicenseShortName": {"value": "Needs Review (DDG)"},
                     "UsageTerms": {"value": "Check source for license details"}
                 }
             })
        return results

    except Exception as exc:
        add_trace(trace, "duckduckgo_search", "failed", str(exc))
        return []


def search_images(symbol: dict[str, Any], trace: list[dict[str, str]]) -> list[dict[str, Any]]:
    try:
        # Throttling to avoid 429s
        time.sleep(1.5)
        results = search_commons_images(symbol, trace)
        if results:
            return results
    except Exception as exc:
        add_trace(trace, "commons_search", "failed", f"{symbol.get('name')}: {exc}")

    try:
        time.sleep(1.0)
        results = search_duckduckgo_images(symbol, trace)
        if results:
            return results
    except Exception as exc:
        add_trace(trace, "duckduckgo_search", "failed", f"{symbol.get('name')}: {exc}")

    return []


def license_status(extmetadata: dict[str, Any]) -> tuple[str, str | None]:
    license_short = ((extmetadata.get("LicenseShortName") or {}).get("value") or "").strip()
    usage_terms = ((extmetadata.get("UsageTerms") or {}).get("value") or "").strip()
    combined = f"{license_short} {usage_terms}".lower()
    if any(marker in combined for marker in LOW_RISK_LICENSE_MARKERS):
        return "low_risk", license_short or usage_terms or "permissive"
    return "needs_review", license_short or usage_terms or None


def candidate_text(page: dict[str, Any]) -> str:
    image_info = (page.get("imageinfo") or [{}])[0]
    extmetadata = image_info.get("extmetadata") or {}
    metadata_values = " ".join(
        str((value or {}).get("value") or "")
        for value in extmetadata.values()
        if isinstance(value, dict)
    )
    return " ".join(
        str(part or "")
        for part in (
            page.get("title"),
            page.get("description"),
            image_info.get("url"),
            image_info.get("thumburl"),
            image_info.get("descriptionurl"),
            image_info.get("descriptionshorturl"),
            image_info.get("mime"),
            metadata_values,
        )
    ).lower()


def meaningful_symbol_terms(symbol: dict[str, Any]) -> set[str]:
    import re

    terms: set[str] = set()
    for field in ("name", "category"):
        value = str(symbol.get(field) or "").lower()
        for token in re.findall(r"[a-z0-9]+", value):
            normalized = token[:-1] if len(token) > 4 and token.endswith("s") else token
            if len(normalized) >= 3 and normalized not in GENERIC_SYMBOL_TOKENS:
                terms.add(normalized)
    return terms


def candidate_quality_reasons(symbol: dict[str, Any], page: dict[str, Any]) -> list[str]:
    haystack = candidate_text(page)
    image_info = (page.get("imageinfo") or [{}])[0]
    image_url = str(image_info.get("thumburl") or image_info.get("url") or "")
    source_url = str(image_info.get("descriptionurl") or image_info.get("descriptionshorturl") or image_url)
    source_domain = urllib.parse.urlparse("https:" + source_url if source_url.startswith("//") else source_url).netloc.lower()
    title = str(page.get("title") or "").strip().lower()
    reasons: list[str] = []

    if source_domain.endswith("duckduckgo.com") and ("/assets/icons/" in image_url.lower() or title == "ddg result"):
        reasons.append("duckduckgo_asset_or_generic_result")
    if any(marker in haystack for marker in BAD_CANDIDATE_MARKERS):
        reasons.append("blocked_noise_marker")
    if not any(ext in image_url.lower().split("?", 1)[0] for ext in (".jpg", ".jpeg", ".png", ".webp")):
        reasons.append("not_direct_raster_image")

    terms = meaningful_symbol_terms(symbol)
    matched_terms = {term for term in terms if term in haystack}
    if not matched_terms:
        reasons.append("no_meaningful_symbol_match")
    if not any(marker in haystack for marker in EQUIPMENT_PHOTO_MARKERS):
        reasons.append("no_equipment_photo_context")
    return reasons


def candidate_is_quality_acceptable(symbol: dict[str, Any], page: dict[str, Any]) -> bool:
    return not candidate_quality_reasons(symbol, page)


def candidate_is_auto_attachable(candidate: dict[str, Any]) -> bool:
    return (
        candidate.get("rights_status") == "low_risk"
        and float(candidate.get("relevance_score") or 0) >= AUTO_ATTACH_MIN_SCORE
        and not candidate.get("quality_reasons")
    )


def score_candidate(symbol: dict[str, Any], page: dict[str, Any], rights_status: str) -> float:
    haystack = candidate_text(page)

    if any(m in haystack for m in BAD_CANDIDATE_MARKERS):
        return 0.05
    score = 0.25
    terms = meaningful_symbol_terms(symbol)
    matched_terms = {term for term in terms if term in haystack}
    score += min(0.30, 0.15 * len(matched_terms))
    for field in ("name", "category"):
        value = str(symbol.get(field) or "").strip().lower()
        if value and value in haystack:
            score += 0.16
    if any(marker in haystack for marker in EQUIPMENT_PHOTO_MARKERS):
        score += 0.15
    if "photograph" in haystack or "photo" in haystack:
        score += 0.1
    if rights_status == "low_risk":
        score += 0.12

    # Small DDG boost only after quality checks have removed DDG chrome/icons.
    if "ddg-" in str(page.get("pageid")) and matched_terms:
        score += 0.05

    return min(score, 0.99)


def download_image(url: str, timeout: int = 20) -> tuple[bytes, str]:
    if url.startswith("//"):
        url = "https:" + url
    request = urllib.request.Request(url, headers={"User-Agent": "Symgov-Hannah/0.1 catalogue-curation"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
        content_type = response.headers.get("Content-Type") or "application/octet-stream"
    return payload, content_type.split(";", 1)[0].strip()


def attach_candidate_photo(
    bridge: RuntimePersistenceBridge,
    *,
    symbol: dict[str, Any],
    candidate_id: str,
    image_url: str,
    content_url: str,
    storage_env_file: str | None,
) -> dict[str, str] | None:
    if not storage_env_file:
        return None
    payload, content_type = download_image(image_url)
    if not content_type.startswith("image/"):
        return None
    extension = mimetypes.guess_extension(content_type) or ".jpg"
    object_key = f"hannah/supplemental-photos/{symbol['symbol_slug']}/{candidate_id}{extension}"
    upload = bridge.upload_object_bytes(
        object_key=object_key,
        payload=payload,
        content_type=content_type,
        env_file=storage_env_file,
    )
    attachment = bridge.create_attachment(
        parent_type="symbol_revision",
        parent_id=symbol["symbol_revision_id"],
        filename=Path(urllib.parse.urlparse(content_url).path).name or f"{candidate_id}{extension}",
        object_key=object_key,
        content_type=content_type,
        size_bytes=upload["size_bytes"],
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    return {"attachment_id": attachment["id"], "object_key": object_key}


def build_candidate(symbol: dict[str, Any], page: dict[str, Any], trace: list[dict[str, str]]) -> dict[str, Any] | None:
    image_info = (page.get("imageinfo") or [{}])[0]
    image_url = image_info.get("thumburl") or image_info.get("url")
    source_url = image_info.get("descriptionurl") or image_info.get("descriptionshorturl") or image_url
    if not image_url or not source_url:
        return None
    quality_reasons = candidate_quality_reasons(symbol, page)
    if quality_reasons:
        add_trace(
            trace,
            "candidate_quality_filter",
            "rejected",
            f"{symbol.get('name')}: {page.get('title') or image_url}: {', '.join(quality_reasons)}",
        )
        return None
    extmetadata = image_info.get("extmetadata") or {}
    rights_status, license_label = license_status(extmetadata)
    score = score_candidate(symbol, page, rights_status)
    title = str(page.get("title") or "").replace("File:", "", 1)
    candidate_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"hannah:{symbol['symbol_id']}:{image_url}"))
    return {
        "id": candidate_id,
        "symbol_id": symbol["symbol_id"],
        "symbol_revision_id": symbol["symbol_revision_id"],
        "published_page_id": symbol["published_page_id"],
        "source_url": source_url,
        "image_url": image_url,
        "source_domain": urllib.parse.urlparse(source_url).netloc or "commons.wikimedia.org",
        "title": title[:300],
        "description": ((extmetadata.get("ImageDescription") or {}).get("value") or title)[:1000],
        "rights_status": rights_status,
        "license_label": license_label,
        "status": "candidate",
        "relevance_score": round(score, 4),
        "quality_reasons": [],
        "evidence": {
            "source": "Wikimedia Commons API",
            "commons_page_id": page.get("pageid"),
            "mime": image_info.get("mime"),
            "matched_symbol": symbol.get("name"),
        },
    }


def run_curation_task(task: dict[str, Any], db_env_file: str | None = None, storage_env_file: str | None = None) -> dict[str, Any]:
    started_monotonic = time.monotonic()
    duration_seconds = max(30, min(int(task.get("duration_seconds") or DEFAULT_DURATION_SECONDS), MAX_DURATION_SECONDS))
    deadline = started_monotonic + duration_seconds
    trace: list[dict[str, str]] = []
    symbol_attempts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    bridge = RuntimePersistenceBridge(env_file=db_env_file) if db_env_file else None
    symbols = load_eligible_symbols(db_env_file)

    for symbol in symbols:
        if time.monotonic() >= deadline:
            break
        eligible, missing = symbol_is_eligible(symbol)
        attached_for_symbol = 0
        if not eligible:
            symbol_attempts.append(
                {
                    "symbol_id": symbol["symbol_id"],
                    "status": "not_eligible",
                    "photo_count": int(symbol.get("photo_count") or 0),
                    "notes": {"missing_or_unreasonable": missing},
                }
            )
            continue

        try:
            pages = search_images(symbol, trace)
        except Exception as exc:
            add_trace(trace, "symbol_search", "failed", f"{symbol.get('name')}: {exc}")
            symbol_attempts.append(
                {
                    "symbol_id": symbol["symbol_id"],
                    "status": "search_failed",
                    "photo_count": int(symbol.get("photo_count") or 0),
                    "notes": {"error": str(exc)},
                }
            )
            continue

        current_photo_count = int(symbol.get("photo_count") or 0)
        remaining_slots = max(0, MAX_PHOTOS_PER_SYMBOL - current_photo_count)
        for page in pages:
            if time.monotonic() >= deadline:
                break
            candidate = build_candidate(symbol, page, trace)
            if candidate is None:
                continue
            if candidate_is_auto_attachable(candidate) and remaining_slots > 0 and bridge is not None:
                try:
                    attachment = attach_candidate_photo(
                        bridge,
                        symbol=symbol,
                        candidate_id=candidate["id"],
                        image_url=candidate["image_url"],
                        content_url=candidate["source_url"],
                        storage_env_file=storage_env_file,
                    )
                    if attachment:
                        candidate.update(attachment)
                        candidate["status"] = "attached"
                        attached_for_symbol += 1
                        remaining_slots -= 1
                except Exception as exc:
                    candidate["status"] = "candidate"
                    candidate["evidence"]["attachment_error"] = str(exc)
            candidates.append(candidate)

        symbol_attempts.append(
            {
                "symbol_id": symbol["symbol_id"],
                "status": "photos_attached" if attached_for_symbol else "candidates_recorded",
                "photo_count": current_photo_count + attached_for_symbol,
                "notes": {"candidate_count": len([item for item in candidates if item["symbol_id"] == symbol["symbol_id"]])},
            }
        )

    elapsed_seconds = int(time.monotonic() - started_monotonic)
    return {
        "queue_item_id": task.get("queue_item_id") or "untracked",
        "agent": "hannah",
        "schema_version": SCHEMA_VERSION,
        "task_type": "published_symbol_photo_search",
        "decision": "progress_saved",
        "duration_seconds": duration_seconds,
        "elapsed_seconds": elapsed_seconds,
        "symbols_considered": len(symbol_attempts),
        "candidate_count": len(candidates),
        "attached_count": len([candidate for candidate in candidates if candidate.get("status") == "attached"]),
        "symbol_attempts": symbol_attempts,
        "candidates": candidates,
        "evidence_trace": trace,
    }


def queue_item_payload_to_task(queue_item: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(queue_item.get("payload_json") or {})
    payload["queue_item_id"] = queue_item.get("id")
    payload["source_type"] = queue_item.get("source_type")
    payload["source_id"] = queue_item.get("source_id")
    payload["priority"] = queue_item.get("priority")
    return payload


def process_queue_item(queue_item_path: str | Path, runtime_root: str | Path, persist_db: bool = False, db_env_file: str | None = None, storage_env_file: str | None = None) -> dict[str, Any]:
    queue_item_path = Path(queue_item_path)
    runtime_root = Path(runtime_root)
    queue_item = load_json(queue_item_path)
    if queue_item.get("agent_id") != "hannah":
        raise ValueError("Queue item agent_id must be 'hannah'.")

    started_at = utc_now()
    queue_item["status"] = "searching"
    queue_item["started_at"] = started_at
    write_json(queue_item_path, queue_item)

    artifact = run_curation_task(queue_item_payload_to_task(queue_item), db_env_file=db_env_file, storage_env_file=storage_env_file)
    completed_at = utc_now()
    queue_item["status"] = "progress_saved"
    queue_item["confidence"] = None
    queue_item["escalation_reason"] = None
    queue_item["payload_json"] = {
        **(queue_item.get("payload_json") or {}),
        "symbols_considered": artifact["symbols_considered"],
        "candidate_count": artifact["candidate_count"],
        "attached_count": artifact["attached_count"],
        "elapsed_seconds": artifact["elapsed_seconds"],
    }
    queue_item["completed_at"] = completed_at
    write_json(queue_item_path, queue_item)

    run_id = stamp_id("arun", queue_item["id"])
    run_record = {
        "id": run_id,
        "queue_item_id": queue_item["id"],
        "model": "ollama/gemma4:e4b",
        "prompt_version": PROMPT_VERSION,
        "tool_trace_json": artifact["evidence_trace"],
        "result_status": queue_item["status"],
        "started_at": started_at,
        "completed_at": completed_at,
    }

    artifact_id = stamp_id("aout", queue_item["id"])
    output_artifact_record = {
        "id": artifact_id,
        "queue_item_id": queue_item["id"],
        "artifact_type": "hannah_curation_report",
        "schema_version": artifact["schema_version"],
        "payload_json": artifact,
        "created_at": completed_at,
    }

    record_id = stamp_id("hc", queue_item["id"])
    durable_record = {
        "id": record_id,
        "queue_item_id": queue_item["id"],
        "symbol_attempts": artifact["symbol_attempts"],
        "candidates": artifact["candidates"],
        "report_json": artifact,
        "completed_at": completed_at,
    }

    write_json(runtime_root / "agent_runs" / f"{run_id}.json", run_record)
    write_json(runtime_root / "agent_output_artifacts" / f"{artifact_id}.json", output_artifact_record)
    durable_record_path = runtime_root / "curation_reports" / f"{record_id}.json"
    write_json(durable_record_path, durable_record)

    db_persistence = None
    if persist_db:
        bridge = RuntimePersistenceBridge(env_file=db_env_file)
        db_persistence = bridge.persist_agent_execution(
            queue_item=queue_item,
            run_record=run_record,
            output_artifact_record=output_artifact_record,
            durable_record=durable_record,
            durable_kind="hannah_curation_report",
        )

    return {
        "queue_item_id": queue_item["id"],
        "queue_item_status": queue_item["status"],
        "curation_report_path": str(durable_record_path),
        "db_persistence": db_persistence,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Hannah catalogue curation.")
    parser.add_argument("--queue-item", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--persist-db", action="store_true")
    parser.add_argument("--db-env-file")
    parser.add_argument("--storage-env-file")
    args = parser.parse_args()
    result = process_queue_item(
        args.queue_item,
        args.runtime_root,
        persist_db=args.persist_db,
        db_env_file=args.db_env_file,
        storage_env_file=args.storage_env_file,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
