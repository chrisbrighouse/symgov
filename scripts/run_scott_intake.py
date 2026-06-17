#!/usr/bin/env python3
import argparse
import copy
import html
from html.parser import HTMLParser
import importlib.util
import json
import re
import socket
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import hashlib
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath


SCHEMA_VERSION = "0.1.0"
PROMPT_VERSION = "scott-local-contract-0.1.0"
SOURCE_DISCOVERY_PROMPT_VERSION = "scott-source-discovery-0.1.0"
SUPPORTED_SUBMISSION_KINDS = {
    "single_upload",
    "contributor_submission",
    "imported_symbol_library",
}
SUPPORTED_FORMATS = {
    ".svg": "svg",
    ".json": "json",
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".dxf": "dxf",
    ".zip": "zip",
}
DEFAULT_ENV_PATH = Path("/data/.openclaw/workspace/symgov/.env.backend.database")
REPO_BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
BACKEND_ROOT = REPO_BACKEND_ROOT if REPO_BACKEND_ROOT.exists() else Path("/data/.openclaw/workspace/symgov/backend")
DEFAULT_SOURCE_DISCOVERY_DURATION_SECONDS = 300
SOURCE_DISCOVERY_QUERY_SEEDS = [
    "engineering symbol library P&ID valve symbols SVG",
    "industrial process control symbols downloadable library",
    "ISA instrumentation symbols standard library",
    "CAD P&ID symbols valve pump instrumentation",
    "public engineering standards symbol library",
]
SYMBOL_FORMAT_KEYWORDS = {
    "SVG": (".svg", "svg"),
    "PNG": (".png", "png"),
    "JPEG": (".jpg", ".jpeg", "jpeg"),
    "DXF": (".dxf", "dxf"),
    "DWG": (".dwg", "dwg"),
    "PDF": (".pdf", "pdf"),
    "JSON": (".json", "json"),
}
INDUSTRY_KEYWORDS = {
    "process engineering": ("p&id", "piping", "instrumentation", "process"),
    "electrical": ("electrical", "iec", "schematic"),
    "mechanical": ("mechanical", "hydraulic", "pneumatic"),
    "fire safety": ("fire", "nfpa"),
}
PROCESS_KEYWORDS = {
    "piping and instrumentation": ("p&id", "piping and instrumentation", "isa"),
    "cad library import": ("cad", "autocad", "dxf", "dwg"),
    "standards reference": ("standard", "specification", "iso", "iec", "ansi"),
    "vendor catalogue": ("catalog", "catalogue", "product", "manufacturer"),
}

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.filename_inference import infer_filename_metadata, inferred_candidate_title
from symgov_backend.runtime import RuntimePersistenceBridge, env_flag
from symgov_backend.notifications import send_agent_status_update
from sqlalchemy import text


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
        "message": "Queue item removed from Scott runtime queue.",
    }


def queue_status_for_decision(decision):
    if decision == "escalated":
        return "escalated"
    return "completed"


def infer_format(raw_input_path, declared_format):
    suffix = Path(raw_input_path).suffix.lower() if raw_input_path else ""
    inferred = SUPPORTED_FORMATS.get(suffix)
    normalized_declared = declared_format.lower() if isinstance(declared_format, str) else None
    return suffix, inferred, normalized_declared


def ensure_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


ZIP_MEMBER_LIMIT = 200
ZIP_MAX_MEMBER_BYTES = 50 * 1024 * 1024
ZIP_MAX_TOTAL_BYTES = 250 * 1024 * 1024
ZIP_MAX_COMPRESSION_RATIO = 100
ZIP_MEMBER_FORMATS = {key: value for key, value in SUPPORTED_FORMATS.items() if value != "zip"}


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def safe_package_token(value):
    normalized = "".join(char.lower() if char.isalnum() else "-" for char in str(value or "").strip())
    return "-".join(part for part in normalized.split("-") if part) or "member"


def zip_member_reason_codes(info):
    raw_name = info.filename or ""
    posix_path = PurePosixPath(raw_name.replace("\\", "/"))
    windows_path = PureWindowsPath(raw_name)
    reasons = []
    if not raw_name or raw_name.endswith("/") or info.is_dir():
        reasons.append("directory_member")
    if raw_name.startswith(("/", "\\")) or posix_path.is_absolute() or windows_path.is_absolute():
        reasons.append("absolute_path")
    if windows_path.drive:
        reasons.append("windows_drive_path")
    if any(part in {"", ".", ".."} for part in posix_path.parts):
        reasons.append("path_traversal")
    mode = (info.external_attr >> 16) & 0o170000
    if mode in {0o120000, 0o010000, 0o020000, 0o060000, 0o140000}:
        reasons.append("symlink_or_special_file")
    if info.file_size > ZIP_MAX_MEMBER_BYTES:
        reasons.append("member_too_large")
    if info.compress_size and info.file_size / max(1, info.compress_size) > ZIP_MAX_COMPRESSION_RATIO:
        reasons.append("suspicious_compression_ratio")
    if Path(posix_path.name).suffix.lower() == ".zip":
        reasons.append("nested_zip_not_supported")
    return reasons


def build_zip_member_task(base_task, manifest, member):
    member_path = member["safe_stored_path"]
    package_id = manifest["source_package_id"]
    member_id = member["member_id"]
    filename = member["filename"]
    filename_inference = infer_filename_metadata(filename)
    object_key = f"{manifest['source_package_object_key']}/members/{member_id}/{filename}" if manifest.get("source_package_object_key") else None
    package_member = {
        key: member[key]
        for key in (
            "member_id",
            "member_index",
            "original_path",
            "filename",
            "extension",
            "declared_format",
            "sha256",
            "compressed_size",
            "uncompressed_size",
        )
    }
    source_asset = {
        "object_key": object_key,
        "filename": filename,
        "content_type": member.get("content_type") or "application/octet-stream",
        "format": member["declared_format"],
        "role": "package_member_source",
        "downloadable": True,
        "source_package_id": package_id,
        "package_member_id": member_id,
        "original_path": member["original_path"],
    }
    return {
        "submission_kind": base_task.get("submission_kind"),
        "source_ref": base_task.get("source_ref"),
        "submitted_by": base_task.get("submitted_by"),
        "raw_input_path": member_path,
        "original_filename": filename,
        "declared_format": member["declared_format"],
        "candidate_symbol_id": f"{safe_package_token(Path(filename).stem).upper()}-{member['member_index']:03d}",
        "candidate_title": inferred_candidate_title(filename),
        "filename_inference": filename_inference,
        "contributor_name": base_task.get("contributor_name"),
        "contributor_org": base_task.get("contributor_org"),
        "contributor_declaration": base_task.get("contributor_declaration"),
        "source_notes": base_task.get("source_notes"),
        "submission_batch_id": base_task.get("submission_batch_id"),
        "submission_batch_summary": base_task.get("submission_batch_summary"),
        "file_note": base_task.get("file_note"),
        "external_submitter_id": base_task.get("external_submitter_id"),
        "attachment_id": base_task.get("attachment_id"),
        "attachment_ids": ensure_list(base_task.get("attachment_ids")) or ensure_list(base_task.get("attachment_id")),
        "raw_object_key": object_key,
        "visual_assets": {"source_assets": [source_asset]},
        "companion_files": [],
        "rights_documents": ensure_list(base_task.get("rights_documents")),
        "evidence_links": ensure_list(base_task.get("evidence_links")),
        "standards_source_refs": ensure_list(base_task.get("standards_source_refs")),
        "source_package_id": package_id,
        "source_package_attachment_id": manifest.get("source_package_attachment_id"),
        "source_package_object_key": manifest.get("source_package_object_key"),
        "source_package_sha256": manifest.get("source_package_sha256"),
        "source_package_queue_item_id": manifest.get("source_package_queue_item_id"),
        "package_member": package_member,
    }


def zip_member_pair_key(member):
    """Return a conservative package-local key for pairing companion files."""
    original_path = str(member.get("original_path") or "").replace("\\", "/")
    posix = PurePosixPath(original_path)
    parent = posix.parent.as_posix() if posix.parent.as_posix() != "." else ""
    return (parent.lower(), Path(member.get("filename") or "").stem.lower())


def zip_member_source_asset(manifest, member, role="package_member_source"):
    object_key = (
        f"{manifest['source_package_object_key']}/members/{member['member_id']}/{member['filename']}"
        if manifest.get("source_package_object_key")
        else None
    )
    return {
        "object_key": object_key,
        "filename": member["filename"],
        "content_type": member.get("content_type") or "application/octet-stream",
        "format": member["declared_format"],
        "role": role,
        "downloadable": True,
        "source_package_id": manifest["source_package_id"],
        "package_member_id": member["member_id"],
        "original_path": member["original_path"],
    }


def mark_zip_task_as_standalone_symbol(task):
    """Mark a ZIP member as an already-isolated symbol, not a raster sheet."""
    task["package_member_relationship"] = "standalone_symbol_file"
    task["package_symbol_grouping"] = "standalone_package_symbol_file"
    package_member = task.get("package_member")
    if isinstance(package_member, dict):
        package_member["relationship"] = "standalone_symbol_file"


def attach_zip_companion_to_task(task, manifest, companion_member):
    companion_asset = zip_member_source_asset(manifest, companion_member, role="package_member_companion")
    task.setdefault("visual_assets", {}).setdefault("source_assets", []).append(companion_asset)
    task.setdefault("companion_files", []).append(
        {
            "file_name": companion_member["filename"],
            "format": companion_member["declared_format"],
            "role": "package_member_companion",
            "raw_input_path": companion_member["safe_stored_path"],
            "object_key": companion_asset.get("object_key"),
            "source_package_id": manifest["source_package_id"],
            "package_member_id": companion_member["member_id"],
            "original_path": companion_member["original_path"],
            "sha256": companion_member.get("sha256"),
        }
    )
    task["package_member_relationship"] = "primary_with_companion"
    task["package_symbol_grouping"] = "paired_dxf_raster_symbol"
    package_member = task.get("package_member")
    if isinstance(package_member, dict):
        package_member["relationship"] = "primary"
        package_member["companion_member_ids"] = [companion_member["member_id"]]
    companion_member["relationship"] = "companion"
    companion_member["primary_member_id"] = package_member.get("member_id") if isinstance(package_member, dict) else None


def build_zip_member_tasks(base_task, manifest):
    accepted_members = [member for member in manifest.get("members", []) if member.get("status") == "accepted"]
    by_pair_key = {}
    for member in accepted_members:
        by_pair_key.setdefault(zip_member_pair_key(member), []).append(member)

    companion_member_ids = set()
    primary_companions = {}
    raster_formats = {"jpeg", "png"}
    for members in by_pair_key.values():
        dxf_members = [member for member in members if member.get("declared_format") == "dxf"]
        raster_members = [member for member in members if member.get("declared_format") in raster_formats]
        if len(dxf_members) == 1 and len(raster_members) == 1:
            primary = dxf_members[0]
            companion = raster_members[0]
            primary_companions[primary["member_id"]] = companion
            companion_member_ids.add(companion["member_id"])

    child_tasks = []
    for member in accepted_members:
        if member["member_id"] in companion_member_ids:
            member.setdefault("downstream_queue_ids", [])
            member["downstream_role"] = "companion_to_primary_symbol"
            continue
        task = build_zip_member_task(base_task, manifest, member)
        mark_zip_task_as_standalone_symbol(task)
        companion = primary_companions.get(member["member_id"])
        if companion:
            attach_zip_companion_to_task(task, manifest, companion)
        child_tasks.append(task)
    return child_tasks


def expand_zip_package(task, raw_input_path, queue_item_id):
    package_sha = sha256_file(raw_input_path)
    package_id = f"pkg-{safe_package_token(queue_item_id)}-{package_sha[:12]}"
    workspace_root = Path(task.get("package_workspace_root") or raw_input_path.parent / "zip_packages")
    package_dir = workspace_root / package_id
    package_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source_package_id": package_id,
        "source_package_attachment_id": task.get("attachment_id"),
        "source_package_object_key": task.get("raw_object_key"),
        "source_package_sha256": package_sha,
        "source_package_queue_item_id": queue_item_id,
        "original_zip_filename": task.get("original_filename") or raw_input_path.name,
        "package_workspace_path": str(package_dir),
        "members": [],
    }
    child_tasks = []
    total_uncompressed = 0
    unsafe_package = False
    try:
        with zipfile.ZipFile(raw_input_path) as archive:
            infos = archive.infolist()
            if len(infos) > ZIP_MEMBER_LIMIT:
                unsafe_package = True
                manifest["members"].append({
                    "member_id": f"{package_id}-limit",
                    "member_index": 0,
                    "original_path": "<archive>",
                    "filename": raw_input_path.name,
                    "extension": ".zip",
                    "declared_format": None,
                    "sha256": None,
                    "compressed_size": raw_input_path.stat().st_size,
                    "uncompressed_size": None,
                    "status": "rejected",
                    "reason_codes": ["too_many_members"],
                    "downstream_queue_ids": [],
                })
                return manifest, child_tasks, ["too_many_members"]
            for index, info in enumerate(infos, start=1):
                original_path = info.filename
                filename = Path(PurePosixPath(original_path.replace("\\", "/")).name).name
                extension = Path(filename).suffix.lower()
                declared_format = ZIP_MEMBER_FORMATS.get(extension)
                reason_codes = zip_member_reason_codes(info)
                total_uncompressed += info.file_size
                if total_uncompressed > ZIP_MAX_TOTAL_BYTES:
                    reason_codes.append("package_too_large")
                normalized_member_path = PurePosixPath(original_path.replace("\\", "/")).as_posix()
                member_id = f"{index:04d}-{safe_package_token(normalized_member_path)}-{info.CRC:08x}"
                safe_stored_path = str(package_dir / member_id / filename) if filename else None
                member = {
                    "member_id": member_id,
                    "member_index": index,
                    "original_path": original_path,
                    "safe_stored_path": safe_stored_path,
                    "filename": filename,
                    "extension": extension or None,
                    "declared_format": declared_format,
                    "sha256": None,
                    "compressed_size": info.compress_size,
                    "uncompressed_size": info.file_size,
                    "status": "accepted",
                    "reason_codes": [],
                    "downstream_queue_ids": [],
                }
                if reason_codes:
                    member["status"] = "rejected"
                    member["reason_codes"] = reason_codes
                    unsafe_package = True
                    manifest["members"].append(member)
                    continue
                if declared_format is None:
                    member["status"] = "skipped"
                    member["reason_codes"] = ["unsupported_member_format"]
                    manifest["members"].append(member)
                    continue
                payload = archive.read(info)
                member["sha256"] = sha256_bytes(payload)
                output_path = Path(safe_stored_path)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(payload)
                manifest["members"].append(member)
    except zipfile.BadZipFile:
        return manifest, child_tasks, ["bad_zip_file"]
    if unsafe_package:
        return manifest, child_tasks, ["unsafe_zip_member"]
    child_tasks = build_zip_member_tasks(task, manifest)
    if not child_tasks:
        return manifest, child_tasks, ["no_supported_members"]
    return manifest, child_tasks, []


class SearchResultParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self._active_href = None
        self._active_text = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attr_map = dict(attrs)
        href = attr_map.get("href")
        if href:
            self._active_href = href
            self._active_text = []

    def handle_data(self, data):
        if self._active_href:
            self._active_text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._active_href:
            text = " ".join(part.strip() for part in self._active_text if part.strip())
            self.results.append({"url": self._active_href, "title": html.unescape(text)})
            self._active_href = None
            self._active_text = []


class PageSummaryParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.description = ""
        self.text_parts = []
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "meta":
            attr_map = {name.lower(): value for name, value in attrs}
            name = (attr_map.get("name") or attr_map.get("property") or "").lower()
            if name in {"description", "og:description"} and attr_map.get("content") and not self.description:
                self.description = html.unescape(attr_map["content"].strip())

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skip_depth:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title:
            self.title = html.unescape(f"{self.title} {text}".strip())
            return
        if len(" ".join(self.text_parts)) < 8000:
            self.text_parts.append(html.unescape(text))


def fetch_text_url(url, timeout=15, max_bytes=600000, auth_secret_key=None, db_env_file=None):
    headers = {
        "User-Agent": "Symgov Scott source discovery/0.1 (+https://apps.chrisbrighouse.com/apps/workspace/symgov/)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    # 1. Resolve credential value from environment if key looks like an environment variable name
    resolved_secret = None
    if auth_secret_key:
        import os
        # First check system env
        resolved_secret = os.environ.get(auth_secret_key)
        # Next try db env file if system env is empty
        if not resolved_secret:
            try:
                _, env_map = load_env_file(db_env_file)
                resolved_secret = env_map.get(auth_secret_key)
            except Exception:
                pass
        # Fallback to literal if neither found
        if not resolved_secret:
            resolved_secret = auth_secret_key

    # 2. Inject Authorization headers if a credential was resolved
    if resolved_secret:
        if resolved_secret.startswith("Bearer ") or resolved_secret.startswith("Basic "):
            headers["Authorization"] = resolved_secret
        elif ":" in resolved_secret and not resolved_secret.startswith("http"):
            # Username:Password Basic auth
            import base64
            encoded = base64.b64encode(resolved_secret.encode("utf-8")).decode("utf-8")
            headers["Authorization"] = f"Basic {encoded}"
        elif resolved_secret.startswith("Cookie: "):
            headers["Cookie"] = resolved_secret.removeprefix("Cookie: ")
        else:
            headers["X-Api-Key"] = resolved_secret

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final_url = response.geturl() or url
            status_code = int(getattr(response, "status", 200) or 200)
            content_type = response.headers.get("content-type", "")
            raw = response.read(max_bytes)
    except urllib.error.HTTPError as exc:
        status_code = int(getattr(exc, "code", 0) or 0)
        content_type = exc.headers.get("content-type", "") if exc.headers else ""
        raw = b""
        try:
            raw = exc.read(max_bytes)
        except Exception:
            raw = b""
        final_url = str(getattr(exc, "url", None) or url)
    except Exception:
        return {
            "url": url,
            "final_url": url,
            "status_code": None,
            "content_type": "",
            "text": "",
        }

    if "text/html" not in content_type and "application/xhtml" not in content_type and "text/plain" not in content_type:
        return {
            "url": url,
            "final_url": final_url,
            "status_code": status_code,
            "content_type": content_type,
            "text": "",
        }

    charset = "utf-8"
    match = re.search(r"charset=([^;]+)", content_type, flags=re.I)
    if match:
        charset = match.group(1).strip()

    return {
        "url": url,
        "final_url": final_url,
        "status_code": status_code,
        "content_type": content_type,
        "text": raw.decode(charset, errors="replace"),
    }


def normalize_search_url(url):
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        query = urllib.parse.parse_qs(parsed.query)
        if query.get("uddg"):
            return query["uddg"][0]
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("/"):
        return ""
    return url


def domain_for_url(url):
    parsed = urllib.parse.urlparse(url)
    return (parsed.netloc or "").lower().removeprefix("www.")


def choose_first_keyword(text, mapping, fallback):
    lowered = text.lower()
    for label, keywords in mapping.items():
        if any(keyword in lowered for keyword in keywords):
            return label
    return fallback


def infer_organization_type(domain, text):
    if domain.endswith(".gov") or ".gov." in domain:
        return "public government site"
    if domain.endswith(".edu") or ".edu." in domain:
        return "public education site"
    if domain.endswith(".org") or ".org." in domain:
        return "public or non-profit organization site"
    lowered = text.lower()
    if any(term in lowered for term in ("manufacturer", "products", "catalog", "quote", "distributor")):
        return "private company site"
    return "unknown organization type"


def extract_symbol_formats(text):
    lowered = text.lower()
    formats = []
    for label, keywords in SYMBOL_FORMAT_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            formats.append(label)
    return formats


def score_source_candidate(text, formats):
    lowered = text.lower()
    score = 0.15
    for term in ("symbol", "symbols", "p&id", "piping", "instrumentation", "schematic", "cad", "standard"):
        if term in lowered:
            score += 0.08
    if formats:
        score += min(0.25, len(formats) * 0.05)
    if any(term in lowered for term in ("download", "library", "catalog", "catalogue")):
        score += 0.12
    return round(min(score, 0.98), 2)


def build_search_queries(seed_query, prior_sites):
    queries = []
    if seed_query:
        queries.append(seed_query)
    queries.extend(SOURCE_DISCOVERY_QUERY_SEEDS)
    for site in prior_sites[:3]:
        industry = site.get("industry")
        process = site.get("process")
        if industry or process:
            queries.append(f"{industry or ''} {process or ''} engineering symbols library".strip())
    seen = set()
    deduped = []
    for query in queries:
        normalized = " ".join(str(query).split()).lower()
        if normalized and normalized not in seen:
            deduped.append(query)
            seen.add(normalized)
    return deduped


def load_prior_source_memory(db_env_file, limit=25):
    try:
        bridge = RuntimePersistenceBridge(env_file=db_env_file)
        with bridge.session_scope() as session:
            column_rows = session.execute(
                text(
                    "select column_name from information_schema.columns "
                    "where table_name = 'scott_source_discovery_sites'"
                )
            ).mappings().all()
            columns = {row["column_name"] for row in column_rows}
            selected_columns = ["domain", "url", "title", "industry", "process", "status"]
            if "source_prompt" in columns:
                selected_columns.append("source_prompt")
            if "include_next_run" in columns:
                selected_columns.append("include_next_run")
            if "requires_auth" in columns:
                selected_columns.append("requires_auth")
            if "auth_status" in columns:
                selected_columns.append("auth_status")
            if "auth_secret_key" in columns:
                selected_columns.append("auth_secret_key")
            rows = session.execute(
                text(
                    f"select {', '.join(selected_columns)} from scott_source_discovery_sites "
                    "where lower(status) not in ('ignored', 'ignore') "
                    "order by last_seen_at desc limit :limit"
                ),
                {"limit": limit},
            ).mappings().all()
            return [dict(row) for row in rows]
    except Exception:
        return []


def discover_search_results(query):
    encoded = urllib.parse.urlencode({"q": query})
    search_response = fetch_text_url(f"https://duckduckgo.com/html/?{encoded}", timeout=20)
    html_text = search_response.get("text") or ""
    parser = SearchResultParser()
    parser.feed(html_text)
    results = []
    seen = set()
    for result in parser.results:
        url = normalize_search_url(result.get("url"))
        domain = domain_for_url(url)
        if not domain or domain in seen or domain.endswith("duckduckgo.com"):
            continue
        seen.add(domain)
        results.append({"url": url, "domain": domain, "search_title": result.get("title") or ""})
    return results


def prompted_source_targets(site):
    prompt = str(site.get("source_prompt") or "").strip()
    if not prompt:
        return []

    base_url = site.get("url") or ""
    base_domain = site.get("domain") or domain_for_url(base_url)
    targets = []
    seen = set()
    raw_targets = re.findall(r"https?://[^\s\"'<>]+|/[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]+", prompt)
    for raw_target in raw_targets:
        target = raw_target.rstrip(".,);]")
        if target.startswith("/"):
            if not base_url:
                continue
            target = urllib.parse.urljoin(base_url, target)
        target_domain = domain_for_url(target)
        if not target_domain or (base_domain and target_domain != base_domain):
            continue
        if target not in seen:
            targets.append(target)
            seen.add(target)
    return targets


def detect_auth_wall(url, final_url, status_code, body_text):
    lowered_body = (body_text or "").lower()

    status_requires_auth = status_code in {401, 403, 407}

    auth_url_markers = (
        "login",
        "log-in",
        "sign-in",
        "signin",
        "auth",
        "account",
        "register",
        "subscription",
    )
    original_url = url or ""
    resolved_url = final_url or original_url
    resolved_parts = urllib.parse.urlsplit(resolved_url)
    redirected = bool(resolved_url and original_url and resolved_url != original_url)
    # Treat auth-looking URL paths as a gate only when the fetch redirected there.
    # Some legitimate protected resources include words like "auth" in their own
    # path (for example /basic-auth/user/pass); after credentials succeed and the
    # final URL is unchanged, the URL marker alone should not force auth_failed.
    auth_redirect_target = " ".join(
        part for part in (resolved_parts.path, resolved_parts.query) if part
    ).lower()
    auth_redirect = redirected and any(marker in auth_redirect_target for marker in auth_url_markers)

    form_markers = (
        "type=\"password\"",
        "type='password'",
        "name=\"password\"",
        "name='password'",
    )
    has_password_form = any(marker in lowered_body for marker in form_markers)

    keyword_markers = (
        "member sign-in",
        "member login",
        "sign in to continue",
        "log in to continue",
        "subscription required",
        "create an account",
        "register to download",
        "login required",
    )
    keyword_hits = sum(1 for marker in keyword_markers if marker in lowered_body)

    requires_auth = status_requires_auth or auth_redirect or has_password_form or keyword_hits >= 2

    if status_requires_auth:
        reason = f"http_{status_code}"
    elif has_password_form:
        reason = "password_form"
    elif keyword_hits >= 2:
        reason = "login_keywords"
    elif auth_redirect:
        reason = "auth_redirect"
    else:
        reason = "none"

    return {
        "requires_auth": requires_auth,
        "reason": reason,
        "status_code": status_code,
        "keyword_hits": keyword_hits,
    }


def inspect_candidate_site(result, query, source_prompt=None, auth_secret_key=None, db_env_file=None):
    page_response = fetch_text_url(result["url"], timeout=12, auth_secret_key=auth_secret_key, db_env_file=db_env_file)
    page_text = page_response.get("text") or ""
    final_url = page_response.get("final_url") or result["url"]
    status_code = page_response.get("status_code")
    auth_detection = detect_auth_wall(result["url"], final_url, status_code, page_text)

    parser = PageSummaryParser()
    parser.feed(page_text)
    body_text = " ".join(parser.text_parts)
    title = parser.title or result.get("search_title") or result["domain"]
    description = parser.description or " ".join(body_text.split()[:35])
    combined = f"{title} {description} {body_text[:4000]}"
    formats = extract_symbol_formats(combined)
    score = score_source_candidate(combined, formats)
    prompt_available = bool(str(source_prompt or "").strip())
    status = "candidate" if score >= 0.35 else "low_signal"

    requires_auth = bool(auth_detection["requires_auth"])
    if auth_secret_key:
        if requires_auth:
            auth_status = "auth_failed"
        else:
            auth_status = "auth_verified"
            # Keep as requiring auth overall since it is a gated site
            requires_auth = True
    else:
        auth_status = "gated_detected" if requires_auth else "no_auth"

    return {
        "domain": result["domain"],
        "url": result["url"],
        "final_url": final_url,
        "title": title[:300],
        "description": description[:1000],
        "industry": choose_first_keyword(combined, INDUSTRY_KEYWORDS, "unknown"),
        "process": choose_first_keyword(combined, PROCESS_KEYWORDS, "source discovery"),
        "organization_type": infer_organization_type(result["domain"], combined),
        "symbol_formats": formats,
        "status": status,
        "requires_auth": requires_auth,
        "auth_status": auth_status,
        "auth_secret_key": auth_secret_key,
        "relevance_score": score,
        "evidence": {
            "matched_query": query,
            "search_title": result.get("search_title"),
            "source_prompt_available": prompt_available,
            "http_status": status_code,
            "auth_detection": {
                "requires_auth": requires_auth,
                "reason": auth_detection["reason"],
                "keyword_hits": auth_detection["keyword_hits"],
                "final_url": final_url,
            },
            "signals": {
                "mentions_symbol": "symbol" in combined.lower(),
                "mentions_standard": "standard" in combined.lower(),
                "mentions_download": "download" in combined.lower(),
                "mentions_cad": "cad" in combined.lower(),
            },
        },
    }


def run_source_discovery_task(task, db_env_file=None):
    started_monotonic = time.monotonic()
    duration_seconds = min(
        int(task.get("duration_seconds") or DEFAULT_SOURCE_DISCOVERY_DURATION_SECONDS),
        DEFAULT_SOURCE_DISCOVERY_DURATION_SECONDS,
    )
    deadline = started_monotonic + duration_seconds
    prior_sites = load_prior_source_memory(db_env_file)
    preferred_sites = task.get("preferred_sites") or []
    prior_by_domain = {site.get("domain"): site for site in prior_sites if site.get("domain")}
    for site in preferred_sites:
        domain = site.get("domain")
        if domain and domain not in prior_by_domain:
            prior_by_domain[domain] = site
    prior_sites = list(prior_by_domain.values())
    queries = build_search_queries(task.get("seed_query"), prior_sites)
    discovered_sites = []
    trace = []
    seen_domains = {site.get("domain") for site in prior_sites}
    included_sites = [
        site for site in prior_sites
        if bool(site.get("include_next_run")) and str(site.get("status") or "").strip().lower() not in {"ignored", "ignore"}
    ]

    for prior_site in included_sites:
        if time.monotonic() >= deadline:
            break
        urls = [prior_site.get("url"), *prompted_source_targets(prior_site)]
        for url in [target for target in urls if target]:
            if time.monotonic() >= deadline:
                break
            result = {
                "url": url,
                "domain": domain_for_url(url) or prior_site.get("domain"),
                "search_title": prior_site.get("title") or prior_site.get("domain"),
            }
            if not result["url"] or not result["domain"]:
                continue
            try:
                site = inspect_candidate_site(
                    result,
                    "checked source next run",
                    prior_site.get("source_prompt"),
                    auth_secret_key=prior_site.get("auth_secret_key"),
                    db_env_file=db_env_file
                )
                discovered_sites.append(site)
                seen_domains.add(result["domain"])
                add_trace(trace, "checked_site_inspection", "passed", f"{result['domain']}: score {site['relevance_score']}; prompt available: {bool(prior_site.get('source_prompt'))}.")
            except Exception as exc:
                add_trace(trace, "checked_site_inspection", "failed", f"{result['domain']}: {exc}")

    for query in queries:
        if time.monotonic() >= deadline:
            break
        try:
            results = discover_search_results(query)
            add_trace(trace, "search_query", "passed", f"{query}: {len(results)} candidate links.")
        except Exception as exc:
            add_trace(trace, "search_query", "failed", f"{query}: {exc}")
            continue

        for result in results:
            if time.monotonic() >= deadline:
                break
            if result["domain"] in seen_domains:
                continue
            try:
                site = inspect_candidate_site(result, query)
                discovered_sites.append(site)
                seen_domains.add(result["domain"])
                add_trace(trace, "site_inspection", "passed", f"{result['domain']}: score {site['relevance_score']}.")
            except Exception as exc:
                add_trace(trace, "site_inspection", "failed", f"{result['domain']}: {exc}")

    elapsed_seconds = int(time.monotonic() - started_monotonic)
    return {
        "queue_item_id": task.get("queue_item_id") or "untracked",
        "agent": "scott",
        "schema_version": SCHEMA_VERSION,
        "task_type": "source_discovery_search",
        "decision": "progress_saved",
        "duration_seconds": duration_seconds,
        "elapsed_seconds": elapsed_seconds,
        "queries": queries,
        "prior_memory_count": len(prior_sites),
        "new_site_count": len(discovered_sites),
        "sites": discovered_sites,
        "evidence_trace": trace,
    }


def load_json_file(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_env_file(env_path=None):
    path = Path(env_path) if env_path else DEFAULT_ENV_PATH
    env = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key] = value
    return path, env


def detect_postgres_driver():
    for module_name in ("psycopg", "psycopg2", "asyncpg", "pg8000"):
        if importlib.util.find_spec(module_name):
            return module_name
    return None


def parse_database_url(database_url):
    parsed = urllib.parse.urlparse(database_url)
    if parsed.scheme not in {"postgresql", "postgres"}:
        raise ValueError("Database URL must use a PostgreSQL scheme.")
    if not parsed.hostname:
        raise ValueError("Database URL is missing a hostname.")
    if not parsed.path or parsed.path == "/":
        raise ValueError("Database URL is missing a database name.")
    return parsed


def protocol_probe(parsed_url):
    params = (
        b"user\x00" + urllib.parse.unquote(parsed_url.username or "").encode("utf-8") +
        b"\x00database\x00" + parsed_url.path.lstrip("/").encode("utf-8") +
        b"\x00application_name\x00symgov-scott-healthcheck\x00" +
        b"client_encoding\x00UTF8\x00\x00"
    )
    startup = struct.pack("!I", len(params) + 8) + struct.pack("!I", 196608) + params

    with socket.create_connection((parsed_url.hostname, parsed_url.port or 5432), timeout=5) as conn:
        conn.sendall(startup)
        payload = conn.recv(4096)

    if len(payload) < 9:
        raise RuntimeError("PostgreSQL probe returned an unexpectedly short response.")

    message_type = chr(payload[0])
    result = {
        "message_type": message_type,
        "payload_length": struct.unpack("!I", payload[1:5])[0],
        "auth_code": None,
    }
    if message_type == "R":
        result["auth_code"] = struct.unpack("!I", payload[5:9])[0]
    return result


def query_with_driver(driver_name, database_url):
    if driver_name == "psycopg":
        import psycopg

        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("select current_user, current_database()")
                current_user, current_database = cur.fetchone()
        return {
            "query_ok": True,
            "current_user": current_user,
            "current_database": current_database,
        }

    if driver_name == "psycopg2":
        import psycopg2

        with psycopg2.connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("select current_user, current_database()")
                current_user, current_database = cur.fetchone()
        return {
            "query_ok": True,
            "current_user": current_user,
            "current_database": current_database,
        }

    if driver_name == "pg8000":
        import pg8000

        parsed = parse_database_url(database_url)
        conn = pg8000.connect(
            user=urllib.parse.unquote(parsed.username or ""),
            password=urllib.parse.unquote(parsed.password or ""),
            host=parsed.hostname,
            port=parsed.port or 5432,
            database=parsed.path.lstrip("/"),
        )
        try:
            cur = conn.cursor()
            cur.execute("select current_user, current_database()")
            current_user, current_database = cur.fetchone()
        finally:
            conn.close()
        return {
            "query_ok": True,
            "current_user": current_user,
            "current_database": current_database,
        }

    if driver_name == "asyncpg":
        return {
            "query_ok": False,
            "reason": "asyncpg_detected_but_sync_runner_does_not_open_event_loop",
        }

    return {
        "query_ok": False,
        "reason": "unsupported_driver",
    }


def health_check_database(env_path=None, migration=False):
    resolved_env_path, env = load_env_file(env_path)
    url_key = "SYMGOV_MIGRATION_DATABASE_URL" if migration else "SYMGOV_DATABASE_URL"
    if url_key not in env:
        raise ValueError(f"Missing required setting: {url_key}")

    parsed = parse_database_url(env[url_key])
    probe = protocol_probe(parsed)
    driver_name = detect_postgres_driver()
    result = {
        "env_path": str(resolved_env_path),
        "url_key": url_key,
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip("/"),
        "username": urllib.parse.unquote(parsed.username or ""),
        "network_ok": True,
        "postgres_protocol_ok": probe["message_type"] == "R",
        "auth_message_type": probe["message_type"],
        "auth_code": probe["auth_code"],
        "driver": driver_name,
        "query_ok": False,
    }

    if driver_name:
        result.update(query_with_driver(driver_name, env[url_key]))
    else:
        result["reason"] = "no_postgres_driver_installed"

    return result


def build_routing(decision, eligibility_status, guessed_format, queue_priority):
    recommendation = {
        "route_to_agents": [],
        "next_queue_families": [],
        "priority": queue_priority or "medium",
        "reason_codes": [],
        "human_follow_up_required": False,
    }

    if decision == "accepted" and eligibility_status == "eligible":
        if guessed_format == "zip":
            recommendation["reason_codes"].append("SCOTT-ROUTE-ZIP-PACKAGE")
            return recommendation
        recommendation["route_to_agents"] = ["tracy"]
        recommendation["next_queue_families"] = ["provenance"]
        recommendation["reason_codes"].append("SCOTT-ROUTE-ACCEPTED")
        if guessed_format in {"svg", "png", "jpeg", "dxf"}:
            recommendation["route_to_agents"].insert(0, "vlad")
            recommendation["next_queue_families"].insert(0, "validation")
            recommendation["reason_codes"].append(f"SCOTT-ROUTE-{guessed_format.upper()}")
        return recommendation

    if decision == "escalated":
        recommendation["human_follow_up_required"] = True
        recommendation["reason_codes"].append("SCOTT-ROUTE-REVIEW")
        return recommendation

    recommendation["reason_codes"].append("SCOTT-ROUTE-STOP")
    return recommendation


def run_intake_task(task):
    queue_item_id = task.get("queue_item_id") or "untracked"
    source_type = task.get("source_type")
    source_id = task.get("source_id")
    submission_kind = task.get("submission_kind")
    source_ref = task.get("source_ref")
    submitted_by = task.get("submitted_by")
    raw_input_path_raw = task.get("raw_input_path")
    original_filename = (task.get("original_filename") or "").strip()
    declared_format = task.get("declared_format")
    candidate_symbol_id = task.get("candidate_symbol_id")
    contributor_name = task.get("contributor_name")
    contributor_org = task.get("contributor_org")
    contributor_declaration = (task.get("contributor_declaration") or "").strip()
    standards_source_refs = ensure_list(task.get("standards_source_refs"))
    rights_documents = ensure_list(task.get("rights_documents"))
    evidence_links = ensure_list(task.get("evidence_links"))
    submitter_name = (task.get("submitter_name") or "").strip()
    submitter_email = (task.get("submitter_email") or "").strip()
    submission_batch_id = task.get("submission_batch_id")
    submission_batch_summary = (task.get("submission_batch_summary") or "").strip()
    file_note = (task.get("file_note") or "").strip()
    external_submitter_id = task.get("external_submitter_id")
    attachment_id = task.get("attachment_id")
    attachment_ids = ensure_list(task.get("attachment_ids"))
    raw_object_key = task.get("raw_object_key")
    visual_assets = task.get("visual_assets") if isinstance(task.get("visual_assets"), dict) else None
    companion_files = ensure_list(task.get("companion_files"))
    package_manifest = None
    package_child_tasks = []

    defects = []
    evidence_trace = []
    eligibility_flags = []

    raw_input_path = Path(raw_input_path_raw) if raw_input_path_raw else None
    suffix, guessed_format, normalized_declared = infer_format(raw_input_path_raw, declared_format)

    if not candidate_symbol_id and raw_input_path_raw:
        candidate_symbol_id = Path(raw_input_path_raw).stem.upper()

    effective_original_filename = original_filename or (Path(raw_input_path_raw).name if raw_input_path_raw else None)
    filename_inference = copy.deepcopy(task.get("filename_inference")) if isinstance(task.get("filename_inference"), dict) else infer_filename_metadata(effective_original_filename)
    candidate_title = task.get("candidate_title") or filename_inference.get("inferred_title")

    normalized_submission = {
        "source_type": source_type,
        "source_id": source_id,
        "submission_kind": submission_kind,
        "source_ref": source_ref,
        "submitted_by": submitted_by,
        "raw_input_path": raw_input_path_raw,
        "original_filename": effective_original_filename,
        "declared_format": normalized_declared,
        "candidate_symbol_id": candidate_symbol_id,
        "candidate_title": candidate_title,
        "filename_inference": filename_inference,
        "contributor_name": contributor_name,
        "contributor_org": contributor_org,
        "contributor_declaration": contributor_declaration,
        "source_notes": task.get("source_notes"),
        "import_batch_id": task.get("import_batch_id"),
        "submitter_name": submitter_name or None,
        "submitter_email": submitter_email or None,
        "submission_batch_id": submission_batch_id,
        "submission_batch_summary": submission_batch_summary or None,
        "file_note": file_note or None,
        "external_submitter_id": external_submitter_id,
        "attachment_id": attachment_id,
        "attachment_ids": attachment_ids,
        "raw_object_key": raw_object_key,
        "visual_assets": copy.deepcopy(visual_assets) if visual_assets else None,
        "companion_files": copy.deepcopy(companion_files),
        "standards_source_refs": standards_source_refs,
        "rights_documents": rights_documents,
        "evidence_links": evidence_links,
        "source_package_id": task.get("source_package_id"),
        "source_package_attachment_id": task.get("source_package_attachment_id"),
        "source_package_object_key": task.get("source_package_object_key"),
        "source_package_sha256": task.get("source_package_sha256"),
        "source_package_queue_item_id": task.get("source_package_queue_item_id"),
        "package_member": copy.deepcopy(task.get("package_member")) if isinstance(task.get("package_member"), dict) else None,
        "package_member_relationship": task.get("package_member_relationship"),
        "package_symbol_grouping": task.get("package_symbol_grouping"),
    }
    extracted_metadata = {
        "file_name": Path(raw_input_path_raw).name if raw_input_path_raw else None,
        "original_filename": effective_original_filename,
        "file_extension": suffix or None,
        "guessed_format": guessed_format,
        "candidate_symbol_id": candidate_symbol_id,
        "candidate_title": candidate_title,
        "filename_inference": filename_inference,
        "contributor_name": contributor_name,
        "contributor_org": contributor_org,
        "import_batch_id": task.get("import_batch_id"),
        "submitter_name": submitter_name or None,
        "submitter_email": submitter_email or None,
        "submission_batch_id": submission_batch_id,
        "attachment_count": len(attachment_ids) if attachment_ids else (1 if attachment_id else 0),
    }

    decision = "accepted"
    confidence = 0.94
    escalation_target = "none"
    eligibility_status = "eligible"

    required_fields = {
        "submission_kind": submission_kind,
        "source_ref": source_ref,
        "submitted_by": submitted_by,
        "raw_input_path": raw_input_path_raw,
    }
    missing_fields = [name for name, value in required_fields.items() if not value]
    if missing_fields:
        add_defect(defects, "SCOTT-TASK-001", "high", f"Missing required fields: {', '.join(missing_fields)}.")
        add_trace(evidence_trace, "task_fields", "failed", "Task payload is missing required intake fields.")
        decision = "escalated"
        confidence = 0.25
        escalation_target = "human_reviewer"
        eligibility_status = "needs_review"
        eligibility_flags.append("missing_required_fields")

    if submission_kind and submission_kind not in SUPPORTED_SUBMISSION_KINDS:
        add_defect(defects, "SCOTT-TASK-002", "high", f"Unsupported submission_kind: {submission_kind}")
        add_trace(evidence_trace, "submission_kind", "failed", "Submission kind is outside the first local contract.")
        decision = "escalated"
        confidence = min(confidence, 0.3)
        escalation_target = "human_reviewer"
        eligibility_status = "needs_review"
        eligibility_flags.append("unsupported_submission_kind")
    else:
        add_trace(evidence_trace, "submission_kind", "passed", f"Submission kind {submission_kind} is supported.")

    if not raw_input_path_raw:
        pass
    elif not raw_input_path.exists():
        add_defect(defects, "SCOTT-INTEGRITY-001", "high", f"Raw input path does not exist: {raw_input_path_raw}")
        add_trace(evidence_trace, "input_path", "failed", "Raw input path could not be resolved on disk.")
        decision = "escalated"
        confidence = min(confidence, 0.35)
        escalation_target = "human_reviewer"
        eligibility_status = "needs_review"
        eligibility_flags.append("missing_input_file")
    elif raw_input_path.stat().st_size == 0:
        add_defect(defects, "SCOTT-INTEGRITY-002", "critical", "Raw input file is empty.")
        add_trace(evidence_trace, "input_path", "failed", "Resolved raw input file has zero bytes.")
        decision = "rejected"
        confidence = 0.99
        eligibility_status = "ineligible"
        eligibility_flags.append("empty_input")
    else:
        extracted_metadata["file_size_bytes"] = raw_input_path.stat().st_size
        add_trace(evidence_trace, "input_path", "passed", f"Resolved raw input file with {raw_input_path.stat().st_size} bytes.")

    if raw_input_path_raw:
        if not guessed_format:
            add_defect(defects, "SCOTT-FORMAT-001", "high", f"Unsupported file extension: {suffix or 'none'}")
            add_trace(evidence_trace, "format", "failed", "Could not infer a supported format from the input path.")
            decision = "rejected"
            confidence = min(confidence, 0.9)
            eligibility_status = "ineligible"
            eligibility_flags.append("unsupported_format")
        else:
            add_trace(evidence_trace, "format", "passed", f"Inferred supported format {guessed_format}.")

    if guessed_format and normalized_declared and guessed_format != normalized_declared:
        add_defect(
            defects,
            "SCOTT-FORMAT-002",
            "medium",
            f"Declared format {normalized_declared} does not match inferred format {guessed_format}.",
        )
        add_trace(evidence_trace, "declared_format", "failed", "Declared format did not match the file extension.")
        confidence = min(confidence, 0.82)
        eligibility_flags.append("declared_format_mismatch")
    elif guessed_format:
        add_trace(evidence_trace, "declared_format", "passed", "Declared format matches or was not required.")

    if guessed_format == "json" and raw_input_path and raw_input_path.exists():
        try:
            payload = load_json_file(raw_input_path)
            extracted_metadata["json_top_level_keys"] = sorted(payload.keys()) if isinstance(payload, dict) else []
            extracted_metadata["library_symbol_count"] = len(payload.get("symbols", [])) if isinstance(payload, dict) else 0
            add_trace(evidence_trace, "json_parse", "passed", "Parsed JSON intake payload successfully.")
        except (OSError, json.JSONDecodeError) as exc:
            add_defect(defects, "SCOTT-FORMAT-003", "high", f"JSON intake payload could not be parsed: {exc}")
            add_trace(evidence_trace, "json_parse", "failed", "JSON parse failed.")
            decision = "rejected"
            confidence = 0.97
            eligibility_status = "ineligible"
            eligibility_flags.append("unparseable_json")

    if guessed_format in {"png", "jpeg"} and raw_input_path and raw_input_path.exists():
        extracted_metadata["raster_review_required"] = True
        add_trace(
            evidence_trace,
            "raster_intake",
            "passed",
            f"{guessed_format.upper()} intake accepted for downstream raster sheet analysis by Vlad.",
        )

    if guessed_format == "zip" and raw_input_path and raw_input_path.exists() and decision == "accepted":
        package_manifest, package_child_tasks, package_errors = expand_zip_package(task, raw_input_path, queue_item_id)
        extracted_metadata["zip_member_count"] = len(package_manifest.get("members", [])) if package_manifest else 0
        extracted_metadata["zip_supported_member_count"] = len(package_child_tasks)
        if package_errors:
            if "no_supported_members" in package_errors:
                add_defect(defects, "SCOTT-ZIP-003", "high", "ZIP package did not contain supported member files.")
                eligibility_flags.append("no_supported_zip_members")
            elif "bad_zip_file" in package_errors:
                add_defect(defects, "SCOTT-ZIP-001", "critical", "ZIP package could not be opened as a valid archive.")
                eligibility_flags.append("bad_zip_file")
            else:
                add_defect(defects, "SCOTT-ZIP-002", "critical", f"ZIP package failed safety checks: {', '.join(package_errors)}.")
                eligibility_flags.append("unsafe_zip_member")
            add_trace(evidence_trace, "zip_package_expansion", "failed", f"ZIP expansion stopped: {', '.join(package_errors)}.")
            decision = "rejected"
            confidence = 0.98
            eligibility_status = "ineligible"
        else:
            add_trace(
                evidence_trace,
                "zip_package_expansion",
                "passed",
                f"Safely expanded ZIP package into {len(package_child_tasks)} supported member task(s).",
            )
            eligibility_flags.append("zip_package_expanded")

    if submission_kind == "contributor_submission" and not contributor_declaration:
        add_defect(defects, "SCOTT-ELIG-001", "high", "Contributor submissions require a contributor_declaration.")
        add_trace(evidence_trace, "declaration", "failed", "Contributor submission did not include a declaration.")
        decision = "escalated"
        confidence = min(confidence, 0.4)
        escalation_target = "human_reviewer"
        eligibility_status = "needs_review"
        eligibility_flags.append("missing_contributor_declaration")
    else:
        add_trace(evidence_trace, "declaration", "passed", "Declaration completeness satisfied the first local contract.")

    if guessed_format == "svg" and decision == "accepted":
        eligibility_flags.append("technical_validation_candidate")
    if guessed_format == "dxf" and decision == "accepted":
        eligibility_flags.append("dxf_validation_candidate")
    if guessed_format in {"png", "jpeg"} and decision == "accepted":
        eligibility_flags.append("raster_sheet_analysis_candidate")
    if submission_kind == "imported_symbol_library" and guessed_format == "json" and decision == "accepted":
        eligibility_flags.append("batch_library_candidate")

    if decision == "accepted" and eligibility_status == "eligible":
        add_trace(evidence_trace, "eligibility", "passed", "Intake is accepted and eligible for downstream review.")
    elif decision == "rejected":
        add_trace(evidence_trace, "eligibility", "failed", "Intake is ineligible for downstream automation.")
    else:
        add_trace(evidence_trace, "eligibility", "failed", "Intake requires human follow-up before downstream routing.")

    routing_recommendation = build_routing(
        decision,
        eligibility_status,
        guessed_format,
        task.get("priority"),
    )

    return {
        "queue_item_id": queue_item_id,
        "agent": "scott",
        "schema_version": SCHEMA_VERSION,
        "decision": decision,
        "confidence": round(confidence, 2),
        "escalation_target": escalation_target,
        "normalized_submission": normalized_submission,
        "extracted_metadata": extracted_metadata,
        "eligibility_status": eligibility_status,
        "eligibility_flags": eligibility_flags,
        "routing_recommendation": routing_recommendation,
        "defects": defects,
        "evidence_trace": evidence_trace,
        "package_manifest": package_manifest,
        "package_child_tasks": package_child_tasks,
    }


def queue_item_payload_to_task(queue_item):
    payload = copy.deepcopy(queue_item.get("payload_json") or {})
    payload["queue_item_id"] = queue_item.get("id")
    payload["source_type"] = queue_item.get("source_type")
    payload["source_id"] = queue_item.get("source_id")
    payload["priority"] = queue_item.get("priority")
    return payload


def process_queue_item(queue_item_path, runtime_root, persist_db=False, db_env_file=None):
    queue_item_path = Path(queue_item_path)
    runtime_root = Path(runtime_root)

    with queue_item_path.open("r", encoding="utf-8") as handle:
        queue_item = json.load(handle)

    if queue_item.get("agent_id") != "scott":
        raise ValueError("Queue item agent_id must be 'scott'.")

    started_at = utc_now()
    is_source_discovery = (queue_item.get("payload_json") or {}).get("task_type") == "source_discovery_search"
    queue_item["status"] = "searching" if is_source_discovery else "running"
    queue_item["started_at"] = started_at
    write_json(queue_item_path, queue_item)
    notification_status = {
        "started": send_agent_status_update("scott", "started", queue_item),
        "completed": None,
    }

    task = queue_item_payload_to_task(queue_item)
    artifact = run_source_discovery_task(task, db_env_file=db_env_file) if is_source_discovery else run_intake_task(task)
    completed_at = utc_now()

    queue_item["status"] = "progress_saved" if is_source_discovery else queue_status_for_decision(artifact["decision"])
    if is_source_discovery:
        queue_item["confidence"] = None
        queue_item["escalation_reason"] = None
        queue_item["payload_json"] = {
            **(queue_item.get("payload_json") or {}),
            "new_site_count": artifact["new_site_count"],
            "prior_memory_count": artifact["prior_memory_count"],
            "elapsed_seconds": artifact["elapsed_seconds"],
        }
    else:
        queue_item["confidence"] = artifact["confidence"]
        queue_item["escalation_reason"] = (
            "intake_requires_escalation" if artifact["decision"] == "escalated" else None
        )
    queue_item["completed_at"] = completed_at
    write_json(queue_item_path, queue_item)

    package_child_queue_item_paths = []
    if not is_source_discovery and artifact.get("package_child_tasks"):
        child_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        member_lookup = {
            member.get("member_id"): member
            for member in (artifact.get("package_manifest") or {}).get("members", [])
            if isinstance(member, dict)
        }
        for child_task in artifact.get("package_child_tasks") or []:
            package_member = child_task.get("package_member") or {}
            member_id = package_member.get("member_id") or f"member-{len(package_child_queue_item_paths) + 1:04d}"
            child_queue_id = f"aqi-scott-{safe_package_token(member_id)}-{child_timestamp}"
            child_queue_item = {
                "id": child_queue_id,
                "agent_id": "scott",
                "source_type": "zip_package_member",
                "source_id": child_task.get("source_package_id"),
                "status": "queued",
                "priority": queue_item.get("priority") or "medium",
                "payload_json": child_task,
                "confidence": None,
                "escalation_reason": None,
                "created_at": completed_at,
                "started_at": None,
                "completed_at": None,
            }
            child_path = runtime_root / "agent_queue_items" / f"{child_queue_id}.json"
            write_json(child_path, child_queue_item)
            package_child_queue_item_paths.append(str(child_path))
            if member_id in member_lookup:
                member_lookup[member_id].setdefault("downstream_queue_ids", []).append(child_queue_id)

    run_id = stamp_id("arun", queue_item["id"])
    run_record = {
        "id": run_id,
        "queue_item_id": queue_item["id"],
        "model": "ollama/gemma4:e4b",
        "prompt_version": SOURCE_DISCOVERY_PROMPT_VERSION if is_source_discovery else PROMPT_VERSION,
        "tool_trace_json": artifact["evidence_trace"],
        "result_status": queue_item["status"],
        "started_at": started_at,
        "completed_at": completed_at,
    }

    artifact_id = stamp_id("aout", queue_item["id"])
    output_artifact_record = {
        "id": artifact_id,
        "queue_item_id": queue_item["id"],
        "artifact_type": "scott_source_discovery" if is_source_discovery else "intake_record",
        "schema_version": artifact["schema_version"],
        "payload_json": artifact,
        "created_at": completed_at,
    }

    if is_source_discovery:
        record_id = stamp_id("sd", queue_item["id"])
        durable_record = {
            "id": record_id,
            "queue_item_id": queue_item["id"],
            "sites": artifact["sites"],
            "report_json": artifact,
            "completed_at": completed_at,
        }
        durable_kind = "scott_source_discovery"
        durable_record_path = runtime_root / "source_discovery_reports" / f"{record_id}.json"
    else:
        record_id = stamp_id("ir", queue_item["id"])
        durable_record = {
            "id": record_id,
            "queue_item_id": queue_item["id"],
            "source_type": queue_item.get("source_type"),
            "source_ref": artifact["normalized_submission"].get("source_ref"),
            "submitter": artifact["normalized_submission"].get("submitted_by"),
            "submission_kind": artifact["normalized_submission"].get("submission_kind"),
            "intake_status": artifact["decision"],
            "eligibility_status": artifact["eligibility_status"],
            "normalized_submission_json": artifact["normalized_submission"],
            "routing_recommendation_json": artifact["routing_recommendation"],
            "raw_object_key": artifact["normalized_submission"].get("raw_object_key"),
            "report_json": {
                "decision": artifact["decision"],
                "confidence": artifact["confidence"],
                "escalation_target": artifact["escalation_target"],
                "extracted_metadata": artifact["extracted_metadata"],
                "eligibility_flags": artifact["eligibility_flags"],
                "defects": artifact["defects"],
                "evidence_trace": artifact["evidence_trace"],
            },
            "created_at": completed_at,
        }
        durable_kind = "intake_record"
        durable_record_path = runtime_root / "intake_records" / f"{record_id}.json"

    write_json(runtime_root / "agent_runs" / f"{run_id}.json", run_record)
    write_json(runtime_root / "agent_output_artifacts" / f"{artifact_id}.json", output_artifact_record)
    write_json(durable_record_path, durable_record)

    db_persistence = None
    if persist_db or env_flag("SYMGOV_PERSIST_TO_DB"):
        bridge = RuntimePersistenceBridge(env_file=db_env_file)
        db_persistence = bridge.persist_agent_execution(
            queue_item=queue_item,
            run_record=run_record,
            output_artifact_record=output_artifact_record,
            durable_record=durable_record,
            durable_kind=durable_kind,
        )

    notification_status["completed"] = send_agent_status_update(
        "scott",
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
        "durable_record_path": str(durable_record_path),
        "intake_record_path": None if is_source_discovery else str(durable_record_path),
        "db_persistence": db_persistence,
        "notifications": notification_status,
        "artifact": artifact,
        "package_child_queue_item_paths": package_child_queue_item_paths,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Run local Scott intake processing in task or queue mode.")
    parser.add_argument("--input", help="Path to a JSON task file.")
    parser.add_argument("--output", help="Path to write the JSON intake artifact.")
    parser.add_argument("--queue-item", help="Path to an agent_queue_item JSON record.")
    parser.add_argument("--runtime-root", help="Root directory for local file-backed queue records.")
    parser.add_argument(
        "--cleanup-queue-item",
        action="store_true",
        help="Remove the specified queue item from this agent's runtime/agent_queue_items directory.",
    )
    parser.add_argument(
        "--db-health-check",
        action="store_true",
        help="Load the shared Symgov database env file and report connectivity or query health.",
    )
    parser.add_argument(
        "--db-env-file",
        default=str(DEFAULT_ENV_PATH),
        help="Path to the Symgov database env file used by --db-health-check.",
    )
    parser.add_argument(
        "--migration-db",
        action="store_true",
        help="Use SYMGOV_MIGRATION_DATABASE_URL instead of SYMGOV_DATABASE_URL with --db-health-check.",
    )
    parser.add_argument(
        "--persist-db",
        action="store_true",
        help="Also mirror queue, run, artifact, and intake records into the Symgov database.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.db_health_check:
        print(json.dumps(health_check_database(args.db_env_file, migration=args.migration_db), indent=2))
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
        )
        print(json.dumps(result, indent=2))
        return

    if not args.input or not args.output:
        raise SystemExit("--input and --output are required when not using --queue-item.")

    input_path = Path(args.input)
    output_path = Path(args.output)
    with input_path.open("r", encoding="utf-8") as handle:
        task = json.load(handle)

    artifact = run_intake_task(task)
    write_json(output_path, artifact)


if __name__ == "__main__":
    main()
