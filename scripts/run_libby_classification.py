#!/usr/bin/env python3
import argparse
import base64
import copy
import json
import mimetypes
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = "0.1.0"
PROMPT_VERSION = "libby-local-contract-0.1.0"
BACKEND_ROOT = Path(os.environ.get("SYMGOV_BACKEND_ROOT", "/data/symgov/backend"))

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.filename_inference import infer_filename_metadata
from symgov_backend.runtime import RuntimePersistenceBridge, env_flag, download_object_bytes
from symgov_backend.notifications import send_agent_status_update


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stamp_id(prefix, base_id):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{base_id}-{timestamp}"


def add_trace(trace, check, status, detail):
    trace.append({"check": check, "status": status, "detail": detail})


def add_defect(defects, code, severity, detail):
    defects.append({"code": code, "severity": severity, "detail": detail})


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def send_libby_status_update(phase, queue_item, artifact=None, queue_status=None):
    if os.environ.get("LIBBY_SKIP_NOTIFICATIONS", "").lower() in {"1", "true", "yes", "on"}:
        return {"ok": False, "skipped": True, "reason": "LIBBY_SKIP_NOTIFICATIONS"}
    return send_agent_status_update("libby", phase, queue_item, artifact=artifact, queue_status=queue_status)


def cleanup_queue_item(queue_item_path, runtime_root):
    queue_path = Path(queue_item_path).resolve()
    queue_dir = (Path(runtime_root).resolve() / "agent_queue_items").resolve()

    if queue_dir not in queue_path.parents:
        raise SystemExit(f"Refusing to remove queue item outside {queue_dir}.")
    if queue_path.suffix != ".json":
        raise SystemExit("Refusing to remove a non-JSON queue item.")
    if not queue_path.exists():
        return {
            "queue_item_path": str(queue_path),
            "removed": False,
            "message": "Queue item was already absent.",
        }

    queue_path.unlink()
    return {
        "queue_item_path": str(queue_path),
        "removed": True,
        "message": "Queue item removed from Libby runtime queue.",
    }


def queue_status_for_decision(decision):
    if decision == "escalate":
        return "escalated"
    return "completed"


def ensure_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


SYMBOL_PROPERTY_PROMPT = (
    "please review this engineering symbol image and tell me what it represents, "
    "returning a clear property structure for name, description, category and discipline.  "
    "The category and discipline should be short saved-list words or phrases"
)


def parse_json_object_from_text(text):
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("LLM symbol property response must be a JSON object.")
    return payload


def parse_gemini_symbol_property_response(response):
    for candidate in response.get("candidates") or []:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            if part.get("text"):
                payload = parse_json_object_from_text(part["text"])
                return {
                    "name": str(payload.get("name") or "").strip(),
                    "description": str(payload.get("description") or "").strip(),
                    "category": str(payload.get("category") or "").strip(),
                    "discipline": str(payload.get("discipline") or "").strip(),
                }
    raise ValueError("Gemini response did not contain a text JSON property structure.")


def compact_property_text(value, limit):
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def apply_symbol_property_description(artifact, properties):
    clean = {
        "name": compact_property_text(properties.get("name"), 80),
        "description": compact_property_text(properties.get("description"), 500),
        "category": compact_property_text(properties.get("category"), 80),
        "discipline": compact_property_text(properties.get("discipline"), 80),
    }
    if clean["category"]:
        artifact["category"] = clean["category"]
    if clean["discipline"]:
        artifact["discipline"] = clean["discipline"]
    if clean["name"]:
        artifact["symbol_name"] = clean["name"]
        if clean["name"] not in artifact.get("aliases", []):
            artifact.setdefault("aliases", []).insert(0, clean["name"])
    if clean["description"]:
        artifact["classification_summary"] = clean["description"]
        artifact["review_summary"] = clean["description"]
    artifact.setdefault("evidence", {})["llm_symbol_properties"] = clean
    add_trace(
        artifact.setdefault("evidence_trace", []),
        "llm_symbol_property_review",
        "passed",
        "Libby used a vision LLM property review for name, description, category, and discipline.",
    )
    artifact["confidence"] = max(float(artifact.get("confidence") or 0), 0.74)
    return artifact


def resolve_symbol_image_bytes(task, storage_env_file=None):
    for key in ("asset_path", "image_path", "local_image_path"):
        value = task.get(key)
        if value and Path(value).exists():
            path = Path(value)
            return path.read_bytes(), mimetypes.guess_type(path.name)[0] or "image/png", str(path)

    object_key = (
        task.get("attachment_object_key")
        or task.get("object_key")
        or task.get("origin_object_key")
        or task.get("candidate_object_key")
    )
    if object_key and storage_env_file:
        downloaded = download_object_bytes(object_key=object_key, env_file=storage_env_file)
        content_type = downloaded.get("content_type") or "image/png"
        return downloaded["payload"], content_type, object_key
    return None, None, None


def call_gemini_symbol_property_review(image_bytes, content_type, category_options=None, discipline_options=None, filename_hints=None):
    api_key = os.environ.get("SYMGOV_GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("SYMGOV_GEMINI_API_KEY is not configured.")
    model = os.environ.get("SYMGOV_GEMINI_IMAGE_MODEL") or "gemini-1.5-flash"
    prompt = SYMBOL_PROPERTY_PROMPT
    if category_options or discipline_options:
        prompt = (
            f"{prompt}\n\nUse these saved category values when there is an exact or very close match: "
            f"{', '.join(category_options or []) or 'none'}.\n"
            f"Use these saved discipline values when there is an exact or very close match: "
            f"{', '.join(discipline_options or []) or 'none'}.\n"
            "If there is no exact or very close saved value, propose a short new saved-list phrase. "
            "Return JSON only with keys name, description, category, discipline."
        )
    if filename_hints:
        prompt = (
            f"{prompt}\n\nFilename hints (advisory only; reconcile them with the image and override them if the image clearly contradicts them):\n"
            f"- original filename: {filename_hints.get('original_filename') or 'unknown'}\n"
            f"- inferred name: {filename_hints.get('inferred_name') or 'unknown'}\n"
            f"- inferred discipline: {filename_hints.get('discipline_hint') or 'unknown'}\n"
            f"- hint confidence: {filename_hints.get('confidence') or 0}\n"
            "Prefer a compact engineering name when the filename looks intentional, preserve useful compounds like FireAlarm or BreakGlass, "
            "and use the inferred wording as the description only when the image does not support a better description."
        )
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent?key={urllib.parse.quote(api_key)}"
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": content_type or "image/png",
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        }
                    },
                ],
            }
        ],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return parse_gemini_symbol_property_response(json.loads(response.read().decode("utf-8")))


def enrich_classification_with_symbol_image(artifact, task, db_env_file=None, storage_env_file=None):
    if os.environ.get("LIBBY_DISABLE_SYMBOL_VISION", "").lower() in {"1", "true", "yes", "on"}:
        return artifact
    image_bytes, content_type, image_ref = resolve_symbol_image_bytes(task, storage_env_file=storage_env_file)
    if not image_bytes:
        add_trace(artifact.setdefault("evidence_trace", []), "llm_symbol_property_review", "skipped", "No local or stored symbol image was available for Libby's vision review.")
        return artifact
    bridge = RuntimePersistenceBridge(env_file=db_env_file) if db_env_file else None
    category_options = []
    discipline_options = []
    if bridge is not None:
        options = bridge.list_review_symbol_property_options()
        category_options = options.get("category") or []
        discipline_options = options.get("discipline") or []
    try:
        properties = call_gemini_symbol_property_review(
            image_bytes,
            content_type,
            category_options=category_options,
            discipline_options=discipline_options,
            filename_hints=artifact.get("evidence", {}).get("filename_inference") or filename_hints_for_task(task),
        )
        if bridge is not None:
            properties["category"] = bridge.remember_review_symbol_property_option(field_name="category", value=properties.get("category"))
            properties["discipline"] = bridge.remember_review_symbol_property_option(field_name="discipline", value=properties.get("discipline"))
        artifact.setdefault("evidence", {})["llm_symbol_image_ref"] = image_ref
        return apply_symbol_property_description(artifact, properties)
    except Exception as exc:
        add_trace(artifact.setdefault("evidence_trace", []), "llm_symbol_property_review", "warning", f"Vision LLM property review failed; heuristic classification retained: {exc}")
        return artifact


def queue_status(runtime_root):
    queue_dir = Path(runtime_root) / "agent_queue_items"
    counts = {}
    oldest_created_at = None
    oldest_queue_item_id = None
    items = []

    for queue_path in sorted(queue_dir.glob("*.json")):
        try:
            with queue_path.open("r", encoding="utf-8") as handle:
                queue_item = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            items.append({"path": str(queue_path), "status": "unreadable", "error": str(exc)})
            counts["unreadable"] = counts.get("unreadable", 0) + 1
            continue

        status = queue_item.get("status") or "unknown"
        counts[status] = counts.get(status, 0) + 1
        created_at = queue_item.get("created_at")
        if created_at and (oldest_created_at is None or created_at < oldest_created_at):
            oldest_created_at = created_at
            oldest_queue_item_id = queue_item.get("id")
        items.append(
            {
                "id": queue_item.get("id"),
                "status": status,
                "source_type": queue_item.get("source_type"),
                "source_id": queue_item.get("source_id"),
                "created_at": created_at,
            }
        )

    return {
        "agent": "libby",
        "queue_dir": str(queue_dir),
        "total": len(items),
        "counts_by_status": counts,
        "oldest_item": {
            "id": oldest_queue_item_id,
            "created_at": oldest_created_at,
        },
        "items": items,
    }


def is_batch_payload(payload):
    return isinstance(payload, dict) and (
        isinstance(payload.get("items"), list) or isinstance(payload.get("cases"), list)
    )


def batch_items_from_payload(payload):
    items = payload.get("items")
    if not isinstance(items, list):
        items = payload.get("cases")
    if not isinstance(items, list) or not items:
        raise ValueError("Batch Libby queue payload must include a non-empty items or cases list.")
    return items


def safe_token(value, fallback="item"):
    token = str(value or fallback).strip().lower()
    token = "".join(char if char.isalnum() else "-" for char in token)
    token = "-".join(part for part in token.split("-") if part)
    return (token or fallback)[:24]


def queue_item_payload_to_task(queue_item):
    payload = copy.deepcopy(queue_item.get("payload_json") or {})
    payload["queue_item_id"] = queue_item.get("id")
    payload["source_type"] = queue_item.get("source_type")
    payload["source_id"] = queue_item.get("source_id")
    payload["priority"] = queue_item.get("priority")
    return payload


def normalize_label(value):
    if not value:
        return None
    return str(value).strip().replace("_", " ").replace("-", " ")


def title_case_label(value):
    normalized = normalize_label(value)
    if not normalized:
        return None
    return " ".join(part.capitalize() for part in normalized.split())


def filename_hints_for_task(task):
    original_filename = task.get("origin_file_name") or task.get("original_filename") or task.get("candidate_title")
    provided = task.get("filename_inference") if isinstance(task.get("filename_inference"), dict) else None
    inferred = copy.deepcopy(provided) if provided else infer_filename_metadata(original_filename)
    inferred["original_filename"] = original_filename
    return inferred


def is_likely_single_symbol_task(task, filename_hints):
    grouping = str(task.get("package_symbol_grouping") or "").strip().lower()
    if grouping == "paired_dxf_raster_symbol":
        return True
    if str(task.get("task_type") or "").strip().lower() == "symbol_graphic_change_request":
        return True
    confidence = float(filename_hints.get("confidence") or 0)
    evidence = set(filename_hints.get("evidence") or [])
    if "generic_token" in evidence and confidence < 0.7:
        return False
    return confidence >= 0.6


def infer_classification(task):
    candidate_symbol_id = task.get("candidate_symbol_id") or task.get("symbol_key") or "UNSPECIFIED"
    origin_file_name = task.get("origin_file_name") or task.get("original_filename") or "Submitted file"
    declared_format = (task.get("asset_format") or task.get("declared_format") or "").lower() or None
    rights_status = (task.get("rights_status") or "unknown").lower()
    source_refs = ensure_list(task.get("source_refs"))
    ocr_labels = [normalize_label(item) for item in ensure_list(task.get("ocr_labels")) if normalize_label(item)]
    contributor_declaration = normalize_label(task.get("contributor_declaration"))
    source_notes = normalize_label(task.get("source_notes"))
    file_note = normalize_label(task.get("file_note"))
    submission_batch_summary = normalize_label(task.get("submission_batch_summary"))
    candidate_title = normalize_label(task.get("candidate_title"))
    filename_inference = filename_hints_for_task(task)
    file_stub = str(filename_inference.get("raw_stem") or Path(origin_file_name).stem)
    file_terms = [str(term) for term in filename_inference.get("display_tokens") or [] if str(term).strip()]
    normalized_file_stub = normalize_label(file_stub)
    if not file_terms and normalized_file_stub:
        file_terms = [term for term in normalized_file_stub.split() if term]
    likely_single_symbol = is_likely_single_symbol_task(task, filename_inference)

    defects = []
    evidence_trace = []
    evidence = {
        "candidate_symbol_id": candidate_symbol_id,
        "origin_file_name": origin_file_name,
        "candidate_title": candidate_title,
        "filename_inference": filename_inference,
        "source_refs": source_refs,
        "ocr_labels": ocr_labels,
        "contributor_declaration": contributor_declaration,
        "source_notes": source_notes,
        "file_note": file_note,
        "submission_batch_summary": submission_batch_summary,
        "web_research_used": False,
    }

    aliases = []
    search_terms = []
    for value in (
        candidate_symbol_id,
        candidate_title,
        filename_inference.get("inferred_name"),
        file_stub,
        *ocr_labels,
    ):
        label = normalize_label(value)
        if label and label not in aliases:
            aliases.append(label)
    for term in [*aliases, *file_terms, *(filename_inference.get("display_tokens") or [])]:
        normalized_term = normalize_label(term)
        if normalized_term and normalized_term not in search_terms:
            search_terms.append(normalized_term)
    if contributor_declaration:
        for term in contributor_declaration.split():
            if term not in search_terms and len(term) > 2:
                search_terms.append(term)
    for note in (source_notes, file_note, submission_batch_summary):
        if not note:
            continue
        for term in note.split():
            if term not in search_terms and len(term) > 2:
                search_terms.append(term)

    discipline = "instrumentation"
    category = "unclassified_symbol"
    symbol_family = "general_symbol"
    process_category = "unknown_process"
    parent_equipment_class = "unknown_equipment"
    industry = "general_industry"
    standards_source = source_refs[0] if source_refs else None
    library_provenance_class = "internet_research_candidate" if rights_status == "unknown" else "contributor_submission"
    source_classification = "unknown"
    classification_status = "provisional"
    libby_approved = False
    taxonomy_terms_created = []
    confidence = 0.58
    escalation_target = "human_reviewer"
    decision = "escalate"
    symbol_name = None
    description_fallback = None

    add_trace(evidence_trace, "seed_context", "passed", "Loaded file, symbol, and provenance context for Libby classification.")

    if declared_format:
        add_trace(evidence_trace, "format_detection", "passed", f"Detected submitted format {declared_format}.")
    else:
        add_trace(evidence_trace, "format_detection", "warning", "No explicit submitted format was available.")

    if likely_single_symbol and filename_inference.get("inferred_name"):
        symbol_name = str(filename_inference.get("inferred_name") or "").strip() or None
        if symbol_name:
            description_fallback = str(filename_inference.get("description_fallback") or symbol_name)
            confidence = max(confidence, float(filename_inference.get("confidence") or 0))
            add_trace(evidence_trace, "filename_name_inference", "passed", "Libby promoted filename inference to first-class symbol naming evidence.")
    else:
        add_trace(evidence_trace, "filename_name_inference", "skipped", "Filename evidence was not strong enough to promote to a primary symbol name.")

    filename_discipline = str(filename_inference.get("discipline_hint") or "").strip()
    if likely_single_symbol and filename_discipline and float(filename_inference.get("confidence") or 0) >= 0.8:
        discipline = filename_discipline
        confidence = max(confidence, float(filename_inference.get("confidence") or 0))
        add_trace(evidence_trace, "filename_discipline_inference", "passed", f"Libby inferred discipline {filename_discipline} from the filename.")
    elif filename_discipline:
        add_trace(evidence_trace, "filename_discipline_inference", "skipped", "Filename discipline hint was available but not strong enough to override broader heuristics.")

    if source_refs:
        source_classification = "standards_derived"
        add_trace(evidence_trace, "source_refs", "passed", f"Libby received {len(source_refs)} source reference(s) from upstream.")
        confidence = max(confidence, 0.76)
    else:
        add_defect(defects, "LIBBY-SOURCE-001", "medium", "No upstream source references were available for classification.")
        add_trace(evidence_trace, "source_refs", "failed", "No upstream source references were available.")

    joined_labels = " ".join(
        [
            candidate_symbol_id,
            file_stub,
            candidate_title or "",
            symbol_name or "",
            contributor_declaration or "",
            source_notes or "",
            submission_batch_summary or "",
        ]
        + ocr_labels
    ).lower()
    if "mechanical" in joined_labels:
        category = "symbol_sheet"
        symbol_family = "mixed_symbol_set"
        process_category = "review_required"
        parent_equipment_class = "mixed_equipment"
        discipline = "Mechanical" if filename_discipline == "Mechanical" else "mechanical"
        industry = "mechanical_engineering"
        confidence = max(confidence, 0.75)
        add_trace(evidence_trace, "taxonomy_match", "passed", "Detected mechanical classification cues from filename/notes.")
    elif "valve" in joined_labels:
        category = "valve_symbol"
        symbol_family = "valve"
        process_category = "flow_control"
        parent_equipment_class = "valve"
        if not filename_discipline:
            discipline = "process_instrumentation"
        industry = "process_engineering"
        confidence = max(confidence, 0.84)
        add_trace(evidence_trace, "taxonomy_match", "passed", "Detected valve-related classification cues.")
    elif "symbol" in joined_labels and not symbol_name:
        category = "symbol_sheet"
        symbol_family = "mixed_symbol_set"
        process_category = "review_required"
        parent_equipment_class = "mixed_equipment"
        if not filename_discipline:
            discipline = "process_instrumentation"
        industry = "process_engineering"
        confidence = max(confidence, 0.69)
        add_trace(evidence_trace, "taxonomy_match", "passed", "Detected generic symbol-sheet classification cues.")
    else:
        taxonomy_terms_created.append(
            {
                "term_type": "symbol_family",
                "term_value": f"candidate:{candidate_symbol_id.lower()}",
                "reason": "No stable existing family match was found in the first local Libby heuristic pass.",
            }
        )
        symbol_family = f"candidate:{candidate_symbol_id.lower()}"
        add_trace(evidence_trace, "taxonomy_match", "warning", "No strong family match found; Libby created a provisional taxonomy term.")

    if rights_status == "cleared":
        source_classification = "contributor_asserted" if source_classification == "unknown" else source_classification
        confidence = max(confidence, 0.8)
    elif rights_status in {"restricted", "conflict"}:
        add_defect(defects, "LIBBY-RIGHTS-001", "high", "Classification depends on a provenance record that still has rights risk.")
        add_trace(evidence_trace, "rights_status", "warning", f"Upstream rights status remained {rights_status}.")
        confidence = min(confidence, 0.62)
    else:
        evidence["web_research_used"] = True
        source_classification = "internet_inferred" if source_classification == "unknown" else source_classification
        add_trace(evidence_trace, "web_research_policy", "passed", "Marked the record as eligible for internet-assisted classification follow-up.")

    if confidence >= 0.82 and source_classification != "unknown":
        decision = "pass"
        escalation_target = "none"
        classification_status = "classified"
        libby_approved = True
    elif confidence >= 0.7:
        classification_status = "classified"

    summary = description_fallback or (
        f"Libby classified {candidate_symbol_id} from {origin_file_name} as {title_case_label(symbol_family) or symbol_family} "
        f"within {title_case_label(discipline) or discipline}."
    )

    artifact = {
        "queue_item_id": task.get("queue_item_id") or "untracked",
        "agent": "libby",
        "schema_version": SCHEMA_VERSION,
        "decision": decision,
        "confidence": round(confidence, 2),
        "escalation_target": escalation_target,
        "classification_summary": summary,
        "classification_status": classification_status,
        "category": category,
        "discipline": discipline,
        "format": declared_format,
        "industry": industry,
        "symbol_family": symbol_family,
        "process_category": process_category,
        "parent_equipment_class": parent_equipment_class,
        "standards_source": standards_source,
        "library_provenance_class": library_provenance_class,
        "source_classification": source_classification,
        "aliases": aliases,
        "search_terms": search_terms,
        "source_refs": source_refs,
        "taxonomy_terms_created": taxonomy_terms_created,
        "libby_approved": libby_approved,
        "evidence": evidence,
        "defects": defects,
        "evidence_trace": evidence_trace,
        "review_summary": summary,
    }
    if symbol_name:
        artifact["symbol_name"] = symbol_name
    return artifact


def review_text_indicates_graphic_change(task):
    values = [task.get("decision_note"), task.get("case_comment")]
    for child in ensure_list(task.get("child_decisions")):
        if isinstance(child, dict):
            values.extend([child.get("note"), child.get("details"), child.get("action")])
    text = " ".join(str(value or "") for value in values).lower()
    graphic_terms = (
        "graphic",
        "drawing",
        "image",
        "symbol shape",
        "crop",
        "line",
        "rotate",
        "resize",
        "redraw",
        "edit symbol",
        "physical change",
        "text",
        "label",
        "lettering",
        "annotation",
        "remove text",
        "erase text",
    )
    return any(term in text for term in graphic_terms)


def build_daisy_queue_item(task, artifact, timestamp):
    review_case_id = task.get("review_case_id")
    item_token = safe_token(
        task.get("item_id")
        or task.get("child_id")
        or task.get("review_decision_id")
        or artifact.get("review_decision_id")
        or review_case_id
        or artifact["queue_item_id"]
    )
    queue_id = f"aqi-daisy-libby-{item_token}-{timestamp}"
    return {
        "id": queue_id,
        "agent_id": "daisy",
        "source_type": "libby_follow_up",
        "source_id": task.get("review_decision_id") or review_case_id,
        "status": "queued",
        "priority": task.get("priority") or "medium",
        "payload_json": {
            "review_case_id": review_case_id,
            "current_stage": "libby_follow_up_complete",
            "source_entity_type": task.get("source_entity_type"),
            "source_entity_id": task.get("source_entity_id"),
            "escalation_level": "medium",
            "validation_status": "pending_review",
            "rights_status": "pending_review",
            "reviewer_pool": ["methods_lead", "qa_admin"],
            "libby_follow_up_report": artifact,
            "prior_review_decision_id": task.get("review_decision_id"),
            "returned_split_items": artifact.get("returned_split_items") or [],
        },
        "confidence": None,
        "escalation_reason": None,
        "created_at": utc_now(),
        "started_at": None,
        "completed_at": None,
    }


def build_vlad_queue_item(task, artifact, timestamp):
    review_case_id = task.get("review_case_id")
    classification = task.get("classification") if isinstance(task.get("classification"), dict) else {}
    item_token = safe_token(
        task.get("item_id")
        or task.get("child_id")
        or task.get("review_decision_id")
        or artifact.get("review_decision_id")
        or review_case_id
        or artifact["queue_item_id"]
    )
    queue_id = f"aqi-vlad-libby-graphic-{item_token}-{timestamp}"
    return {
        "id": queue_id,
        "agent_id": "vlad",
        "source_type": "libby_graphic_change_request",
        "source_id": task.get("review_decision_id") or review_case_id,
        "status": "queued",
        "priority": task.get("priority") or "medium",
        "payload_json": {
            "task_type": "symbol_graphic_change_request",
            "review_case_id": review_case_id,
            "review_decision_id": task.get("review_decision_id"),
            "libby_queue_item_id": task.get("queue_item_id"),
            "origin_object_key": task.get("origin_object_key"),
            "origin_file_name": task.get("origin_file_name"),
            "asset_path": task.get("asset_path"),
            "asset_format": task.get("asset_format") or classification.get("format"),
            "candidate_symbol_id": task.get("candidate_symbol_id"),
            "candidate_symbol_name": task.get("candidate_symbol_name"),
            "requested_changes": {
                "decision_code": task.get("decision_code"),
                "decision_note": task.get("decision_note"),
                "case_comment": task.get("case_comment"),
                "child_decisions": ensure_list(task.get("child_decisions")),
                "libby_summary": artifact["follow_up_summary"],
            },
            "return_to_agent": "libby",
            "next_review_agent": "daisy",
        },
        "confidence": None,
        "escalation_reason": None,
        "created_at": utc_now(),
        "started_at": None,
        "completed_at": None,
    }


def run_review_follow_up_task(task):
    queue_item_id = task.get("queue_item_id") or "untracked"
    follow_up_type = task.get("libby_follow_up_type") or "review_follow_up"
    child_decisions = ensure_list(task.get("child_decisions"))
    non_graphic_follow_up_types = {
        "deletion_or_rejection",
        "duplicate_resolution",
        "metadata_or_classification_update",
        "evidence_request",
        "deferral",
    }
    if follow_up_type == "graphic_change_triage":
        needs_graphic_change = True
    elif follow_up_type in non_graphic_follow_up_types:
        needs_graphic_change = False
    else:
        needs_graphic_change = review_text_indicates_graphic_change(task)
    next_agent = "vlad" if needs_graphic_change else "daisy"
    if follow_up_type == "deletion_or_rejection":
        next_agent = "none"
    direct_actions = []
    evidence_trace = []
    action_by_type = {
        "deletion_or_rejection": "review_symbol_disposition_before_deletion_or_rejection",
        "duplicate_resolution": "review_duplicate_relationship_and_record_resolution",
        "metadata_or_classification_update": "update_symbol_metadata_or_classification",
        "evidence_request": "prepare_source_or_contributor_evidence_request",
        "deferral": "record_deferral_context_for_next_review",
        "review_follow_up": "assess_reviewer_feedback_and_prepare_re_review",
    }
    if needs_graphic_change:
        direct_actions.append("prepare_vlad_graphic_change_request")
        add_trace(evidence_trace, "graphic_change_triage", "passed", "Reviewer feedback requires Vlad symbol graphic modification before Daisy re-review.")
    elif follow_up_type == "deletion_or_rejection":
        direct_actions.append("record_libby_deletion_or_rejection_disposition")
        add_trace(evidence_trace, "deletion_or_rejection_triage", "passed", "Libby can record the rejected or deleted symbol disposition without Daisy re-review.")
    else:
        direct_actions.append(action_by_type.get(follow_up_type, "assess_reviewer_feedback_and_prepare_re_review"))
        add_trace(evidence_trace, "review_follow_up_triage", "passed", "Libby can prepare the item for Daisy re-review without Vlad graphic modification.")
    if child_decisions:
        add_trace(evidence_trace, "child_decisions", "passed", f"Received {len(child_decisions)} child-level review decision(s).")
    else:
        add_trace(evidence_trace, "child_decisions", "skipped", "No child-level review decisions were provided.")
    summary = (
        f"Libby assessed {task.get('decision_code')} review feedback for case {task.get('review_case_id')} "
        f"and routed follow-up to {next_agent}."
    )
    artifact = {
        "queue_item_id": queue_item_id,
        "agent": "libby",
        "schema_version": SCHEMA_VERSION,
        "task_type": task.get("task_type"),
        "decision": "escalate" if needs_graphic_change else "pass",
        "confidence": 0.82 if needs_graphic_change else 0.86,
        "escalation_target": next_agent if needs_graphic_change else "none",
        "review_case_id": task.get("review_case_id"),
        "review_decision_id": task.get("review_decision_id"),
        "decision_code": task.get("decision_code"),
        "follow_up_type": follow_up_type,
        "next_agent": next_agent,
        "next_review_agent": "daisy",
        "return_to_agent": "libby" if needs_graphic_change else None,
        "direct_actions": direct_actions,
        "child_decisions": child_decisions,
        "review_feedback": {
            "decision_note": task.get("decision_note"),
            "case_comment": task.get("case_comment"),
            "decider_name": task.get("decider_name"),
            "decider_role": task.get("decider_role"),
        },
        "follow_up_summary": summary,
        "evidence_trace": evidence_trace,
        "defects": [],
    }
    if follow_up_type == "deletion_or_rejection":
        artifact["disposition_split_items"] = disposition_split_items_from_review_followup(task, child_decisions)
    return artifact


def _float_or_none(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _best_duplicate_evidence(task):
    evidence_items = [item for item in ensure_list(task.get("duplicate_evidence")) if isinstance(item, dict)]
    if not evidence_items:
        return None

    def score(item):
        hamming = _float_or_none(item.get("hamming_distance"))
        pixel = _float_or_none(item.get("pixel_difference"))
        return (hamming if hamming is not None else 999.0, pixel if pixel is not None else 999.0)

    return sorted(evidence_items, key=score)[0]


def _duplicate_confidence(evidence):
    if not evidence:
        return 0.0
    hamming = _float_or_none(evidence.get("hamming_distance"))
    hamming_threshold = _float_or_none(evidence.get("distance_threshold")) or 4.0
    pixel = _float_or_none(evidence.get("pixel_difference"))
    pixel_threshold = _float_or_none(evidence.get("pixel_difference_threshold")) or 0.08
    hamming_score = 0.0 if hamming is None else max(0.0, 1.0 - (hamming / max(hamming_threshold, 1.0)))
    pixel_score = 0.0 if pixel is None else max(0.0, 1.0 - (pixel / max(pixel_threshold, 0.000001)))
    return round(min(0.97, max(0.0, 0.50 + (0.25 * hamming_score) + (0.25 * pixel_score))), 2)


def run_duplicate_resolution_task(task):
    queue_item_id = task.get("queue_item_id") or "untracked"
    best = _best_duplicate_evidence(task)
    confidence = _duplicate_confidence(best)
    evidence_trace = []
    defects = []
    hamming = _float_or_none((best or {}).get("hamming_distance"))
    hamming_threshold = _float_or_none((best or {}).get("distance_threshold")) or 4.0
    pixel = _float_or_none((best or {}).get("pixel_difference"))
    pixel_threshold = _float_or_none((best or {}).get("pixel_difference_threshold")) or 0.08
    strong_duplicate = bool(
        best
        and hamming is not None
        and pixel is not None
        and hamming <= max(2.0, hamming_threshold * 0.75)
        and pixel <= min(0.04, pixel_threshold * 0.60)
        and confidence >= 0.78
    )

    if strong_duplicate:
        outcome = "duplicate_confirmed"
        recommended_action = "do_not_publish"
        decision = "pass"
        next_agent = "none"
        escalation_target = "none"
        direct_actions = ["record_duplicate_resolution", "do_not_publish_candidate"]
        add_trace(
            evidence_trace,
            "duplicate_gate_evidence",
            "passed",
            "Libby confirmed Rupert's graphical duplicate gate using low dHash distance and low pixel difference.",
        )
    else:
        outcome = "needs_human_review"
        recommended_action = "send_to_daisy"
        decision = "escalate"
        next_agent = "daisy"
        escalation_target = "daisy"
        direct_actions = ["prepare_duplicate_exception_for_daisy"]
        if best:
            add_trace(
                evidence_trace,
                "duplicate_gate_evidence",
                "warning",
                "Rupert found a possible duplicate, but the match was not strong enough for unattended Libby resolution.",
            )
        else:
            add_trace(evidence_trace, "duplicate_gate_evidence", "failed", "No duplicate evidence was supplied to Libby.")
            add_defect(defects, "missing_duplicate_evidence", "high", "Duplicate-resolution task did not include Rupert evidence.")

    matched_slug = (best or {}).get("matched_symbol_slug")
    candidate_revision_id = (best or {}).get("candidate_revision_id")
    matched_revision_id = (best or {}).get("matched_revision_id")
    reason = (
        f"Best graphical match: dHash distance {hamming} <= {hamming_threshold}, "
        f"pixel difference {pixel} <= {pixel_threshold}; matched symbol {matched_slug or matched_revision_id or 'unknown'}."
        if best
        else "No duplicate evidence was available."
    )
    summary = (
        f"Libby resolved duplicate gate for case {task.get('review_case_id')}: {outcome}. "
        f"Recommended action: {recommended_action}."
    )
    return {
        "queue_item_id": queue_item_id,
        "agent": "libby",
        "schema_version": SCHEMA_VERSION,
        "task_type": task.get("task_type"),
        "decision": decision,
        "confidence": confidence,
        "escalation_target": escalation_target,
        "review_case_id": task.get("review_case_id"),
        "review_decision_id": task.get("review_decision_id"),
        "decision_code": task.get("decision_code") or "duplicate",
        "follow_up_type": "duplicate_resolution",
        "next_agent": next_agent,
        "next_review_agent": "daisy" if next_agent == "daisy" else None,
        "return_to_agent": None,
        "direct_actions": direct_actions,
        "duplicate_resolution": {
            "outcome": outcome,
            "recommended_action": recommended_action,
            "confidence": confidence,
            "candidate_revision_id": candidate_revision_id,
            "matched_revision_id": matched_revision_id,
            "matched_symbol_slug": matched_slug,
            "duplicate_split_item_id": task.get("duplicate_split_item_id"),
            "reason": reason,
            "evidence": best,
        },
        "review_feedback": {
            "decision_note": task.get("decision_note"),
            "case_comment": task.get("case_comment"),
            "origin": task.get("origin"),
        },
        "follow_up_summary": summary,
        "evidence_trace": evidence_trace,
        "defects": defects,
    }


def run_vlad_return_task(task):
    artifact = run_review_follow_up_task(
        {
            **task,
            "task_type": "review_decision_follow_up",
            "libby_follow_up_type": "review_follow_up",
            "decision_code": task.get("decision_code") or "request_changes",
            "decision_note": task.get("vlad_result_summary") or task.get("decision_note"),
        }
    )
    artifact["task_type"] = task.get("task_type")
    artifact["next_agent"] = "daisy"
    artifact["return_to_agent"] = None
    artifact["follow_up_summary"] = f"Libby checked Vlad graphic update for case {task.get('review_case_id')} and prepared Daisy re-review."
    returned_items = returned_split_items_from_vlad_result({"vlad_result": task.get("vlad_result")})
    artifact["vlad_result"] = task.get("vlad_result")
    artifact["returned_split_items"] = returned_items
    artifact["decision"] = "pass"
    artifact["confidence"] = 0.86
    artifact["escalation_target"] = "none"
    artifact["direct_actions"] = ["prepare_daisy_re_review"]
    return artifact


def edited_assets_from_vlad_result(artifact):
    vlad_result = artifact.get("vlad_result") if isinstance(artifact.get("vlad_result"), dict) else {}
    metadata = vlad_result.get("normalized_technical_metadata") if isinstance(vlad_result.get("normalized_technical_metadata"), dict) else {}
    return metadata.get("edited_assets") or []


def returned_split_items_from_vlad_result(artifact):
    edited_assets = edited_assets_from_vlad_result(artifact)
    if edited_assets:
        return edited_assets

    vlad_result = artifact.get("vlad_result") if isinstance(artifact.get("vlad_result"), dict) else {}
    metadata = vlad_result.get("normalized_technical_metadata") if isinstance(vlad_result.get("normalized_technical_metadata"), dict) else {}
    requested_changes = metadata.get("requested_changes") if isinstance(metadata.get("requested_changes"), dict) else {}
    defects = vlad_result.get("defects") if isinstance(vlad_result.get("defects"), list) else []
    defect_summary = "; ".join(str(defect.get("detail") or defect.get("code") or "") for defect in defects if isinstance(defect, dict)).strip()
    result_summary = defect_summary or "Vlad did not produce an edited asset."
    returned_items = []

    for index, child_decision in enumerate(requested_changes.get("child_decisions") or [], start=1):
        if not isinstance(child_decision, dict):
            continue
        child_id = child_decision.get("childId")
        if not child_id:
            continue
        returned_items.append(
            {
                "child_id": child_id,
                "proposed_symbol_id": child_decision.get("proposedSymbolId"),
                "proposed_symbol_name": child_decision.get("proposedSymbolName"),
                "object_key": child_id,
                "file_name": Path(str(child_id)).name,
                "content_type": "image/png",
                "edit_operation": "none",
                "edit_prompt": child_decision.get("details") or child_decision.get("note") or requested_changes.get("decision_note"),
                "edit_status": "no_changes_made",
                "review_note": "no changes made",
                "review_details": result_summary,
                "vlad_result_status": vlad_result.get("decision"),
                "graphic_change_status": metadata.get("graphic_change_status"),
                "defects": defects,
                "sort_order": index,
            }
        )

    return returned_items


def disposition_split_items_from_review_followup(task, child_decisions):
    disposition_items = []
    for index, child_decision in enumerate(child_decisions, start=1):
        if not isinstance(child_decision, dict):
            continue
        child_id = child_decision.get("childId")
        if not child_id:
            continue
        action = str(child_decision.get("action") or task.get("decision_code") or "reject").strip().lower().replace("-", "_")
        disposition = "deleted" if action in {"deleted", "delete"} else "rejected"
        note = child_decision.get("note") or task.get("decision_note")
        details = child_decision.get("details") or task.get("case_comment")
        disposition_items.append(
            {
                "child_id": child_id,
                "proposed_symbol_id": child_decision.get("proposedSymbolId"),
                "proposed_symbol_name": child_decision.get("proposedSymbolName"),
                "disposition": disposition,
                "review_note": note,
                "review_details": details,
                "sort_order": index,
            }
        )
    return disposition_items


def run_task(task):
    task_type = task.get("task_type")
    if task_type == "publication_duplicate_detected" or task.get("libby_follow_up_type") == "duplicate_resolution":
        return run_duplicate_resolution_task(task), "review_followup_report"
    if task_type == "review_decision_follow_up":
        return run_review_follow_up_task(task), "review_followup_report"
    if task_type == "vlad_graphic_update_completed":
        return run_vlad_return_task(task), "review_followup_report"
    return infer_classification(task), "classification_record"


def write_downstream_queue_item(task, artifact, timestamp):
    downstream_queue_item = None
    downstream_root = None
    if artifact.get("next_agent") == "vlad":
        downstream_queue_item = build_vlad_queue_item(task, artifact, timestamp)
        downstream_root = Path(os.environ.get("LIBBY_VLAD_RUNTIME_ROOT", "/data/.openclaw/workspaces/vlad/runtime"))
    elif artifact.get("next_agent") == "daisy":
        downstream_queue_item = build_daisy_queue_item(task, artifact, timestamp)
        downstream_root = Path(os.environ.get("LIBBY_DAISY_RUNTIME_ROOT", "/data/.openclaw/workspaces/daisy/runtime"))

    if not downstream_queue_item or not downstream_root:
        return None

    downstream_queue_path = downstream_root / "agent_queue_items" / f"{downstream_queue_item['id']}.json"
    write_json(downstream_queue_path, downstream_queue_item)
    return str(downstream_queue_path)


def process_batch_queue_item(queue_item, runtime_root, started_at, notification_status, persist_db=False, db_env_file=None, storage_env_file=None):
    payload = copy.deepcopy(queue_item.get("payload_json") or {})
    parent_task = queue_item_payload_to_task(queue_item)
    item_payloads = batch_items_from_payload(payload)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    item_results = []
    downstream_queue_paths = []

    for index, item_payload in enumerate(item_payloads, start=1):
        if not isinstance(item_payload, dict):
            raise ValueError(f"Batch item {index} must be an object.")
        item_id = item_payload.get("item_id") or item_payload.get("child_id") or item_payload.get("review_case_id") or index
        task = {
            **parent_task,
            **copy.deepcopy(item_payload),
            "queue_item_id": f"{queue_item['id']}-{safe_token(item_id, f'item-{index}')}",
            "batch_queue_item_id": queue_item["id"],
            "batch_item_index": index,
            "item_id": item_id,
        }
        artifact, durable_kind = run_task(task)
        if durable_kind == "classification_record":
            artifact = enrich_classification_with_symbol_image(
                artifact,
                task,
                db_env_file=db_env_file if (persist_db or env_flag("SYMGOV_PERSIST_TO_DB")) else None,
                storage_env_file=storage_env_file,
            )
        item_completed_at = utc_now()
        downstream_queue_path = None
        if durable_kind == "review_followup_report":
            report_id = stamp_id("lfr", task["queue_item_id"])
            report_record = {
                "id": report_id,
                "queue_item_id": queue_item["id"],
                "item_queue_item_id": task["queue_item_id"],
                "batch_item_index": index,
                "item_id": item_id,
                "review_case_id": task.get("review_case_id"),
                "review_decision_id": task.get("review_decision_id"),
                "follow_up_type": artifact["follow_up_type"],
                "next_agent": artifact["next_agent"],
                "payload_json": artifact,
                "created_at": item_completed_at,
            }
            write_json(runtime_root / "review_followup_reports" / f"{report_id}.json", report_record)
            downstream_queue_path = write_downstream_queue_item(task, artifact, timestamp)
        else:
            classification_id = stamp_id("cr", task["queue_item_id"])
            classification_record = {
                "id": classification_id,
                "queue_item_id": queue_item["id"],
                "item_queue_item_id": task["queue_item_id"],
                "batch_item_index": index,
                "item_id": item_id,
                "intake_record_id": task.get("intake_record_id"),
                "review_case_id": task.get("review_case_id"),
                "origin_file_name": task.get("origin_file_name"),
                "symbol_key": task.get("symbol_key") or task.get("candidate_symbol_id") or task.get("origin_file_name"),
                "status": "current",
                "classification_status": artifact["classification_status"],
                "category": artifact["category"],
                "discipline": artifact["discipline"],
                "format": artifact["format"],
                "industry": artifact["industry"],
                "symbol_family": artifact["symbol_family"],
                "process_category": artifact["process_category"],
                "parent_equipment_class": artifact["parent_equipment_class"],
                "standards_source": artifact["standards_source"],
                "library_provenance_class": artifact["library_provenance_class"],
                "source_classification": artifact["source_classification"],
                "aliases_json": artifact["aliases"],
                "search_terms_json": artifact["search_terms"],
                "source_refs_json": artifact["source_refs"],
                "evidence_json": artifact["evidence"],
                "taxonomy_terms_created_json": artifact["taxonomy_terms_created"],
                "review_summary": artifact["classification_summary"],
                "confidence": artifact["confidence"],
                "libby_approved": artifact["libby_approved"],
                "created_at": item_completed_at,
                "updated_at": item_completed_at,
            }
            write_json(runtime_root / "classification_records" / f"{classification_id}.json", classification_record)

        if downstream_queue_path:
            downstream_queue_paths.append(downstream_queue_path)

        item_results.append(
            {
                "item_id": item_id,
                "batch_item_index": index,
                "durable_kind": durable_kind,
                "decision": artifact["decision"],
                "next_agent": artifact.get("next_agent"),
                "downstream_queue_item_path": downstream_queue_path,
                "artifact": artifact,
            }
        )

    completed_at = utc_now()
    batch_decision = "escalate" if any(item["decision"] == "escalate" for item in item_results) else "pass"
    queue_item["status"] = queue_status_for_decision(batch_decision)
    queue_item["confidence"] = min((item["artifact"].get("confidence") or 0 for item in item_results), default=0)
    queue_item["escalation_reason"] = "batch_contains_escalated_items" if batch_decision == "escalate" else None
    queue_item["completed_at"] = completed_at
    batch_artifact = {
        "queue_item_id": queue_item["id"],
        "agent": "libby",
        "schema_version": SCHEMA_VERSION,
        "task_type": payload.get("task_type") or "batch_review_follow_up",
        "decision": batch_decision,
        "confidence": queue_item["confidence"],
        "escalation_target": "mixed_downstream" if batch_decision == "escalate" else "none",
        "item_count": len(item_results),
        "results": item_results,
        "evidence_trace": [
            {
                "check": "batch_items",
                "status": "passed",
                "detail": f"Processed {len(item_results)} Libby batch item(s).",
            }
        ],
    }

    run_id = stamp_id("arun", queue_item["id"])
    run_record = {
        "id": run_id,
        "queue_item_id": queue_item["id"],
        "model": "ollama/gemma4:e4b",
        "prompt_version": PROMPT_VERSION,
        "tool_trace_json": batch_artifact["evidence_trace"],
        "result_status": queue_item["status"],
        "started_at": started_at,
        "completed_at": completed_at,
    }
    artifact_id = stamp_id("aout", queue_item["id"])
    output_artifact_record = {
        "id": artifact_id,
        "queue_item_id": queue_item["id"],
        "artifact_type": "libby_batch_report",
        "schema_version": batch_artifact["schema_version"],
        "payload_json": batch_artifact,
        "created_at": completed_at,
    }
    write_json(runtime_root / "agent_runs" / f"{run_id}.json", run_record)
    write_json(runtime_root / "agent_output_artifacts" / f"{artifact_id}.json", output_artifact_record)

    notification_status["completed"] = send_libby_status_update(
        "completed",
        queue_item,
        artifact=batch_artifact,
        queue_status=queue_item["status"],
    )

    return {
        "queue_item_status": queue_item["status"],
        "run_record_path": str(runtime_root / "agent_runs" / f"{run_id}.json"),
        "artifact_record_path": str(runtime_root / "agent_output_artifacts" / f"{artifact_id}.json"),
        "downstream_queue_item_paths": downstream_queue_paths,
        "db_persistence": "not_supported_for_batch" if persist_db else None,
        "notifications": notification_status,
        "artifact": batch_artifact,
    }


def process_queue_item(queue_item_path, runtime_root, persist_db=False, db_env_file=None, storage_env_file=None):
    queue_item_path = Path(queue_item_path)
    runtime_root = Path(runtime_root)

    with queue_item_path.open("r", encoding="utf-8") as handle:
        queue_item = json.load(handle)

    if queue_item.get("agent_id") != "libby":
        raise ValueError("Queue item agent_id must be 'libby'.")

    started_at = utc_now()
    queue_item["status"] = "running"
    queue_item["started_at"] = started_at
    write_json(queue_item_path, queue_item)
    notification_status = {
        "started": send_libby_status_update("started", queue_item),
        "completed": None,
    }

    if is_batch_payload(queue_item.get("payload_json") or {}):
        batch_result = process_batch_queue_item(
            queue_item,
            runtime_root,
            started_at,
            notification_status,
            persist_db=persist_db,
            db_env_file=db_env_file,
            storage_env_file=storage_env_file,
        )
        queue_item["status"] = queue_status_for_decision(batch_result["artifact"]["decision"])
        queue_item["confidence"] = batch_result["artifact"]["confidence"]
        queue_item["escalation_reason"] = (
            "batch_contains_escalated_items" if batch_result["artifact"]["decision"] == "escalate" else None
        )
        write_json(queue_item_path, queue_item)
        batch_result["queue_item_path"] = str(queue_item_path)
        batch_result["queue_item_status"] = queue_item["status"]
        return batch_result

    task = queue_item_payload_to_task(queue_item)
    artifact, durable_kind = run_task(task)
    if durable_kind == "classification_record":
        artifact = enrich_classification_with_symbol_image(
            artifact,
            task,
            db_env_file=db_env_file if (persist_db or env_flag("SYMGOV_PERSIST_TO_DB")) else None,
            storage_env_file=storage_env_file,
        )
    completed_at = utc_now()

    queue_item["status"] = queue_status_for_decision(artifact["decision"])
    queue_item["confidence"] = artifact["confidence"]
    queue_item["escalation_reason"] = (
        f"{durable_kind}_requires_{artifact['escalation_target']}" if artifact["decision"] == "escalate" else None
    )
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
        "artifact_type": durable_kind,
        "schema_version": artifact["schema_version"],
        "payload_json": artifact,
        "created_at": completed_at,
    }

    if durable_kind == "review_followup_report":
        report_id = stamp_id("lfr", queue_item["id"])
        report_record = {
            "id": report_id,
            "queue_item_id": queue_item["id"],
            "review_case_id": task.get("review_case_id"),
            "review_decision_id": task.get("review_decision_id"),
            "follow_up_type": artifact["follow_up_type"],
            "next_agent": artifact["next_agent"],
            "payload_json": artifact,
            "created_at": completed_at,
        }
        write_json(runtime_root / "agent_runs" / f"{run_id}.json", run_record)
        write_json(runtime_root / "agent_output_artifacts" / f"{artifact_id}.json", output_artifact_record)
        write_json(runtime_root / "review_followup_reports" / f"{report_id}.json", report_record)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        downstream_queue_item = None
        downstream_root = None
        if artifact["next_agent"] == "vlad":
            downstream_queue_item = build_vlad_queue_item(task, artifact, timestamp)
            downstream_root = Path("/data/.openclaw/workspaces/vlad/runtime")
        elif artifact["next_agent"] == "daisy":
            downstream_queue_item = build_daisy_queue_item(task, artifact, timestamp)
            downstream_root = Path("/data/.openclaw/workspaces/daisy/runtime")

        downstream_queue_path = None
        if downstream_queue_item and downstream_root:
            downstream_queue_path = downstream_root / "agent_queue_items" / f"{downstream_queue_item['id']}.json"
            write_json(downstream_queue_path, downstream_queue_item)

        db_persistence = None
        downstream_db_persistence = None
        returned_split_items = []
        disposition_split_items = []
        duplicate_resolution_split_item = None
        if persist_db or env_flag("SYMGOV_PERSIST_TO_DB"):
            bridge = RuntimePersistenceBridge(env_file=db_env_file)
            db_persistence = bridge.persist_agent_execution(
                queue_item=queue_item,
                run_record=run_record,
                output_artifact_record=output_artifact_record,
                durable_record=report_record,
                durable_kind="review_followup_report",
            )
            if downstream_queue_item:
                downstream_db_persistence = bridge.upsert_agent_queue_item(downstream_queue_item)
            returned_split_items = []
            for returned_item in artifact.get("returned_split_items") or []:
                child_key = returned_item.get("child_id")
                object_key = returned_item.get("attachment_object_key") or returned_item.get("object_key")
                if task.get("review_case_id") and child_key and object_key:
                    payload_key = "vlad_no_change_result" if returned_item.get("edit_status") == "no_changes_made" else "vlad_edited_asset"
                    returned_split_items.append(
                        bridge.return_review_split_item_for_review(
                            review_case_id=task["review_case_id"],
                            child_key=child_key,
                            attachment_object_key=object_key,
                            payload_updates={payload_key: returned_item},
                            latest_note=returned_item.get("review_note"),
                            latest_details=returned_item.get("review_details"),
                        )
                    )
            disposition_split_items = []
            for disposition_item in artifact.get("disposition_split_items") or []:
                child_key = disposition_item.get("child_id")
                if task.get("review_case_id") and child_key:
                    disposition_split_items.append(
                        bridge.dispose_review_split_item(
                            review_case_id=task["review_case_id"],
                            child_key=child_key,
                            disposition=disposition_item.get("disposition") or "rejected",
                            latest_note=disposition_item.get("review_note"),
                            latest_details=disposition_item.get("review_details"),
                            payload_updates={"libby_disposition_result": disposition_item},
                        )
                    )
            duplicate_resolution_split_item = None
            if artifact.get("follow_up_type") == "duplicate_resolution" and task.get("review_case_id"):
                duplicate_resolution_split_item = bridge.resolve_duplicate_review_split_item(
                    review_case_id=task["review_case_id"],
                    review_decision_id=task.get("review_decision_id"),
                    queue_item_id=queue_item["id"],
                    duplicate_resolution=artifact.get("duplicate_resolution") or {},
                )

        notification_status["completed"] = send_libby_status_update(
            "completed",
            queue_item,
            artifact=artifact,
            queue_status=queue_item["status"],
        )

        return {
            "queue_item_path": str(queue_item_path),
            "queue_item_status": queue_item["status"],
            "run_record_path": str(runtime_root / "agent_runs" / f"{run_id}.json"),
            "artifact_record_path": str(runtime_root / "agent_output_artifacts" / f"{artifact_id}.json"),
            "review_followup_report_path": str(runtime_root / "review_followup_reports" / f"{report_id}.json"),
            "downstream_queue_item_path": str(downstream_queue_path) if downstream_queue_path else None,
            "downstream_agent": artifact["next_agent"],
            "db_persistence": db_persistence,
            "downstream_db_persistence": downstream_db_persistence,
            "additional_db_records": {
                "review_case": None,
                "returned_split_items": returned_split_items if (persist_db or env_flag("SYMGOV_PERSIST_TO_DB")) else [],
                "disposition_split_items": disposition_split_items if (persist_db or env_flag("SYMGOV_PERSIST_TO_DB")) else [],
                "duplicate_resolution_split_item": duplicate_resolution_split_item if (persist_db or env_flag("SYMGOV_PERSIST_TO_DB")) else None,
            },
            "notifications": notification_status,
            "artifact": artifact,
        }

    classification_id = stamp_id("cr", queue_item["id"])
    classification_record = {
        "id": classification_id,
        "queue_item_id": queue_item["id"],
        "intake_record_id": task.get("intake_record_id"),
        "validation_report_id": task.get("validation_report_id"),
        "provenance_assessment_id": task.get("provenance_assessment_id"),
        "review_case_id": task.get("review_case_id"),
        "origin_attachment_id": task.get("origin_attachment_id"),
        "origin_object_key": task.get("origin_object_key"),
        "origin_file_name": task.get("origin_file_name"),
        "origin_batch_id": task.get("origin_batch_id"),
        "parent_review_case_id": task.get("parent_review_case_id"),
        "symbol_key": task.get("symbol_key") or task.get("candidate_symbol_id") or task.get("origin_file_name"),
        "symbol_region_index": task.get("symbol_region_index"),
        "status": "current",
        "classification_status": artifact["classification_status"],
        "supersedes_classification_id": task.get("current_classification_id"),
        "source_id": task.get("source_id") or task.get("provenance_assessment_id") or task.get("intake_record_id"),
        "source_type": task.get("source_type") or "provenance_assessment",
        "category": artifact["category"],
        "discipline": artifact["discipline"],
        "format": artifact["format"],
        "industry": artifact["industry"],
        "symbol_family": artifact["symbol_family"],
        "process_category": artifact["process_category"],
        "parent_equipment_class": artifact["parent_equipment_class"],
        "standards_source": artifact["standards_source"],
        "library_provenance_class": artifact["library_provenance_class"],
        "source_classification": artifact["source_classification"],
        "aliases_json": artifact["aliases"],
        "search_terms_json": artifact["search_terms"],
        "source_refs_json": artifact["source_refs"],
        "evidence_json": artifact["evidence"],
        "taxonomy_terms_created_json": artifact["taxonomy_terms_created"],
        "review_summary": artifact["classification_summary"],
        "confidence": artifact["confidence"],
        "libby_approved": artifact["libby_approved"],
        "created_at": completed_at,
        "updated_at": completed_at,
    }

    write_json(runtime_root / "agent_runs" / f"{run_id}.json", run_record)
    write_json(runtime_root / "agent_output_artifacts" / f"{artifact_id}.json", output_artifact_record)
    write_json(runtime_root / "classification_records" / f"{classification_id}.json", classification_record)

    db_persistence = None
    additional_db_records = {"review_case": None}
    if persist_db or env_flag("SYMGOV_PERSIST_TO_DB"):
        bridge = RuntimePersistenceBridge(env_file=db_env_file)
        db_persistence = bridge.persist_agent_execution(
            queue_item=queue_item,
            run_record=run_record,
            output_artifact_record=output_artifact_record,
            durable_record=classification_record,
            durable_kind="classification_record",
        )
        if task.get("review_case_id"):
            additional_db_records["review_case"] = bridge.update_review_case(
                review_case_id=task["review_case_id"],
                current_stage="classification_review",
            )

    notification_status["completed"] = send_libby_status_update(
        "completed",
        queue_item,
        artifact=artifact,
        queue_status=queue_item["status"],
    )

    return {
        "queue_item_path": str(queue_item_path),
        "queue_item_status": queue_item["status"],
        "run_record_path": str(runtime_root / "agent_runs" / f"{run_id}.json"),
        "artifact_record_path": str(runtime_root / "agent_output_artifacts" / f"{artifact_id}.json"),
        "classification_record_path": str(runtime_root / "classification_records" / f"{classification_id}.json"),
        "db_persistence": db_persistence,
        "additional_db_records": additional_db_records,
        "notifications": notification_status,
        "artifact": artifact,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Run local Libby classification in task or queue mode.")
    parser.add_argument("--input", help="Path to a JSON task file.")
    parser.add_argument("--output", help="Path to write the JSON classification artifact.")
    parser.add_argument("--queue-item", help="Path to an agent_queue_item JSON record.")
    parser.add_argument("--runtime-root", help="Root directory for local file-backed queue records.")
    parser.add_argument(
        "--cleanup-queue-item",
        action="store_true",
        help="Remove the specified queue item from this agent's runtime/agent_queue_items directory.",
    )
    parser.add_argument(
        "--queue-status",
        action="store_true",
        help="Print local Libby queue counts and oldest item details for chat/status use.",
    )
    parser.add_argument(
        "--persist-db",
        action="store_true",
        help="Also mirror queue, run, artifact, and classification records into the Symgov database.",
    )
    parser.add_argument("--db-env-file", help="Path to the Symgov database env file used with --persist-db.")
    parser.add_argument("--storage-env-file", help="Path to the Symgov object-storage env file used for symbol image lookup.")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.queue_status:
        if not args.runtime_root:
            raise SystemExit("--runtime-root is required with --queue-status.")
        print(json.dumps(queue_status(args.runtime_root), indent=2))
        return

    if args.cleanup_queue_item:
        if not args.queue_item or not args.runtime_root:
            raise SystemExit("--queue-item and --runtime-root are required with --cleanup-queue-item.")
        print(json.dumps(cleanup_queue_item(args.queue_item, args.runtime_root), indent=2))
        return

    if args.queue_item:
        if not args.runtime_root:
            raise SystemExit("--runtime-root is required with --queue-item.")
        result = process_queue_item(
            args.queue_item,
            args.runtime_root,
            persist_db=args.persist_db,
            db_env_file=args.db_env_file,
            storage_env_file=args.storage_env_file,
        )
        print(json.dumps(result, indent=2))
        return

    if not args.input or not args.output:
        raise SystemExit("--input and --output are required when not using --queue-item.")

    input_path = Path(args.input)
    output_path = Path(args.output)
    with input_path.open("r", encoding="utf-8") as handle:
        task = json.load(handle)

    artifact = infer_classification(task)
    write_json(output_path, artifact)


if __name__ == "__main__":
    main()
