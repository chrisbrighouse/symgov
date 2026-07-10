#!/usr/bin/env python3
import argparse
import base64
import csv
import copy
import hashlib
import html
import io
import json
import os
import shutil
import subprocess
import sys
import struct
import tempfile
import urllib.error
import urllib.request
import zlib
import binascii
import uuid
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET


SCHEMA_VERSION = "0.2.0"
PROMPT_VERSION = "vlad-local-contract-0.2.0"
DEFAULT_VLAD_MODEL = "ollama/gemma4:e4b"
DEFAULT_VLAD_GEMINI_MODEL = "gemini/gemini-2.5-flash"
BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.runtime import RuntimePersistenceBridge, env_flag
from symgov_backend.notifications import send_agent_status_update
from symgov_backend.services.btx_converter import BtxConversionError, convert_btx

try:
    from PIL import Image, ImageDraw, ImageOps
except ImportError:  # pragma: no cover
    Image = None
    ImageDraw = None
    ImageOps = None

try:
    import ezdxf
    from ezdxf import recover as ezdxf_recover
except ImportError:  # pragma: no cover
    ezdxf = None
    ezdxf_recover = None


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_gemini_api_key():
    return (os.environ.get("SYMGOV_GEMINI_API_KEY", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip())


def resolve_vlad_model():
    """Return the model Vlad should report/use for non-deterministic assistance."""
    configured = os.environ.get("SYMGOV_VLAD_MODEL", "").strip()
    if configured:
        return configured
    if get_gemini_api_key():
        return os.environ.get("SYMGOV_GEMINI_MODEL", DEFAULT_VLAD_GEMINI_MODEL).strip() or DEFAULT_VLAD_GEMINI_MODEL
    return DEFAULT_VLAD_MODEL


def stamp_id(prefix, base_id):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{base_id}-{timestamp}"


def add_trace(trace, check, status, detail):
    trace.append({"check": check, "status": status, "detail": detail})


def new_persistence_boundary(*, queue_item_id, report_id, expected_derivatives, expected_children, expected_libby):
    return {
        "correlation_id": f"pb-{uuid.uuid4().hex[:16]}",
        "queue_item_id": queue_item_id,
        "report_id": report_id,
        "expected_derivative_count": expected_derivatives,
        "expected_child_count": expected_children,
        "expected_libby_queue_count": expected_libby,
        "actual_derivative_count": 0,
        "actual_child_count": 0,
        "actual_libby_queue_count": 0,
        "events": [],
    }


def add_persistence_event(boundary, phase, status, detail, **extra):
    boundary["events"].append({"phase": phase, "status": status, "detail": detail, **extra})


def add_defect(defects, code, severity, detail):
    defects.append({"code": code, "severity": severity, "detail": detail})


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
        "message": "Queue item removed from Vlad runtime queue.",
    }


def local_name(tag):
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def infer_asset_format(asset_path_raw, declared_format):
    normalized_declared = (declared_format or "").strip().lower()
    if normalized_declared in {"svg", "png", "jpeg", "json", "dxf", "btx"}:
        return normalized_declared
    if asset_path_raw:
        suffix = Path(asset_path_raw).suffix.lstrip(".").lower()
        if suffix == "jpg":
            return "jpeg"
        return suffix
    return normalized_declared or None


def extract_dxf_metadata(doc, auditor):
    modelspace = doc.modelspace()
    entity_counts = {}
    layer_names = set()
    external_reference_entities = []

    for entity in modelspace:
        entity_type = entity.dxftype()
        entity_counts[entity_type] = entity_counts.get(entity_type, 0) + 1
        layer = getattr(entity.dxf, "layer", None)
        if layer:
            layer_names.add(layer)
        if entity_type in {"IMAGE", "UNDERLAY", "PDFUNDERLAY", "DWFUNDERLAY", "DGNUNDERLAY", "XREF"}:
            external_reference_entities.append(entity_type)

    block_names = sorted(
        block.name
        for block in doc.blocks
        if not block.name.startswith("*")
    )
    layout_names = sorted(layout.name for layout in doc.layouts)
    layer_table_names = sorted(layer.dxf.name for layer in doc.layers)
    all_layer_names = sorted(set(layer_table_names) | layer_names)

    audit_errors = [str(error) for error in getattr(auditor, "errors", [])]
    audit_fixes = [str(fix) for fix in getattr(auditor, "fixes", [])]

    return {
        "dxf_version": getattr(doc, "dxfversion", None),
        "units": doc.header.get("$INSUNITS", 0),
        "entity_counts": dict(sorted(entity_counts.items())),
        "modelspace_entity_count": sum(entity_counts.values()),
        "layer_names": all_layer_names,
        "layer_count": len(all_layer_names),
        "block_names": block_names,
        "block_count": len(block_names),
        "layout_names": layout_names,
        "layout_count": len(layout_names),
        "audit_error_count": len(audit_errors),
        "audit_fix_count": len(audit_fixes),
        "audit_errors": audit_errors,
        "audit_fixes": audit_fixes,
        "risk_flags": {
            "external_reference_entities": sorted(set(external_reference_entities)),
        },
    }


def dxf_source_asset_entry(asset_path, raw_object_key=None):
    return {
        "object_key": raw_object_key or f"raw-submissions/{asset_path.name}",
        "filename": asset_path.name,
        "content_type": "application/dxf",
        "format": "dxf",
        "role": "source",
        "downloadable": True,
    }


def dxf_derivative_asset_entry(queue_item_id, svg_path):
    return {
        "object_key": f"dxf-derivatives/{queue_item_id}/{svg_path.name}",
        "filename": svg_path.name,
        "content_type": "image/svg+xml",
        "format": "svg",
        "role": "generated_preview",
        "downloadable": False,
    }


def create_dxf_svg_derivative(doc, metadata, asset_path, runtime_root, queue_item_id, candidate_title=None, raw_object_key=None):
    output_root = Path(runtime_root) if runtime_root else asset_path.parent
    derivative_dir = output_root / "dxf_derivatives" / queue_item_id
    derivative_dir.mkdir(parents=True, exist_ok=True)
    svg_path = derivative_dir / f"{asset_path.stem}.svg"
    manifest_path = derivative_dir / f"{asset_path.stem}.manifest.json"
    source_asset = dxf_source_asset_entry(asset_path, raw_object_key)
    derivative_asset = dxf_derivative_asset_entry(queue_item_id, svg_path)
    title = candidate_title or asset_path.stem
    title_id = "dxf-title"
    desc_id = "dxf-desc"

    # Phase 1 keeps the derivative deliberately conservative: it is an accessible
    # technical preview/manifest carrier, while exact CAD rendering can be refined
    # behind this same artifact contract later.
    entity_summary = ", ".join(
        f"{count} {kind}"
        for kind, count in metadata.get("entity_counts", {}).items()
    ) or "no modelspace entities"
    escaped_title = html.escape(title)
    escaped_desc = html.escape(f"DXF technical preview generated by Vlad; contains {entity_summary}.")
    svg_text = f'''<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512" role="img" aria-labelledby="{title_id} {desc_id}">
  <title id="{title_id}">{escaped_title}</title>
  <desc id="{desc_id}">{escaped_desc}</desc>
  <rect x="1" y="1" width="510" height="510" fill="white" stroke="#1f2937" stroke-width="2"/>
  <text x="32" y="64" font-family="monospace" font-size="22" fill="#111827">DXF source accepted</text>
  <text x="32" y="104" font-family="monospace" font-size="16" fill="#374151">{html.escape(entity_summary)}</text>
</svg>
'''
    svg_path.write_text(svg_text, encoding="utf-8")
    manifest = {
        "source_asset_path": str(asset_path),
        "svg_path": str(svg_path),
        "metadata_path": str(manifest_path),
        "object_key": derivative_asset["object_key"],
        "filename": derivative_asset["filename"],
        "content_type": derivative_asset["content_type"],
        "format": derivative_asset["format"],
        "role": derivative_asset["role"],
        "downloadable": derivative_asset["downloadable"],
        "source_asset": source_asset,
        "preview_asset": derivative_asset,
        "visual_assets": {
            "preview": derivative_asset,
            "source_assets": [source_asset],
            "derivatives": [derivative_asset],
        },
        "metadata": metadata,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def persist_dxf_derivative_assets(bridge, *, report_id, derivative_manifest, storage_env_file=None):
    svg_path = Path(derivative_manifest["svg_path"])
    object_key = derivative_manifest["object_key"]
    content_type = derivative_manifest.get("content_type") or "image/svg+xml"
    attachment = bridge.create_attachment(
        parent_type="validation_report",
        parent_id=report_id,
        filename=derivative_manifest.get("filename") or svg_path.name,
        object_key=object_key,
        content_type=content_type,
        size_bytes=svg_path.stat().st_size,
        sha256=sha256_file(svg_path),
    )
    storage_result = bridge.upload_file(
        object_key=object_key,
        path=str(svg_path),
        content_type=content_type,
        env_file=storage_env_file,
    )
    derivative_manifest["attachment_id"] = attachment["id"]
    derivative_manifest["attachment_object_key"] = attachment["object_key"]
    derivative_manifest["attachment_storage"] = storage_result
    preview_asset = derivative_manifest.get("preview_asset") or {}
    if isinstance(preview_asset, dict):
        preview_asset["attachment_id"] = attachment["id"]
        preview_asset["attachment_object_key"] = attachment["object_key"]
    visual_assets = derivative_manifest.get("visual_assets") or {}
    if isinstance(visual_assets, dict):
        preview = visual_assets.get("preview")
        if isinstance(preview, dict):
            preview["attachment_id"] = attachment["id"]
            preview["attachment_object_key"] = attachment["object_key"]
        derivatives = visual_assets.get("derivatives")
        if isinstance(derivatives, list):
            for derivative in derivatives:
                if isinstance(derivative, dict) and derivative.get("object_key") == object_key:
                    derivative["attachment_id"] = attachment["id"]
                    derivative["attachment_object_key"] = attachment["object_key"]
    return attachment


def normalize_raster_asset_for_analysis(asset_path, asset_format):
    if asset_format == "png":
        return asset_path, None
    if asset_format != "jpeg":
        raise ValueError(f"Unsupported raster asset format for sheet analysis: {asset_format}")
    if Image is None:
        raise ValueError("Pillow is required to normalize JPEG inputs for raster sheet analysis.")

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    with Image.open(asset_path) as image:
        image.convert("RGBA").save(tmp_path, format="PNG")
    return tmp_path, tmp_path


def scan_duplicates(asset_path, compare_root):
    matches = []
    if not compare_root:
        return matches

    root = Path(compare_root)
    if not root.exists() or not root.is_dir():
        return matches

    asset_hash = sha256_file(asset_path)
    for candidate in root.rglob("*.svg"):
        if candidate.resolve() == asset_path.resolve():
            continue
        try:
            if sha256_file(candidate) == asset_hash:
                matches.append(str(candidate))
        except OSError:
            continue
    return matches


def read_png_chunks(path):
    with path.open("rb") as handle:
        signature = handle.read(8)
        if signature != b"\x89PNG\r\n\x1a\n":
            raise ValueError("File does not have a valid PNG signature.")

        chunks = []
        while True:
            header = handle.read(8)
            if len(header) == 0:
                break
            if len(header) != 8:
                raise ValueError("PNG ended mid-chunk header.")
            length, chunk_type = struct.unpack(">I4s", header)
            data = handle.read(length)
            crc = handle.read(4)
            if len(data) != length or len(crc) != 4:
                raise ValueError(f"PNG chunk {chunk_type.decode('ascii', errors='replace')} truncated.")
            chunks.append((chunk_type, data))
            if chunk_type == b"IEND":
                break
        return chunks


def png_chunk(chunk_type, data):
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", binascii.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def paeth_predictor(left, up, up_left):
    predictor = left + up - up_left
    left_delta = abs(predictor - left)
    up_delta = abs(predictor - up)
    up_left_delta = abs(predictor - up_left)
    if left_delta <= up_delta and left_delta <= up_left_delta:
        return left
    if up_delta <= up_left_delta:
        return up
    return up_left


def bytes_per_pixel(color_type, bit_depth):
    if bit_depth != 8:
        raise ValueError(f"Unsupported PNG bit depth for raster sheet analysis: {bit_depth}")
    samples_per_pixel = {
        0: 1,
        2: 3,
        3: 1,
        4: 2,
        6: 4,
    }.get(color_type)
    if samples_per_pixel is None:
        raise ValueError(f"Unsupported PNG color type for raster sheet analysis: {color_type}")
    return samples_per_pixel


def unfilter_scanlines(raw_bytes, width, height, pixel_stride):
    stride = width * pixel_stride
    expected = height * (stride + 1)
    if len(raw_bytes) != expected:
        raise ValueError(
            f"PNG decompressed payload length mismatch: expected {expected} bytes, got {len(raw_bytes)}."
        )

    rows = []
    offset = 0
    previous = bytearray(stride)
    for _ in range(height):
        filter_type = raw_bytes[offset]
        offset += 1
        filtered = bytearray(raw_bytes[offset:offset + stride])
        offset += stride
        row = bytearray(stride)

        for index in range(stride):
            left = row[index - pixel_stride] if index >= pixel_stride else 0
            up = previous[index]
            up_left = previous[index - pixel_stride] if index >= pixel_stride else 0

            if filter_type == 0:
                value = filtered[index]
            elif filter_type == 1:
                value = (filtered[index] + left) & 0xFF
            elif filter_type == 2:
                value = (filtered[index] + up) & 0xFF
            elif filter_type == 3:
                value = (filtered[index] + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                value = (filtered[index] + paeth_predictor(left, up, up_left)) & 0xFF
            else:
                raise ValueError(f"Unsupported PNG filter type: {filter_type}")

            row[index] = value

        rows.append(bytes(row))
        previous = row

    return rows


def decode_png_rows(asset_path):
    chunks = read_png_chunks(asset_path)
    ihdr = next((data for chunk_type, data in chunks if chunk_type == b"IHDR"), None)
    if ihdr is None:
        raise ValueError("PNG is missing IHDR.")

    width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(">IIBBBBB", ihdr)
    if compression != 0 or filter_method != 0:
        raise ValueError("PNG uses unsupported compression or filter metadata.")
    if interlace != 0:
        raise ValueError("Interlaced PNGs are not supported in the first raster sheet-analysis slice.")

    palette = next((data for chunk_type, data in chunks if chunk_type == b"PLTE"), None)
    transparency = next((data for chunk_type, data in chunks if chunk_type == b"tRNS"), None)
    idat = b"".join(data for chunk_type, data in chunks if chunk_type == b"IDAT")
    if not idat:
        raise ValueError("PNG is missing IDAT data.")

    pixel_stride = bytes_per_pixel(color_type, bit_depth)
    decompressed = zlib.decompress(idat)
    rows = unfilter_scanlines(decompressed, width, height, pixel_stride)
    return {
        "width": width,
        "height": height,
        "bit_depth": bit_depth,
        "color_type": color_type,
        "palette": palette,
        "transparency": transparency,
        "rows": rows,
    }


def crop_png_to_rgba_rows(decoded_png, region):
    rows = []
    start_x = region["x"]
    start_y = region["y"]
    width = region["width"]
    height = region["height"]
    for y in range(start_y, start_y + height):
        row = bytearray()
        for x in range(start_x, start_x + width):
            row.extend(png_pixel_rgba(decoded_png, x, y))
        rows.append(bytes(row))
    return rows


def encode_rgba_png(width, height, rows):
    raw = bytearray()
    for row in rows:
        raw.append(0)
        raw.extend(row)
    compressed = zlib.compress(bytes(raw))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            png_chunk(b"IHDR", ihdr),
            png_chunk(b"IDAT", compressed),
            png_chunk(b"IEND", b""),
        ]
    )


def expand_region(region, image_width, image_height, padding):
    min_x = max(0, region["x"] - padding)
    min_y = max(0, region["y"] - padding)
    max_x = min(image_width, region["x"] + region["width"] + padding)
    max_y = min(image_height, region["y"] + region["height"] + padding)
    return {
        "x": min_x,
        "y": min_y,
        "width": max_x - min_x,
        "height": max_y - min_y,
        "pixel_area": region["pixel_area"],
        "confidence": region["confidence"],
        "padding_px": padding,
    }


def slugify_token(value):
    normalized = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    collapsed = "-".join(part for part in normalized.split("-") if part)
    return collapsed or "symbol"


def proposed_child_symbol_fields(asset_path, region_index):
    stem = slugify_token(Path(asset_path).stem)
    symbol_slug = f"{stem}-region-{region_index:02d}"
    return {
        "proposed_symbol_id": symbol_slug.upper(),
        "proposed_symbol_name": f"{Path(asset_path).stem} Region {region_index:02d}",
        "proposed_filename": f"{symbol_slug}.png",
    }


def strip_storage_prefix(filename):
    stem = Path(filename or "").stem
    suffix = Path(filename or "").suffix
    parts = stem.split("-", 1)
    if len(parts) == 2 and parts[0].isdigit():
        return f"{parts[1]}{suffix}"
    return filename


def title_from_filename(filename):
    stem = Path(strip_storage_prefix(filename) or "").stem
    cleaned = " ".join(part for part in stem.replace("_", " ").replace("-", " ").split() if part)
    return " ".join(part.capitalize() for part in cleaned.split()) if cleaned else None


def keywords_from_text(*values):
    stop_words = {"and", "for", "the", "with", "from", "symbol", "symbols"}
    keywords = []
    for value in values:
        normalized = normalize_label_text(str(value or "").replace("_", " ").replace("-", " ").replace(".", " "))
        for raw_token in normalized.split():
            token = "".join(char.lower() for char in raw_token if char.isalnum())
            if len(token) < 3 or token in stop_words or token in keywords:
                continue
            keywords.append(token)
    return keywords


def build_single_symbol_candidate(task, normalized, raster_analysis, split_plan, ocr_label_summary):
    origin_file_name = (
        task.get("origin_file_name")
        or normalized.get("file_name")
        or (Path(normalized["asset_path"]).name if normalized.get("asset_path") else None)
    )
    candidate_title = task.get("candidate_title") or title_from_filename(origin_file_name)
    candidate_symbol_id = task.get("candidate_symbol_id")
    if not candidate_symbol_id and origin_file_name:
        candidate_symbol_id = slugify_token(Path(strip_storage_prefix(origin_file_name)).stem).upper()

    ocr_labels = []
    for candidate in ocr_label_summary.get("candidates") or []:
        label = normalize_label_text(candidate.get("text") or "")
        if label and label not in ocr_labels:
            ocr_labels.append(label)

    aliases = []
    for value in (candidate_symbol_id, Path(strip_storage_prefix(origin_file_name or "")).stem if origin_file_name else None, candidate_title):
        label = normalize_label_text(value)
        if label and label not in aliases:
            aliases.append(label)
    for label in ocr_labels:
        if label not in aliases:
            aliases.append(label)

    description_hints = [
        value for value in (
            task.get("file_note"),
            task.get("source_notes"),
            task.get("submission_batch_summary"),
        )
        if value
    ]

    return {
        "artifact_type": "single_symbol_raster_candidate",
        "source_asset_path": normalized.get("asset_path"),
        "origin_file_name": origin_file_name,
        "asset_format": normalized.get("asset_format"),
        "attachment_id": normalized.get("attachment_id"),
        "attachment_ids": normalized.get("attachment_ids") or [],
        "raw_object_key": normalized.get("raw_object_key"),
        "submission_batch_id": normalized.get("submission_batch_id"),
        "candidate_symbol_id": candidate_symbol_id,
        "candidate_title": candidate_title,
        "aliases": aliases,
        "keywords": keywords_from_text(candidate_symbol_id, candidate_title, origin_file_name, *description_hints, *ocr_labels),
        "description_hints": description_hints,
        "ocr_labels": ocr_labels,
        "sheet_type": raster_analysis["sheet_type"],
        "analysis_confidence": raster_analysis["analysis_confidence"],
        "estimated_symbol_count": raster_analysis["estimated_symbol_count"],
        "image_width": raster_analysis["image_width"],
        "image_height": raster_analysis["image_height"],
        "split_recommended": False,
        "split_status": "none",
        "candidate_region": split_plan["regions"][0] if split_plan.get("regions") else None,
        "recommended_next_agents": ["tracy", "libby"],
    }


def normalize_label_text(value):
    cleaned = (value or "").replace("{", "(").replace("}", ")")
    cleaned = " ".join(cleaned.replace("\n", " ").split())
    cleaned = cleaned.strip(" -_.,;:|")
    return cleaned


def label_is_suitable(label_text, average_confidence):
    cleaned = normalize_label_text(label_text)
    alpha_count = sum(1 for char in cleaned if char.isalpha())
    if not cleaned or len(cleaned) < 3 or len(cleaned) > 80:
        return False
    if alpha_count < 3:
        return False
    if average_confidence < 55:
        return False
    normalized = cleaned.lower()
    if normalized in {"valve", "actuator", "type", "check", "pressure", "electric", "hydraulic"}:
        return False
    return True


def alpha_count(value):
    return sum(1 for char in value if char.isalpha())


def word_alpha_count(word):
    return alpha_count(word.get("text") or "")


def median_number(values):
    if not values:
        return 0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def phrase_typical_height(words):
    content_heights = [word["height"] for word in words if word_alpha_count(word) >= 2]
    if content_heights:
        return median_number(content_heights)
    return median_number([word["height"] for word in words])


def is_bridge_noise_word(word, typical_height):
    text = normalize_label_text(word.get("text") or "")
    alpha_chars = alpha_count(text)
    if not text:
        return True
    if alpha_chars == 0:
        return True
    if alpha_chars > 1:
        return False
    height_limit = max(18, typical_height * 1.6)
    width_limit = max(12, typical_height * 1.4)
    return word["height"] >= height_limit or word["width"] >= width_limit


def trim_phrase_edges(words, typical_height):
    trimmed = list(words)
    while len(trimmed) > 1 and is_bridge_noise_word(trimmed[0], typical_height):
        trimmed.pop(0)
    while len(trimmed) > 1 and is_bridge_noise_word(trimmed[-1], typical_height):
        trimmed.pop()
    return trimmed


def phrases_should_merge(current_text, candidate_text):
    current = normalize_label_text(current_text)
    candidate = normalize_label_text(candidate_text)
    if not current or not candidate:
        return False
    if candidate.startswith("("):
        return True
    if alpha_count(current) < 4 or alpha_count(candidate) < 3:
        return False
    current_words = current.lower().split()
    candidate_words = candidate.lower().split()
    if candidate_words == ["valve"] and "valve" not in current_words:
        return True
    if candidate_words == ["actuator"] and "actuator" not in current_words:
        return True
    if current_words[-1] in {"pressure", "pneumatic", "hydraulic", "electric", "manually", "operated"}:
        return True
    return False


def average_confidence(words):
    confidences = [float(word["conf"]) for word in words if float(word["conf"]) >= 0]
    if not confidences:
        return 0.0
    return sum(confidences) / len(confidences)


def word_bbox_union(words):
    left = min(word["left"] for word in words)
    top = min(word["top"] for word in words)
    right = max(word["left"] + word["width"] for word in words)
    bottom = max(word["top"] + word["height"] for word in words)
    return {
        "x": left,
        "y": top,
        "width": right - left,
        "height": bottom - top,
    }


def group_words_into_phrases(words):
    if not words:
        return []
    ordered = sorted(words, key=lambda word: word["left"])
    typical_height = phrase_typical_height(ordered)
    gap_threshold = max(14, int(typical_height * 1.8))
    groups = []
    current_group = []
    for word in ordered:
        if is_bridge_noise_word(word, typical_height):
            if current_group:
                groups.append(current_group)
                current_group = []
            continue
        if not current_group:
            current_group = [word]
            continue
        previous = current_group[-1]
        gap = word["left"] - (previous["left"] + previous["width"])
        if gap <= gap_threshold:
            current_group.append(word)
        else:
            groups.append(current_group)
            current_group = [word]
    if current_group:
        groups.append(current_group)

    phrases = []
    for group in groups:
        group = trim_phrase_edges(group, typical_height)
        if not group:
            continue
        text = normalize_label_text(" ".join(word["text"] for word in group))
        if not text:
            continue
        phrases.append(
            {
                "text": text,
                "bbox": word_bbox_union(group),
                "average_confidence": round(average_confidence(group), 2),
                "words": group,
            }
        )
    return phrases


def bbox_horizontal_overlap(a, b):
    left = max(a["x"], b["x"])
    right = min(a["x"] + a["width"], b["x"] + b["width"])
    return max(0, right - left)


def bbox_vertical_overlap(a, b):
    top = max(a["y"], b["y"])
    bottom = min(a["y"] + a["height"], b["y"] + b["height"])
    return max(0, bottom - top)


def merge_multiline_phrases(phrases):
    if not phrases:
        return []
    ordered = sorted(phrases, key=lambda phrase: (phrase["bbox"]["y"], phrase["bbox"]["x"]))
    used = set()
    merged = []
    for index, phrase in enumerate(ordered):
        if index in used:
            continue
        current = dict(phrase)
        current_words = list(phrase["words"])
        current_bbox = dict(phrase["bbox"])
        for next_index in range(index + 1, len(ordered)):
            if next_index in used:
                continue
            candidate = ordered[next_index]
            vertical_gap = candidate["bbox"]["y"] - (current_bbox["y"] + current_bbox["height"])
            horizontal_overlap = bbox_horizontal_overlap(current_bbox, candidate["bbox"])
            center_delta = abs(
                (current_bbox["x"] + current_bbox["width"] / 2)
                - (candidate["bbox"]["x"] + candidate["bbox"]["width"] / 2)
            )
            if vertical_gap < 0 or vertical_gap > max(24, int((current_bbox["height"] + candidate["bbox"]["height"]) * 0.9)):
                continue
            if horizontal_overlap <= 0 and center_delta > max(current_bbox["width"], candidate["bbox"]["width"]) * 0.35:
                continue
            if not phrases_should_merge(current["text"], candidate["text"]):
                continue
            current_words.extend(candidate["words"])
            current_bbox = word_bbox_union(current_words)
            current = {
                "text": normalize_label_text(f"{current['text']} {candidate['text']}"),
                "bbox": current_bbox,
                "average_confidence": round(average_confidence(current_words), 2),
                "words": current_words,
            }
            used.add(next_index)
        merged.append(current)
    return merged


def tesseract_available():
    return shutil.which("tesseract") is not None and Image is not None and ImageOps is not None


def extract_ocr_label_candidates(asset_path):
    if not tesseract_available():
        return {"available": False, "reason": "missing_tesseract_or_pillow", "candidates": []}

    image = Image.open(asset_path).convert("L")
    image = ImageOps.autocontrast(image)
    scale = 2
    image = image.resize((image.width * scale, image.height * scale))

    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
        image.save(tmp.name)
        result = subprocess.run(
            ["tesseract", tmp.name, "stdout", "--psm", "6", "tsv"],
            capture_output=True,
            text=True,
            check=False,
        )

    if result.returncode != 0:
        return {
            "available": False,
            "reason": "tesseract_failed",
            "stderr": result.stderr.strip(),
            "candidates": [],
        }

    lines = {}
    for row in csv.DictReader(io.StringIO(result.stdout), delimiter="\t"):
        text = normalize_label_text(row.get("text") or "")
        conf_raw = row.get("conf") or "-1"
        try:
            conf = float(conf_raw)
        except ValueError:
            conf = -1
        if not text or conf < 35:
            continue
        key = (row.get("block_num"), row.get("par_num"), row.get("line_num"))
        lines.setdefault(key, []).append(
            {
                "text": text,
                "conf": conf,
                "left": int(int(row["left"]) / scale),
                "top": int(int(row["top"]) / scale),
                "width": max(1, int(int(row["width"]) / scale)),
                "height": max(1, int(int(row["height"]) / scale)),
            }
        )

    phrases = []
    for words in lines.values():
        phrases.extend(group_words_into_phrases(words))

    candidates = []
    for phrase in merge_multiline_phrases(phrases):
        if label_is_suitable(phrase["text"], phrase["average_confidence"]):
            candidates.append(
                {
                    "text": phrase["text"],
                    "bbox": phrase["bbox"],
                    "average_confidence": phrase["average_confidence"],
                }
            )

    return {
        "available": True,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def region_label_score(region_bbox, candidate):
    candidate_bbox = candidate["bbox"]
    region_right = region_bbox["x"] + region_bbox["width"]
    region_bottom = region_bbox["y"] + region_bbox["height"]
    candidate_right = candidate_bbox["x"] + candidate_bbox["width"]
    candidate_bottom = candidate_bbox["y"] + candidate_bbox["height"]
    candidate_center_x = candidate_bbox["x"] + candidate_bbox["width"] / 2
    region_center_x = region_bbox["x"] + region_bbox["width"] / 2
    candidate_center_y = candidate_bbox["y"] + candidate_bbox["height"] / 2
    region_center_y = region_bbox["y"] + region_bbox["height"] / 2
    center_delta = abs(candidate_center_x - region_center_x)
    vertical_center_delta = abs(candidate_center_y - region_center_y)
    horizontal_overlap = bbox_horizontal_overlap(region_bbox, candidate_bbox)
    vertical_overlap = bbox_vertical_overlap(region_bbox, candidate_bbox)
    horizontal_overlap_ratio = horizontal_overlap / max(1, min(region_bbox["width"], candidate_bbox["width"]))
    vertical_overlap_ratio = vertical_overlap / max(1, min(region_bbox["height"], candidate_bbox["height"]))
    vertical_gap = candidate_bbox["y"] - region_bottom
    right_gap = candidate_bbox["x"] - region_right
    left_gap = region_bbox["x"] - candidate_right

    scores = []
    normalized_text = normalize_label_text(candidate.get("text") or "")
    normalized_words = normalized_text.split()
    quality_adjustment = 0.0
    if len(normalized_words) >= 2:
        quality_adjustment += 0.12
    if candidate["average_confidence"] < 65:
        quality_adjustment -= 0.35
    if normalized_words and normalized_words[0][:1].islower():
        quality_adjustment -= 0.7
    if len(normalized_words) == 1 and normalized_text.lower() in {"vessel", "tank", "column"}:
        quality_adjustment -= 0.45

    # Labels below the symbol remain the primary path used by Valves1-style sheets.
    if candidate_bbox["y"] >= region_bbox["y"] - 24 and candidate_bbox["y"] <= region_bottom + 120:
        if center_delta <= max(region_bbox["width"] * 1.5, candidate_bbox["width"] * 0.8, 110):
            vertical_penalty = abs(vertical_gap) / 120
            center_penalty = center_delta / max(1, region_bbox["width"])
            scores.append(
                (horizontal_overlap_ratio * 2.4)
                + (candidate["average_confidence"] / 100)
                + quality_adjustment
                - vertical_penalty
                - center_penalty
            )

    # Allow labels positioned to the right of the symbol with shared vertical banding.
    if right_gap >= -18 and right_gap <= max(220, region_bbox["width"] * 3):
        if vertical_overlap_ratio >= 0.22 or vertical_center_delta <= max(region_bbox["height"] * 0.7, 28):
            right_gap_penalty = max(0, right_gap) / max(90, region_bbox["width"] * 2.2)
            vertical_penalty = vertical_center_delta / max(1, region_bbox["height"] * 1.4)
            scores.append(
                (vertical_overlap_ratio * 2.2)
                + (candidate["average_confidence"] / 100)
                + quality_adjustment
                - right_gap_penalty
                - vertical_penalty
            )

    # A lighter fallback for left-positioned labels keeps the matcher flexible for future sheets.
    if left_gap >= -18 and left_gap <= max(180, region_bbox["width"] * 2.5):
        if vertical_overlap_ratio >= 0.3 or vertical_center_delta <= max(region_bbox["height"] * 0.65, 24):
            left_gap_penalty = max(0, left_gap) / max(80, region_bbox["width"] * 2.0)
            vertical_penalty = vertical_center_delta / max(1, region_bbox["height"] * 1.35)
            scores.append(
                (vertical_overlap_ratio * 1.8)
                + (candidate["average_confidence"] / 100)
                + quality_adjustment
                - left_gap_penalty
                - vertical_penalty
            )

    if not scores:
        return None
    return max(scores)


def apply_ocr_labels_to_split_plan(asset_path, split_plan):
    ocr = extract_ocr_label_candidates(asset_path)
    if not ocr.get("available"):
        return ocr

    used_candidate_indexes = set()
    for region in split_plan["regions"]:
        scored = []
        for index, candidate in enumerate(ocr["candidates"]):
            if index in used_candidate_indexes:
                continue
            score = region_label_score(region["bbox"], candidate)
            if score is None or score < 0.9:
                continue
            scored.append((score, index, candidate))
        if not scored:
            region["name_source"] = "fallback"
            continue
        scored.sort(reverse=True, key=lambda item: item[0])
        _, candidate_index, candidate = scored[0]
        used_candidate_indexes.add(candidate_index)
        label_text = normalize_label_text(candidate["text"])
        slug = slugify_token(label_text)
        region["proposed_symbol_name"] = label_text
        region["proposed_symbol_id"] = slug.upper()
        region["crop_filename"] = f"{slug}-region-{region['region_index']:02d}.png"
        region["name_source"] = "ocr_label"
        region["ocr_label_confidence"] = candidate["average_confidence"]
        region["ocr_label_bbox"] = candidate["bbox"]

    ocr["assigned_count"] = len(used_candidate_indexes)
    return ocr


def png_pixel_rgba(decoded_png, x, y):
    color_type = decoded_png["color_type"]
    row = decoded_png["rows"][y]
    palette = decoded_png["palette"]
    transparency = decoded_png["transparency"]

    if color_type == 0:
        gray = row[x]
        return gray, gray, gray, 255
    if color_type == 2:
        index = x * 3
        return row[index], row[index + 1], row[index + 2], 255
    if color_type == 3:
        if palette is None:
            raise ValueError("Indexed PNG is missing a palette.")
        palette_index = row[x]
        palette_offset = palette_index * 3
        if palette_offset + 2 >= len(palette):
            raise ValueError("Indexed PNG references a palette entry outside PLTE.")
        alpha = transparency[palette_index] if transparency and palette_index < len(transparency) else 255
        return palette[palette_offset], palette[palette_offset + 1], palette[palette_offset + 2], alpha
    if color_type == 4:
        index = x * 2
        gray = row[index]
        return gray, gray, gray, row[index + 1]
    if color_type == 6:
        index = x * 4
        return row[index], row[index + 1], row[index + 2], row[index + 3]
    raise ValueError(f"Unsupported PNG color type: {color_type}")


def estimate_background_level(decoded_png):
    width = decoded_png["width"]
    height = decoded_png["height"]
    sample_points = {
        (0, 0),
        (width - 1, 0),
        (0, height - 1),
        (width - 1, height - 1),
        (width // 2, 0),
        (width // 2, height - 1),
        (0, height // 2),
        (width - 1, height // 2),
    }
    luminance_samples = []
    for x, y in sample_points:
        r, g, b, alpha = png_pixel_rgba(decoded_png, x, y)
        if alpha == 0:
            luminance_samples.append(255)
        else:
            luminance_samples.append(int((r + g + b) / 3))
    luminance_samples.sort()
    return luminance_samples[len(luminance_samples) // 2]


def is_foreground_pixel(decoded_png, x, y, background_level):
    r, g, b, alpha = png_pixel_rgba(decoded_png, x, y)
    if alpha <= 16:
        return False
    luminance = (r + g + b) / 3
    if alpha < 200:
        return True
    return abs(luminance - background_level) >= 48


def find_connected_components(mask, width, height):
    visited = set()
    components = []
    for y in range(height):
        for x in range(width):
            if not mask[y][x] or (x, y) in visited:
                continue
            queue = [(x, y)]
            visited.add((x, y))
            min_x = max_x = x
            min_y = max_y = y
            area = 0
            while queue:
                current_x, current_y = queue.pop()
                area += 1
                min_x = min(min_x, current_x)
                max_x = max(max_x, current_x)
                min_y = min(min_y, current_y)
                max_y = max(max_y, current_y)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    next_x = current_x + dx
                    next_y = current_y + dy
                    if not (0 <= next_x < width and 0 <= next_y < height):
                        continue
                    if not mask[next_y][next_x] or (next_x, next_y) in visited:
                        continue
                    visited.add((next_x, next_y))
                    queue.append((next_x, next_y))
            components.append({
                "x": min_x,
                "y": min_y,
                "width": max_x - min_x + 1,
                "height": max_y - min_y + 1,
                "pixel_area": area,
            })
    return components


def analyze_raster_sheet(asset_path):
    decoded_png = decode_png_rows(asset_path)
    width = decoded_png["width"]
    height = decoded_png["height"]
    background_level = estimate_background_level(decoded_png)

    mask = []
    foreground_pixels = 0
    for y in range(height):
        row = []
        for x in range(width):
            foreground = is_foreground_pixel(decoded_png, x, y, background_level)
            row.append(foreground)
            if foreground:
                foreground_pixels += 1
        mask.append(row)

    components = find_connected_components(mask, width, height)
    min_component_pixels = max(24, (width * height) // 2000)
    min_component_dimension = max(4, min(width, height) // 50)

    candidate_regions = []
    for component in components:
        if component["pixel_area"] < min_component_pixels:
            continue
        if component["width"] < min_component_dimension or component["height"] < min_component_dimension:
            continue
        candidate_regions.append({
            "x": component["x"],
            "y": component["y"],
            "width": component["width"],
            "height": component["height"],
            "pixel_area": component["pixel_area"],
            "confidence": 0.78,
        })

    candidate_regions.sort(key=lambda item: (item["y"], item["x"]))
    estimated_count = len(candidate_regions)
    foreground_ratio = round(foreground_pixels / max(1, width * height), 4)

    if estimated_count == 0:
        sheet_type = "ambiguous"
        split_recommended = False
        confidence = 0.35
    elif estimated_count == 1:
        sheet_type = "single_symbol"
        split_recommended = False
        confidence = 0.82
    else:
        sheet_type = "multi_symbol_sheet"
        split_recommended = True
        confidence = min(0.9, 0.72 + min(estimated_count, 4) * 0.04)

    return {
        "image_width": width,
        "image_height": height,
        "bit_depth": decoded_png["bit_depth"],
        "color_type": decoded_png["color_type"],
        "background_level": background_level,
        "foreground_pixel_ratio": foreground_ratio,
        "raw_component_count": len(components),
        "candidate_regions": candidate_regions,
        "estimated_symbol_count": estimated_count,
        "sheet_type": sheet_type,
        "split_recommended": split_recommended,
        "analysis_confidence": round(confidence, 2),
    }


def build_split_plan(asset_path, raster_analysis):
    image_width = raster_analysis["image_width"]
    image_height = raster_analysis["image_height"]
    regions = []
    for index, candidate in enumerate(raster_analysis["candidate_regions"], start=1):
        padding = max(8, min(candidate["width"], candidate["height"]) // 10)
        expanded = expand_region(candidate, image_width, image_height, padding)
        proposed_child = proposed_child_symbol_fields(asset_path, index)
        regions.append(
            {
                "region_index": index,
                "bbox": {
                    "x": expanded["x"],
                    "y": expanded["y"],
                    "width": expanded["width"],
                    "height": expanded["height"],
                },
                "pixel_area": candidate["pixel_area"],
                "padding_px": padding,
                "region_confidence": candidate["confidence"],
                "crop_filename": proposed_child["proposed_filename"],
                "crop_status": "proposed",
                "proposed_symbol_id": proposed_child["proposed_symbol_id"],
                "proposed_symbol_name": proposed_child["proposed_symbol_name"],
            }
        )

    if raster_analysis["sheet_type"] == "multi_symbol_sheet" and raster_analysis["analysis_confidence"] >= 0.8:
        recommended_action = "create_proposed_children"
    elif raster_analysis["sheet_type"] == "single_symbol":
        recommended_action = "continue_single_symbol_validation"
    else:
        recommended_action = "review_required"

    return {
        "source_file_name": asset_path.name,
        "source_asset_path": str(asset_path),
        "sheet_type": raster_analysis["sheet_type"],
        "analysis_confidence": raster_analysis["analysis_confidence"],
        "image_width": image_width,
        "image_height": image_height,
        "estimated_symbol_count": raster_analysis["estimated_symbol_count"],
        "recommended_action": recommended_action,
        "regions": regions,
    }


def create_split_derivatives(asset_path, decoded_png, split_plan, runtime_root, queue_item_id):
    derivative_root = Path(runtime_root) / "derivative_assets" / queue_item_id
    derivative_root.mkdir(parents=True, exist_ok=True)
    children = []
    for region in split_plan["regions"]:
        bbox = region["bbox"]
        crop_rows = crop_png_to_rgba_rows(
            decoded_png,
            {
                "x": bbox["x"],
                "y": bbox["y"],
                "width": bbox["width"],
                "height": bbox["height"],
            },
        )
        crop_bytes = encode_rgba_png(bbox["width"], bbox["height"], crop_rows)
        crop_path = derivative_root / region["crop_filename"]
        crop_path.write_bytes(crop_bytes)
        children.append(
            {
                "region_index": region["region_index"],
                "file_name": crop_path.name,
                "path": str(crop_path),
                "sha256": hashlib.sha256(crop_bytes).hexdigest(),
                "size_bytes": len(crop_bytes),
                "bbox": bbox,
                "crop_status": region["crop_status"],
                "proposed_symbol_id": region.get("proposed_symbol_id"),
                "proposed_symbol_name": region.get("proposed_symbol_name"),
                "name_source": region.get("name_source"),
                "ocr_label_confidence": region.get("ocr_label_confidence"),
                "ocr_label_bbox": region.get("ocr_label_bbox"),
            }
        )

    return {
        "source_asset_path": str(asset_path),
        "derivative_root": str(derivative_root),
        "children": children,
    }


def graphic_change_requests_text_removal(requested_changes):
    values = [
        requested_changes.get("decision_note"),
        requested_changes.get("case_comment"),
        requested_changes.get("libby_summary"),
    ]
    for child in requested_changes.get("child_decisions") or []:
        if isinstance(child, dict):
            values.extend([child.get("note"), child.get("details"), child.get("action")])
    text = " ".join(str(value or "") for value in values).lower()
    return any(term in text for term in ("remove text", "erase text", "delete text", "text", "label", "lettering", "annotation"))


def child_asset_path_from_decision(child_decision, runtime_root):
    child_id = str(child_decision.get("childId") or "")
    if child_id.startswith("derived-splits/"):
        relative = child_id[len("derived-splits/") :]
        candidate = Path(runtime_root) / "derivative_assets" / relative
        if candidate.exists():
            return candidate
    return None


def dominant_border_color(image):
    rgba = image.convert("RGBA")
    width, height = rgba.size
    samples = []
    for x in range(width):
        samples.append(rgba.getpixel((x, 0)))
        samples.append(rgba.getpixel((x, height - 1)))
    for y in range(height):
        samples.append(rgba.getpixel((0, y)))
        samples.append(rgba.getpixel((width - 1, y)))
    opaque = [sample for sample in samples if sample[3] > 16]
    if not opaque:
        return (255, 255, 255, 0)
    counts = {}
    for r, g, b, a in opaque:
        key = (round(r / 8) * 8, round(g / 8) * 8, round(b / 8) * 8, 255)
        counts[key] = counts.get(key, 0) + 1
    return max(counts.items(), key=lambda item: item[1])[0]


def text_bboxes_from_tesseract(asset_path):
    if not tesseract_available():
        return {"available": False, "reason": "missing_tesseract_or_pillow", "bboxes": []}

    with Image.open(asset_path) as source:
        gray = ImageOps.autocontrast(source.convert("L"))
        scale = 3
        upscaled = gray.resize((gray.width * scale, gray.height * scale))
        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            upscaled.save(tmp.name)
            result = subprocess.run(
                ["tesseract", tmp.name, "stdout", "--psm", "6", "tsv"],
                capture_output=True,
                text=True,
                check=False,
            )

    if result.returncode != 0:
        return {"available": False, "reason": "tesseract_failed", "stderr": result.stderr.strip(), "bboxes": []}

    bboxes = []
    for row in csv.DictReader(io.StringIO(result.stdout), delimiter="\t"):
        text = normalize_label_text(row.get("text") or "")
        if not text:
            continue
        try:
            confidence = float(row.get("conf") or "-1")
        except ValueError:
            confidence = -1
        if confidence < 20:
            continue
        bbox = {
            "x": max(0, int(int(row["left"]) / scale)),
            "y": max(0, int(int(row["top"]) / scale)),
            "width": max(1, int(int(row["width"]) / scale)),
            "height": max(1, int(int(row["height"]) / scale)),
        }
        bboxes.append({"text": text, "confidence": round(confidence, 2), "bbox": bbox})
    return {"available": True, "bboxes": bboxes}


def erase_text_from_image(source_path, output_path):
    if Image is None or ImageDraw is None:
        return {"status": "failed", "reason": "missing_pillow", "text_regions": []}

    ocr = text_bboxes_from_tesseract(source_path)
    if not ocr.get("available"):
        return {"status": "failed", "reason": ocr.get("reason"), "text_regions": []}
    if not ocr["bboxes"]:
        return {"status": "failed", "reason": "no_text_detected", "text_regions": []}

    with Image.open(source_path) as source:
        edited = source.convert("RGBA")
    draw = ImageDraw.Draw(edited)
    fill = dominant_border_color(edited)
    for item in ocr["bboxes"]:
        bbox = item["bbox"]
        padding_x = max(2, int(bbox["width"] * 0.18))
        padding_y = max(2, int(bbox["height"] * 0.22))
        left = max(0, bbox["x"] - padding_x)
        top = max(0, bbox["y"] - padding_y)
        right = min(edited.width, bbox["x"] + bbox["width"] + padding_x)
        bottom = min(edited.height, bbox["y"] + bbox["height"] + padding_y)
        draw.rectangle([left, top, right, bottom], fill=fill)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    edited.save(output_path)
    return {
        "status": "edited",
        "text_regions": ocr["bboxes"],
        "background_fill": fill,
    }


def gemini_image_edit_enabled():
    provider = os.environ.get("SYMGOV_VLAD_IMAGE_EDIT_PROVIDER", "").strip().lower()
    fallback = os.environ.get("SYMGOV_VLAD_IMAGE_EDIT_FALLBACK", "").strip().lower()
    api_key = get_gemini_api_key()
    return provider == "gemini" and fallback in {"1", "true", "yes", "on"} and bool(api_key)


def gemini_image_edit_config_status():
    provider = os.environ.get("SYMGOV_VLAD_IMAGE_EDIT_PROVIDER", "").strip().lower()
    fallback = os.environ.get("SYMGOV_VLAD_IMAGE_EDIT_FALLBACK", "").strip().lower()
    has_key = bool(get_gemini_api_key())
    if provider != "gemini":
        return "provider_not_gemini"
    if fallback not in {"1", "true", "yes", "on"}:
        return "fallback_disabled"
    if not has_key:
        return "missing_api_key"
    return "enabled"


def image_mime_type(path):
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def gemini_edit_prompt(child_decision, requested_changes):
    requested = child_decision.get("details") or child_decision.get("note") or requested_changes.get("decision_note") or "Remove the requested text."
    symbol_name = child_decision.get("proposedSymbolName") or child_decision.get("proposedSymbolId") or "this engineering symbol"
    return (
        f"Edit this engineering symbol image for {symbol_name}. "
        f"Requested change: {requested}. "
        "Remove only the visible text, labels, lettering, or annotations requested by the reviewer. "
        "Preserve the symbol geometry, strokes, line weights, transparency/background, crop, orientation, and overall dimensions as closely as possible. "
        "Return a clean edited image only."
    )


def response_part_image_bytes(response_payload):
    for candidate in response_payload.get("candidates") or []:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            inline_data = part.get("inlineData") or part.get("inline_data")
            if not isinstance(inline_data, dict):
                continue
            data = inline_data.get("data")
            if not data:
                continue
            return base64.b64decode(data), inline_data.get("mimeType") or inline_data.get("mime_type")
    return None, None


def edit_image_with_gemini(source_path, output_path, prompt):
    api_key = get_gemini_api_key()
    if not api_key:
        return {"status": "failed", "reason": "missing_gemini_api_key", "provider": "gemini"}

    source_path = Path(source_path)
    max_source_bytes = int(os.environ.get("SYMGOV_GEMINI_MAX_SOURCE_BYTES", "7000000"))
    if source_path.stat().st_size > max_source_bytes:
        return {"status": "failed", "reason": "source_image_too_large", "provider": "gemini"}

    model = os.environ.get("SYMGOV_GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image").strip() or "gemini-2.5-flash-image"
    timeout = float(os.environ.get("SYMGOV_GEMINI_TIMEOUT_SECONDS", "60"))
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": image_mime_type(source_path),
                            "data": base64.b64encode(source_path.read_bytes()).decode("ascii"),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "responseModalities": ["Image"],
        },
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")[:1000]
        return {"status": "failed", "reason": f"gemini_http_{exc.code}", "provider": "gemini", "detail": error_body}
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        return {"status": "failed", "reason": "gemini_request_failed", "provider": "gemini", "detail": str(exc)}

    image_bytes, mime_type = response_part_image_bytes(response_payload)
    if not image_bytes:
        return {"status": "failed", "reason": "gemini_no_image_returned", "provider": "gemini"}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if Image is not None:
        try:
            with Image.open(io.BytesIO(image_bytes)) as generated:
                generated.convert("RGBA").save(output_path, format="PNG")
        except OSError:
            output_path.write_bytes(image_bytes)
    else:
        output_path.write_bytes(image_bytes)
    return {
        "status": "edited",
        "provider": "gemini",
        "model": model,
        "mime_type": "image/png",
        "source_mime_type": mime_type or image_mime_type(output_path),
        "prompt": prompt,
    }


def create_graphic_change_assets(task, requested_changes, runtime_root):
    if not graphic_change_requests_text_removal(requested_changes):
        return [], [{"code": "VLAD-GRAPHIC-002", "severity": "medium", "detail": "Graphic change request is not currently supported by Vlad's automated raster editor."}]

    queue_item_id = task.get("queue_item_id") or "untracked"
    output_root = Path(runtime_root) / "derivative_assets" / queue_item_id
    edited_assets = []
    defects = []
    for index, child_decision in enumerate(requested_changes.get("child_decisions") or [], start=1):
        if not isinstance(child_decision, dict):
            continue
        source_path = child_asset_path_from_decision(child_decision, runtime_root)
        if source_path is None:
            defects.append({
                "code": "VLAD-GRAPHIC-003",
                "severity": "medium",
                "detail": f"Could not resolve local child asset for {child_decision.get('childId')}.",
            })
            continue
        output_name = f"{source_path.stem}-text-removed.png"
        output_path = output_root / output_name
        edit_result = erase_text_from_image(source_path, output_path)
        if edit_result["status"] != "edited":
            if gemini_image_edit_enabled():
                output_name = f"{source_path.stem}-gemini-text-removed.png"
                output_path = output_root / output_name
                edit_result = edit_image_with_gemini(
                    source_path,
                    output_path,
                    gemini_edit_prompt(child_decision, requested_changes),
                )
            else:
                edit_result["fallback_status"] = gemini_image_edit_config_status()
        if edit_result["status"] != "edited":
            defects.append({
                "code": "VLAD-GRAPHIC-004",
                "severity": "medium",
                "detail": f"Could not remove text from {source_path.name}: {edit_result.get('reason')}."
                + (f" Gemini fallback: {edit_result.get('fallback_status')}." if edit_result.get("fallback_status") else ""),
            })
            continue
        content = output_path.read_bytes()
        edited_assets.append({
            "child_id": child_decision.get("childId"),
            "proposed_symbol_id": child_decision.get("proposedSymbolId"),
            "proposed_symbol_name": child_decision.get("proposedSymbolName"),
            "source_path": str(source_path),
            "path": str(output_path),
            "file_name": output_name,
            "object_key": f"edited-symbols/{queue_item_id}/{output_name}",
            "content_type": "image/png",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "edit_operation": "remove_text",
            "edit_provider": edit_result.get("provider") or "local_pillow_tesseract",
            "edit_model": edit_result.get("model"),
            "edit_prompt": child_decision.get("details") or requested_changes.get("decision_note"),
            "text_regions": edit_result.get("text_regions", []),
            "gemini_prompt": edit_result.get("prompt"),
            "sort_order": index,
        })
    return edited_assets, defects


def run_graphic_change_task(task):
    queue_item_id = task.get("queue_item_id") or "untracked"
    requested_changes = task.get("requested_changes") if isinstance(task.get("requested_changes"), dict) else {}
    evidence_trace = []
    defects = []
    add_trace(evidence_trace, "graphic_change_context", "passed", "Loaded Libby-routed graphic change request for symbol modification.")
    if not task.get("origin_object_key") and not task.get("asset_path"):
        add_defect(defects, "VLAD-GRAPHIC-001", "medium", "Graphic change request did not include an origin object key or local asset path.")
        add_trace(evidence_trace, "source_asset", "warning", "Vlad could not identify a concrete source asset for automated modification.")
    else:
        add_trace(evidence_trace, "source_asset", "passed", "Graphic change request includes source asset lineage.")

    edited_assets, edit_defects = create_graphic_change_assets(task, requested_changes, task.get("runtime_root") or ".")
    defects.extend(edit_defects)
    if edited_assets:
        add_trace(evidence_trace, "raster_text_removal", "passed", f"Created {len(edited_assets)} edited child asset(s) with text removed.")
    elif graphic_change_requests_text_removal(requested_changes):
        add_trace(evidence_trace, "raster_text_removal", "warning", "No edited child asset was created for the text-removal request.")

    decision = "escalate" if defects else "pass"
    summary = (
        f"Vlad {'created edited graphic asset(s)' if edited_assets else 'prepared graphic change follow-up'} for case {task.get('review_case_id')} "
        "and returned the result to Libby for combination with other updates."
    )
    return {
        "queue_item_id": queue_item_id,
        "agent": "vlad",
        "schema_version": SCHEMA_VERSION,
        "task_type": task.get("task_type"),
        "decision": decision,
        "confidence": 0.72 if defects else 0.84,
        "escalation_target": "libby",
        "normalized_technical_metadata": {
            "review_case_id": task.get("review_case_id"),
            "review_decision_id": task.get("review_decision_id"),
            "libby_queue_item_id": task.get("libby_queue_item_id"),
            "origin_object_key": task.get("origin_object_key"),
            "origin_file_name": task.get("origin_file_name"),
            "asset_path": task.get("asset_path"),
            "asset_format": task.get("asset_format"),
            "candidate_symbol_id": task.get("candidate_symbol_id"),
            "candidate_symbol_name": task.get("candidate_symbol_name"),
            "requested_changes": requested_changes,
            "graphic_change_status": "edited_asset_prepared" if edited_assets and not defects else "needs_manual_asset_resolution",
            "edited_assets": edited_assets,
        },
        "defects": defects,
        "evidence_trace": evidence_trace,
        "additional_artifacts": [
            {
                "artifact_type": "symbol_graphic_change_result",
                "payload_json": {
                    "review_case_id": task.get("review_case_id"),
                    "review_decision_id": task.get("review_decision_id"),
                    "requested_changes": requested_changes,
                    "result_summary": summary,
                    "edited_assets": edited_assets,
                    "return_to_agent": "libby",
                    "next_review_agent": task.get("next_review_agent") or "daisy",
                },
            }
        ],
        "review_recommendation": None,
        "control_exceptions": [],
    }


def run_validation_task(task):
    if task.get("task_type") == "symbol_graphic_change_request":
        return run_graphic_change_task(task)

    queue_item_id = task.get("queue_item_id") or "untracked"
    source_type = task.get("source_type")
    source_id = task.get("source_id")
    asset_path_raw = task.get("asset_path")
    asset_format = infer_asset_format(asset_path_raw, task.get("asset_format"))
    compare_root = task.get("compare_root")
    expected_checks = list(task.get("expected_checks") or [])
    package_symbol_grouping = str(task.get("package_symbol_grouping") or "").strip().lower()
    package_member_relationship = str(task.get("package_member_relationship") or "").strip().lower()
    already_isolated_package_symbol = (
        package_symbol_grouping == "standalone_package_symbol_file"
        or package_member_relationship == "standalone_symbol_file"
    )
    if asset_format in {"png", "jpeg"} and "raster_sheet_analysis" not in expected_checks and not already_isolated_package_symbol:
        expected_checks = ["integrity", "raster_sheet_analysis"]
    if asset_format == "btx" and "btx_library_expansion" not in expected_checks:
        expected_checks = ["integrity", "btx_library_expansion"]

    defects = []
    evidence_trace = []
    normalized = {
        "source_type": source_type,
        "source_id": source_id,
        "intake_record_id": task.get("intake_record_id") or source_id,
        "attachment_id": task.get("attachment_id"),
        "attachment_ids": task.get("attachment_ids") or [],
        "raw_object_key": task.get("raw_object_key"),
        "submission_batch_id": task.get("submission_batch_id"),
        "asset_path": asset_path_raw,
        "file_name": Path(asset_path_raw).name if asset_path_raw else None,
        "origin_file_name": task.get("origin_file_name") or (Path(asset_path_raw).name if asset_path_raw else None),
        "candidate_symbol_id": task.get("candidate_symbol_id"),
        "candidate_title": task.get("candidate_title"),
        "source_notes": task.get("source_notes"),
        "file_note": task.get("file_note"),
        "submission_batch_summary": task.get("submission_batch_summary"),
        "asset_format": asset_format or None,
        "package_member": task.get("package_member"),
        "package_member_relationship": task.get("package_member_relationship"),
        "package_symbol_grouping": task.get("package_symbol_grouping"),
    }

    decision = "pass"
    confidence = 0.95
    escalation_target = "none"
    asset_path = Path(asset_path_raw) if asset_path_raw else None
    additional_artifacts = []
    review_recommendation = None
    control_exceptions = []

    if not asset_path_raw:
        add_defect(defects, "VLAD-INTEGRITY-001", "critical", "Missing required asset_path.")
        add_trace(evidence_trace, "integrity", "failed", "Task payload does not include asset_path.")
        decision = "escalate"
        confidence = 0.1
        escalation_target = "human_reviewer"
    elif not asset_path.exists():
        add_defect(defects, "VLAD-INTEGRITY-002", "critical", f"Asset file does not exist: {asset_path_raw}")
        add_trace(evidence_trace, "integrity", "failed", "Asset path could not be resolved on disk.")
        decision = "fail"
        confidence = 0.99
    elif asset_path.stat().st_size == 0:
        add_defect(defects, "VLAD-INTEGRITY-003", "critical", "Asset file is empty.")
        add_trace(evidence_trace, "integrity", "failed", "Resolved asset file has zero bytes.")
        decision = "fail"
        confidence = 0.99
    else:
        file_hash = sha256_file(asset_path)
        normalized["sha256"] = file_hash
        normalized["file_size_bytes"] = asset_path.stat().st_size
        add_trace(evidence_trace, "integrity", "passed", f"Resolved asset and computed sha256 {file_hash}.")

    if asset_path and asset_path.exists() and asset_format == "btx" and "btx_library_expansion" in expected_checks:
        output_root = Path(task.get("runtime_root") or asset_path.parent) / "btx_derivatives" / queue_item_id
        try:
            manifest = convert_btx(asset_path, output_root)
            children = []
            for symbol in manifest["symbols"]:
                if symbol.get("status") == "failed":
                    add_trace(evidence_trace, "btx_symbol_conversion", "warning", f"BTX symbol {symbol.get('ordinal')} was not converted: {symbol['warnings'][0]['detail']}")
                    continue
                child_id = f"btx:{manifest['source_sha256']}:{symbol['ordinal']}"
                assets = []
                for format_name, content_type in (("svg", "image/svg+xml"), ("dxf", "application/dxf"), ("png", "image/png")):
                    filename = symbol.get(format_name)
                    if not filename:
                        continue
                    generated_path = output_root / filename
                    payload = generated_path.read_bytes()
                    assets.append({
                        "format": format_name,
                        "file_name": filename,
                        "path": str(generated_path),
                        "content_type": content_type,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "size_bytes": len(payload),
                        "object_key": f"btx-derivatives/{queue_item_id}/{symbol['ordinal']:03d}/{filename}",
                    })
                children.append({
                    "child_symbol_id": child_id,
                    "ordinal": symbol["ordinal"],
                    "candidate_title": symbol["subject"],
                    "internal_name": symbol.get("internal_name"),
                    "annotation_type": symbol.get("annotation_type"),
                    "rect_points": symbol.get("rect_points"),
                    "source_btx_sha256": manifest["source_sha256"],
                    "source_package_id": task.get("source_package_id"),
                    "package_member": task.get("package_member"),
                    "warnings": symbol.get("warnings", []),
                    "assets": assets,
                })
            derivative_children = []
            all_derivatives = [asset for child in children for asset in child["assets"]]
            for child in children:
                preview_asset = next((asset for asset in child["assets"] if asset["format"] == "png"), None)
                if preview_asset is None:
                    continue
                derivative_children.append({
                    "proposed_symbol_id": child["child_symbol_id"],
                    "proposed_symbol_name": child["candidate_title"],
                    "file_name": preview_asset["file_name"],
                    "attachment_object_key": preview_asset["object_key"],
                    "size_bytes": preview_asset["size_bytes"],
                    "sha256": preview_asset["sha256"],
                    "path": preview_asset["path"],
                    "content_type": preview_asset["content_type"],
                    "name_source": "btx_annotation_subject",
                    "btx_ordinal": child["ordinal"],
                    "btx_subject": child["candidate_title"],
                    "btx_internal_name": child.get("internal_name"),
                    "source_btx_sha256": child["source_btx_sha256"],
                    "source_object_key": task.get("raw_object_key"),
                    "warnings": child.get("warnings", []),
                    "assets": child["assets"],
                    "visual_assets": {
                        "preview": preview_asset,
                        "derivatives": child["assets"],
                        "source_assets": [
                            {
                                "object_key": task.get("raw_object_key"),
                                "filename": asset_path.name,
                                "format": "btx",
                                "role": "source",
                                "downloadable": True,
                            }
                        ],
                    },
                })
            conversion_trace = {
                "queue_item_id": queue_item_id,
                "source_sha256": manifest["source_sha256"],
                "source_filename": task.get("original_filename") or manifest["source_filename"],
                "source_object_key": task.get("raw_object_key"),
                "btx_version": manifest["btx_version"],
                "tool_set_title": manifest["tool_set_title"],
                "total_symbol_count": len(manifest["symbols"]),
                "successful_symbol_count": manifest["successful_symbol_count"],
                "failed_symbol_count": manifest["failed_symbol_count"],
                "output_directory": str(output_root),
                "symbols": [
                    {
                        "ordinal": child["ordinal"],
                        "subject": child["candidate_title"],
                        "internal_name": child.get("internal_name"),
                        "assets": child["assets"],
                    }
                    for child in children
                ],
            }
            normalized["btx_library"] = {**manifest, "output_dir": str(output_root), "children": children}
            normalized["btx_conversion_trace"] = conversion_trace
            normalized["derivative_manifest"] = {"children": derivative_children}
            normalized["visual_assets"] = {
                "preview": next((asset for asset in all_derivatives if asset["format"] == "png"), None),
                "source_assets": [{"object_key": task.get("raw_object_key"), "filename": asset_path.name, "format": "btx", "role": "source", "downloadable": True}],
                "derivatives": all_derivatives,
            }
            additional_artifacts.append({"artifact_type": "btx_library_manifest", "payload_json": normalized["btx_library"]})
            additional_artifacts.append({"artifact_type": "derivative_manifest", "payload_json": normalized["derivative_manifest"]})
            add_trace(evidence_trace, "btx_conversion", "passed", f"BTX conversion trace recorded for {manifest['successful_symbol_count']} successful and {manifest['failed_symbol_count']} failed symbol(s).")
            add_trace(evidence_trace, "btx_library_expansion", "passed", f"Extracted {len(children)} usable symbol(s) from BTX library; {manifest['failed_symbol_count']} symbol(s) failed.")
            if children:
                decision = "escalate"
                escalation_target = "human_reviewer"
                confidence = min(confidence, 0.9)
                review_recommendation = {
                    "current_stage": "raster_split_review",
                    "escalation_level": "medium",
                    "detail": f"Extracted {len(children)} BTX symbols for individual review.",
                }
            if manifest["failed_symbol_count"]:
                confidence = min(confidence, 0.82)
        except BtxConversionError as exc:
            add_defect(defects, "VLAD-BTX-001", "high", f"BTX conversion failed ({exc.code}): {exc.detail}")
            add_trace(evidence_trace, "btx_conversion", "failed", f"BTX conversion failed for queue {queue_item_id}: {exc.code}: {exc.detail}")
            add_trace(evidence_trace, "btx_library_expansion", "failed", exc.detail)
            decision = "fail"
            confidence = min(confidence, 0.98)

    if asset_path and asset_path.exists() and asset_format == "dxf" and "dxf_parse" in expected_checks:
        if ezdxf_recover is None:
            add_defect(defects, "VLAD-DXF-001", "critical", "ezdxf is required for DXF validation but is not installed.")
            add_trace(evidence_trace, "dxf_parse", "failed", "DXF parser dependency is unavailable.")
            decision = "fail"
            confidence = min(confidence, 0.99)
        else:
            try:
                doc, auditor = ezdxf_recover.readfile(str(asset_path))
                dxf_metadata = extract_dxf_metadata(doc, auditor)
                normalized["dxf_metadata"] = dxf_metadata
                add_trace(
                    evidence_trace,
                    "dxf_parse",
                    "passed" if dxf_metadata["audit_error_count"] == 0 else "warning",
                    f"Parsed DXF {dxf_metadata.get('dxf_version')} with {dxf_metadata['modelspace_entity_count']} modelspace entities.",
                )
                if "dxf_metadata" in expected_checks:
                    add_trace(
                        evidence_trace,
                        "dxf_metadata",
                        "passed",
                        f"Extracted {dxf_metadata['layer_count']} layers, {dxf_metadata['block_count']} blocks, and entity counts.",
                    )
                if dxf_metadata["audit_error_count"]:
                    add_defect(defects, "VLAD-DXF-002", "high", "DXF parser reported audit errors during recovery.")
                    confidence = min(confidence, 0.72)
                if "dxf_derivative" in expected_checks:
                    derivative_manifest = create_dxf_svg_derivative(
                        doc,
                        dxf_metadata,
                        asset_path,
                        task.get("runtime_root"),
                        queue_item_id,
                        task.get("candidate_title") or task.get("candidate_symbol_id"),
                        task.get("raw_object_key"),
                    )
                    normalized["dxf_derivative"] = derivative_manifest
                    normalized["visual_assets"] = derivative_manifest["visual_assets"]
                    additional_artifacts.append(
                        {"artifact_type": "dxf_derivative_manifest", "payload_json": derivative_manifest}
                    )
                    add_trace(
                        evidence_trace,
                        "dxf_derivative",
                        "passed",
                        f"Generated accessible SVG derivative at {derivative_manifest['svg_path']}.",
                    )
            except Exception as exc:
                add_defect(defects, "VLAD-DXF-003", "critical", f"DXF parse or derivative generation failed: {exc}")
                add_trace(evidence_trace, "dxf_parse", "failed", "DXF parser could not recover the submitted asset.")
                decision = "fail"
                confidence = min(confidence, 0.99)

    root = None
    if asset_path and asset_path.exists() and "svg_parse" in expected_checks:
        try:
            tree = ET.parse(asset_path)
            root = tree.getroot()
            normalized["root_tag"] = local_name(root.tag)
            add_trace(evidence_trace, "svg_parse", "passed", f"Parsed SVG root tag {normalized['root_tag']}.")
        except ET.ParseError as exc:
            add_defect(defects, "VLAD-SVG-001", "critical", f"SVG parse failed: {exc}")
            add_trace(evidence_trace, "svg_parse", "failed", "XML parser rejected the file.")
            decision = "fail"
            confidence = 0.99

    if root is not None and "accessibility" in expected_checks:
        title = next((node for node in root.iter() if local_name(node.tag) == "title"), None)
        desc = next((node for node in root.iter() if local_name(node.tag) == "desc"), None)
        role = root.attrib.get("role")
        aria_labelledby = root.attrib.get("aria-labelledby")
        normalized["has_title"] = bool(title is not None and (title.text or "").strip())
        normalized["has_desc"] = bool(desc is not None and (desc.text or "").strip())
        normalized["role"] = role
        normalized["aria_labelledby"] = aria_labelledby

        if not normalized["has_title"]:
            add_defect(defects, "VLAD-A11Y-001", "high", "SVG is missing a non-empty <title> element.")
        if not normalized["has_desc"]:
            add_defect(defects, "VLAD-A11Y-002", "high", "SVG is missing a non-empty <desc> element.")
        if role != "img":
            add_defect(defects, "VLAD-A11Y-003", "medium", "SVG root should declare role=\"img\".")
        if not aria_labelledby:
            add_defect(defects, "VLAD-A11Y-004", "medium", "SVG root should declare aria-labelledby for title/desc.")

        add_trace(
            evidence_trace,
            "accessibility",
            "passed" if not any(d["code"].startswith("VLAD-A11Y") for d in defects) else "failed",
            "Checked title, desc, role, and aria-labelledby on the SVG root."
        )

    if root is not None and "geometry" in expected_checks:
        view_box = root.attrib.get("viewBox")
        width = root.attrib.get("width")
        height = root.attrib.get("height")
        script_nodes = [node for node in root.iter() if local_name(node.tag) == "script"]
        external_hrefs = []
        for node in root.iter():
            href = node.attrib.get("{http://www.w3.org/1999/xlink}href") or node.attrib.get("href")
            if href and (href.startswith("http://") or href.startswith("https://")):
                external_hrefs.append(href)

        normalized["view_box"] = view_box
        normalized["width"] = width
        normalized["height"] = height

        if not view_box:
            add_defect(defects, "VLAD-GEOM-001", "high", "SVG root is missing a viewBox.")
        if not width or not height:
            add_defect(defects, "VLAD-GEOM-002", "medium", "SVG root should declare explicit width and height.")
        if script_nodes:
            add_defect(defects, "VLAD-GEOM-003", "critical", "SVG contains script elements, which are not allowed.")
        if external_hrefs:
            add_defect(defects, "VLAD-GEOM-004", "high", "SVG references external resources.")

        add_trace(
            evidence_trace,
            "geometry",
            "passed" if not any(d["code"].startswith("VLAD-GEOM") for d in defects) else "failed",
            "Checked viewBox, dimensions, script usage, and external href references."
        )

    if asset_path and asset_path.exists() and "duplicates" in expected_checks:
        matches = scan_duplicates(asset_path, compare_root)
        normalized["duplicate_matches"] = matches
        if matches:
            add_defect(defects, "VLAD-DUPE-001", "high", "Duplicate SVG fingerprint matches existing assets.")
            add_trace(evidence_trace, "duplicates", "failed", f"Found duplicate fingerprints: {matches}")
        else:
            status = "passed" if compare_root else "skipped"
            detail = "No duplicate fingerprints found." if compare_root else "Duplicate scan skipped because compare_root was not provided."
            if not compare_root:
                confidence = min(confidence, 0.8)
            add_trace(evidence_trace, "duplicates", status, detail)

    if asset_path and asset_path.exists() and "raster_sheet_analysis" in expected_checks:
        if asset_format not in {"png", "jpeg"}:
            add_defect(
                defects,
                "VLAD-RASTER-001",
                "high",
                f"Raster sheet analysis currently supports PNG/JPEG only, not {asset_format or 'unknown'}.",
            )
            add_trace(
                evidence_trace,
                "raster_sheet_analysis",
                "failed",
                "Task requested raster sheet analysis for an unsupported asset format.",
            )
            decision = "escalate"
            confidence = min(confidence, 0.3)
            escalation_target = "human_reviewer"
        else:
            temp_analysis_path = None
            try:
                analysis_path, temp_analysis_path = normalize_raster_asset_for_analysis(asset_path, asset_format)
                decoded_png = decode_png_rows(analysis_path)
                raster_analysis = analyze_raster_sheet(analysis_path)
                split_plan = build_split_plan(asset_path, raster_analysis)
                ocr_label_summary = apply_ocr_labels_to_split_plan(asset_path, split_plan)
                normalized["raster_sheet_analysis"] = raster_analysis
                normalized["split_plan_summary"] = split_plan
                normalized["ocr_label_summary"] = ocr_label_summary
                normalized["estimated_symbol_count"] = raster_analysis["estimated_symbol_count"]
                normalized["candidate_regions"] = raster_analysis["candidate_regions"]
                normalized["sheet_type"] = raster_analysis["sheet_type"]
                normalized["split_recommended"] = raster_analysis["split_recommended"]
                normalized["split_status"] = "none"
                additional_artifacts.append({"artifact_type": "split_plan", "payload_json": split_plan})

                if raster_analysis["sheet_type"] == "ambiguous":
                    decision = "escalate"
                    confidence = min(confidence, raster_analysis["analysis_confidence"])
                    escalation_target = "human_reviewer"
                    normalized["split_status"] = "needs_review"
                    review_recommendation = {
                        "current_stage": "raster_split_review",
                        "escalation_level": "medium",
                        "detail": "PNG sheet analysis could not confidently isolate one or more symbol regions.",
                    }
                    control_exceptions.append(
                        {
                            "severity": "medium",
                            "rule_code": "VLAD-RASTER-AMBIGUOUS",
                            "detail": "PNG sheet analysis could not confidently isolate one or more symbol regions.",
                        }
                    )
                    add_trace(
                        evidence_trace,
                        "raster_sheet_analysis",
                        "failed",
                        "PNG sheet analysis was ambiguous and requires human review.",
                    )
                else:
                    confidence = min(confidence, raster_analysis["analysis_confidence"])
                    if raster_analysis["split_recommended"]:
                        derivative_manifest = create_split_derivatives(
                            asset_path,
                            decoded_png,
                            split_plan,
                            task.get("runtime_root"),
                            queue_item_id,
                        )
                        normalized["split_status"] = "proposed"
                        normalized["proposed_child_count"] = len(derivative_manifest["children"])
                        normalized["derivative_manifest"] = derivative_manifest
                        additional_artifacts.append(
                            {"artifact_type": "derivative_manifest", "payload_json": derivative_manifest}
                        )
                        decision = "escalate"
                        escalation_target = "human_reviewer"
                        review_recommendation = {
                            "current_stage": "raster_split_review",
                            "escalation_level": "medium",
                            "detail": f"Detected {raster_analysis['estimated_symbol_count']} candidate symbols and created proposed child crops.",
                        }
                        add_trace(
                            evidence_trace,
                            "raster_sheet_analysis",
                            "passed",
                            f"Detected {raster_analysis['estimated_symbol_count']} candidate symbols; proposed split crops were created for review from {asset_format.upper()} input.",
                        )
                        if ocr_label_summary.get("available"):
                            add_trace(
                                evidence_trace,
                                "ocr_label_extraction",
                                "passed" if ocr_label_summary.get("assigned_count") else "skipped",
                                f"Assigned OCR-derived labels to {ocr_label_summary.get('assigned_count', 0)} proposed child regions.",
                            )
                    else:
                        single_symbol_candidate = build_single_symbol_candidate(
                            task,
                            normalized,
                            raster_analysis,
                            split_plan,
                            ocr_label_summary,
                        )
                        normalized["single_symbol_candidate"] = single_symbol_candidate
                        additional_artifacts.append(
                            {
                                "artifact_type": "single_symbol_raster_candidate",
                                "payload_json": single_symbol_candidate,
                            }
                        )
                        add_trace(
                            evidence_trace,
                            "raster_sheet_analysis",
                            "passed",
                            "Detected a single candidate symbol in the PNG sheet.",
                        )
                        if ocr_label_summary.get("available"):
                            add_trace(
                                evidence_trace,
                                "ocr_label_extraction",
                                "skipped",
                                "OCR label extraction ran, but raster splitting was not recommended for this PNG.",
                            )
            except (OSError, ValueError, zlib.error) as exc:
                add_defect(defects, "VLAD-RASTER-003", "high", f"PNG sheet analysis failed: {exc}")
                add_trace(
                    evidence_trace,
                    "raster_sheet_analysis",
                    "failed",
                    "Raster analysis could not decode or analyze the PNG asset.",
                )
                decision = "escalate"
                confidence = min(confidence, 0.28)
                escalation_target = "human_reviewer"
            finally:
                if temp_analysis_path is not None:
                    try:
                        temp_analysis_path.unlink(missing_ok=True)
                    except OSError:
                        pass

    if any(defect["severity"] == "critical" for defect in defects):
        decision = "fail"
        confidence = min(confidence, 0.99)
    elif defects:
        decision = "fail"
        confidence = min(confidence, 0.92)

    if not expected_checks:
        decision = "escalate"
        confidence = 0.2
        escalation_target = "human_reviewer"
        add_trace(evidence_trace, "task", "failed", "Task did not declare expected_checks.")
        add_defect(defects, "VLAD-TASK-001", "high", "Task payload is missing expected_checks.")

    return {
        "queue_item_id": queue_item_id,
        "agent": "vlad",
        "schema_version": SCHEMA_VERSION,
        "decision": decision,
        "confidence": round(confidence, 2),
        "escalation_target": escalation_target,
        "normalized_technical_metadata": normalized,
        "defects": defects,
        "evidence_trace": evidence_trace,
        "additional_artifacts": additional_artifacts,
        "review_recommendation": review_recommendation,
        "control_exceptions": control_exceptions,
    }


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def queue_status_for_decision(decision):
    if decision == "escalate":
        return "escalated"
    return "completed"


def queue_status_for_artifact(task, artifact):
    if task.get("task_type") == "symbol_graphic_change_request":
        return "completed"
    return queue_status_for_decision(artifact["decision"])


def queue_item_payload_to_task(queue_item):
    payload = copy.deepcopy(queue_item.get("payload_json") or {})
    payload["queue_item_id"] = queue_item.get("id")
    payload["source_type"] = queue_item.get("source_type")
    payload["source_id"] = queue_item.get("source_id")
    return payload


def build_libby_return_queue_item(queue_item, task, artifact, timestamp):
    metadata = artifact.get("normalized_technical_metadata") or {}
    review_case_id = metadata.get("review_case_id") or task.get("review_case_id")
    queue_id = f"aqi-libby-vlad-return-{str(review_case_id or queue_item['id'])[:8]}-{timestamp}"
    return {
        "id": queue_id,
        "agent_id": "libby",
        "source_type": "vlad_graphic_change_result",
        "source_id": task.get("review_decision_id") or review_case_id,
        "status": "queued",
        "priority": queue_item.get("priority") or "medium",
        "payload_json": {
            "task_type": "vlad_graphic_update_completed",
            "review_case_id": review_case_id,
            "review_decision_id": task.get("review_decision_id"),
            "vlad_queue_item_id": queue_item.get("id"),
            "candidate_symbol_id": metadata.get("candidate_symbol_id"),
            "candidate_symbol_name": metadata.get("candidate_symbol_name"),
            "origin_object_key": metadata.get("origin_object_key"),
            "origin_file_name": metadata.get("origin_file_name"),
            "vlad_result_summary": (artifact.get("additional_artifacts") or [{}])[0].get("payload_json", {}).get("result_summary"),
            "vlad_result": artifact,
            "next_review_agent": "daisy",
        },
        "confidence": None,
        "escalation_reason": None,
        "created_at": utc_now(),
        "started_at": None,
        "completed_at": None,
    }


def build_btx_libby_queue_item(queue_item, task, child, timestamp):
    """Hand each extracted BTX symbol to Libby as an isolated candidate."""
    preview = next((asset for asset in child["assets"] if asset["format"] == "png"), None)
    vector = next((asset for asset in child["assets"] if asset["format"] == "svg"), None)
    queue_id = f"aqi-libby-btx-{str(queue_item['id'])[:18]}-{child['ordinal']:03d}-{timestamp}"
    btx_subject = str(child.get("candidate_title") or "").strip()
    is_door_subject = "door" in btx_subject.casefold()
    classification_hints = {"category": "Doors", "discipline": "Architectural"} if is_door_subject else {}
    return {
        "id": queue_id,
        "agent_id": "libby",
        "source_type": "btx_extracted_symbol",
        "source_id": child["child_symbol_id"],
        "status": "queued",
        "priority": queue_item.get("priority") or "medium",
        "payload_json": {
            "task_type": "btx_extracted_symbol",
            "candidate_symbol_id": child["child_symbol_id"],
            "candidate_title": child["candidate_title"],
            "btx_subject": btx_subject,
            "btx_annotation_type": child.get("annotation_type"),
            "classification_hints": classification_hints,
            "origin_file_name": task.get("original_filename") or task.get("asset_path"),
            "asset_path": (preview or vector or child["assets"][0])["path"],
            "asset_format": (preview or vector or child["assets"][0])["format"],
            "visual_assets": {
                "preview": preview,
                "derivatives": child["assets"],
            },
            "source_btx_sha256": child["source_btx_sha256"],
            "source_package_id": child.get("source_package_id"),
            "package_member": child.get("package_member"),
            "btx_ordinal": child["ordinal"],
            "btx_internal_name": child.get("internal_name"),
            "btx_warnings": child.get("warnings", []),
            "source_notes": task.get("source_notes"),
            "contributor_declaration": task.get("contributor_declaration"),
        },
        "confidence": None,
        "escalation_reason": None,
        "created_at": utc_now(),
        "started_at": None,
        "completed_at": None,
    }


def process_queue_item(queue_item_path, runtime_root, persist_db=False, db_env_file=None, storage_env_file=None):
    queue_item_path = Path(queue_item_path)
    runtime_root = Path(runtime_root)

    with queue_item_path.open("r", encoding="utf-8") as handle:
        queue_item = json.load(handle)

    if queue_item.get("agent_id") != "vlad":
        raise ValueError("Queue item agent_id must be 'vlad'.")

    started_at = utc_now()
    queue_item["status"] = "running"
    queue_item["started_at"] = started_at
    write_json(queue_item_path, queue_item)
    notification_status = {
        "started": send_agent_status_update("vlad", "started", queue_item),
        "completed": None,
    }

    task = queue_item_payload_to_task(queue_item)
    task["runtime_root"] = str(runtime_root)
    artifact = run_validation_task(task)
    completed_at = utc_now()

    queue_item["status"] = queue_status_for_artifact(task, artifact)
    queue_item["confidence"] = artifact["confidence"]
    queue_item["escalation_reason"] = (
        "validation_requires_escalation" if artifact["decision"] == "escalate" else None
    )
    queue_item["completed_at"] = completed_at
    write_json(queue_item_path, queue_item)

    run_id = stamp_id("arun", queue_item["id"])
    run_record = {
        "id": run_id,
        "queue_item_id": queue_item["id"],
        "model": resolve_vlad_model(),
        "prompt_version": PROMPT_VERSION,
        "tool_trace_json": artifact["evidence_trace"],
        "result_status": queue_item["status"],
        "started_at": started_at,
        "completed_at": completed_at
    }

    artifact_id = stamp_id("aout", queue_item["id"])
    output_artifact_record = {
        "id": artifact_id,
        "queue_item_id": queue_item["id"],
        "artifact_type": "validation_report",
        "schema_version": artifact["schema_version"],
        "payload_json": artifact,
        "created_at": completed_at
    }

    report_id = stamp_id("vr", queue_item["id"])
    validation_report = {
        "id": report_id,
        "queue_item_id": queue_item["id"],
        "source_type": queue_item.get("source_type"),
        "source_id": queue_item.get("source_id"),
        "validation_status": artifact["decision"],
        "defect_count": len(artifact["defects"]),
        "normalized_payload_json": artifact["normalized_technical_metadata"],
        "report_json": {
            "decision": artifact["decision"],
            "confidence": artifact["confidence"],
            "escalation_target": artifact["escalation_target"],
            "defects": artifact["defects"],
            "evidence_trace": artifact["evidence_trace"]
        },
        "created_at": completed_at
    }

    db_persistence = None
    additional_db_records = {"artifacts": [], "review_case": None, "control_exceptions": []}
    should_persist_db = persist_db or env_flag("SYMGOV_PERSIST_TO_DB")
    bridge = RuntimePersistenceBridge(env_file=db_env_file) if should_persist_db else None
    derivative_children = (artifact["normalized_technical_metadata"].get("derivative_manifest") or {}).get("children", [])
    btx_children = (artifact["normalized_technical_metadata"].get("btx_library") or {}).get("children", [])
    expected_derivatives = len(derivative_children) + sum(len(child.get("assets") or []) for child in btx_children)
    expected_children = len(derivative_children)
    expected_libby = len(btx_children)
    persistence_boundary = None
    if should_persist_db:
        persistence_boundary = new_persistence_boundary(
            queue_item_id=queue_item["id"],
            report_id=report_id,
            expected_derivatives=expected_derivatives,
            expected_children=expected_children,
            expected_libby=expected_libby,
        )
        additional_db_records["persistence_boundary"] = persistence_boundary
        add_persistence_event(
            persistence_boundary,
            "persistence_started",
            "started",
            "Beginning durable Vlad persistence boundary execution.",
        )
    if should_persist_db:
        assert bridge is not None
        phase = "persistence_started"
        try:
            for child in derivative_children:
                region_index = child.get("region_index")
                if isinstance(region_index, int):
                    object_name = f"{region_index:03d}-{child['file_name']}"
                else:
                    object_name = child["file_name"]
                object_key = f"derived-splits/{queue_item['id']}/{object_name}"
                phase = "attachment_created"
                attachment = bridge.create_attachment(
                    parent_type="validation_report",
                    parent_id=report_id,
                    filename=child["file_name"],
                    object_key=object_key,
                    content_type=child.get("content_type") or "image/png",
                    size_bytes=child["size_bytes"],
                    sha256=child["sha256"],
                )
                add_persistence_event(
                    persistence_boundary,
                    "attachment_created",
                    "passed",
                    "Created derivative attachment record.",
                    object_key=object_key,
                )
                phase = "upload_completed"
                storage_result = bridge.upload_file(
                    object_key=object_key,
                    path=child["path"],
                    content_type=child.get("content_type") or "image/png",
                    env_file=storage_env_file,
                )
                add_persistence_event(
                    persistence_boundary,
                    "upload_completed",
                    "passed",
                    "Uploaded derivative asset to object storage.",
                    object_key=object_key,
                )
                child["attachment_id"] = attachment["id"]
                child["attachment_object_key"] = attachment["object_key"]
                child["attachment_storage"] = storage_result
                persistence_boundary["actual_derivative_count"] += 1
                persistence_boundary["actual_child_count"] += 1
            dxf_derivative = artifact["normalized_technical_metadata"].get("dxf_derivative")
            if dxf_derivative:
                phase = "attachment_created"
                persist_dxf_derivative_assets(
                    bridge,
                    report_id=report_id,
                    derivative_manifest=dxf_derivative,
                    storage_env_file=storage_env_file,
                )
                add_persistence_event(
                    persistence_boundary,
                    "upload_completed",
                    "passed",
                    "Uploaded DXF-derived preview asset.",
                    object_key=dxf_derivative.get("object_key"),
                )
                persistence_boundary["actual_derivative_count"] += 1
            for child in btx_children:
                for generated_asset in child.get("assets") or []:
                    phase = "attachment_created"
                    attachment = bridge.create_attachment(
                        parent_type="validation_report",
                        parent_id=report_id,
                        filename=generated_asset["file_name"],
                        object_key=generated_asset["object_key"],
                        content_type=generated_asset["content_type"],
                        size_bytes=generated_asset["size_bytes"],
                        sha256=generated_asset["sha256"],
                    )
                    add_persistence_event(
                        persistence_boundary,
                        "attachment_created",
                        "passed",
                        "Created BTX generated-asset attachment record.",
                        object_key=generated_asset["object_key"],
                    )
                    phase = "upload_completed"
                    storage_result = bridge.upload_file(
                        object_key=generated_asset["object_key"], path=generated_asset["path"],
                        content_type=generated_asset["content_type"], env_file=storage_env_file,
                    )
                    add_persistence_event(
                        persistence_boundary,
                        "upload_completed",
                        "passed",
                        "Uploaded BTX generated asset to object storage.",
                        object_key=generated_asset["object_key"],
                    )
                    generated_asset["attachment_id"] = attachment["id"]
                    generated_asset["attachment_object_key"] = attachment["object_key"]
                    generated_asset["attachment_storage"] = storage_result
                    persistence_boundary["actual_derivative_count"] += 1
            validation_report["normalized_payload_json"] = artifact["normalized_technical_metadata"]
            output_artifact_record["payload_json"] = artifact
        except Exception as exc:
            add_persistence_event(
                persistence_boundary,
                "terminal_durable_failure",
                "failed",
                f"Persistence boundary failed during {phase}: {exc}",
                failed_phase=phase,
            )
            raise RuntimeError(
                f"VLAD persistence boundary failure [{phase}] correlation={persistence_boundary['correlation_id']} "
                f"queue={queue_item['id']} report={report_id} expected_derivatives={expected_derivatives} "
                f"actual_derivatives={persistence_boundary['actual_derivative_count']} "
                f"expected_children={expected_children} actual_children={persistence_boundary['actual_child_count']}: {exc}"
            ) from exc

    write_json(runtime_root / "agent_runs" / f"{run_id}.json", run_record)
    write_json(runtime_root / "agent_output_artifacts" / f"{artifact_id}.json", output_artifact_record)
    write_json(runtime_root / "validation_reports" / f"{report_id}.json", validation_report)

    additional_artifact_paths = []
    for item in artifact.get("additional_artifacts", []):
        extra_artifact_id = stamp_id("aout", f"{queue_item['id']}-{item['artifact_type']}")
        extra_artifact_record = {
            "id": extra_artifact_id,
            "queue_item_id": queue_item["id"],
            "artifact_type": item["artifact_type"],
            "schema_version": artifact["schema_version"],
            "payload_json": item["payload_json"],
            "created_at": completed_at,
        }
        extra_path = runtime_root / "agent_output_artifacts" / f"{extra_artifact_id}.json"
        write_json(extra_path, extra_artifact_record)
        additional_artifact_paths.append(str(extra_path))

    downstream_queue_path = None
    downstream_queue_paths = []
    if task.get("task_type") == "symbol_graphic_change_request":
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        libby_queue_item = build_libby_return_queue_item(queue_item, task, artifact, timestamp)
        libby_runtime_root = Path("/data/.openclaw/workspaces/libby/runtime")
        downstream_queue_path = libby_runtime_root / "agent_queue_items" / f"{libby_queue_item['id']}.json"
        write_json(downstream_queue_path, libby_queue_item)
        downstream_queue_paths.append(str(downstream_queue_path))
    elif artifact["normalized_technical_metadata"].get("btx_library", {}).get("children"):
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        libby_runtime_root = Path("/data/.openclaw/workspaces/libby/runtime")
        for child in artifact["normalized_technical_metadata"]["btx_library"]["children"]:
            libby_queue_item = build_btx_libby_queue_item(queue_item, task, child, timestamp)
            child_path = libby_runtime_root / "agent_queue_items" / f"{libby_queue_item['id']}.json"
            write_json(child_path, libby_queue_item)
            downstream_queue_paths.append(str(child_path))

    if should_persist_db:
        assert bridge is not None
        add_persistence_event(persistence_boundary, "validation_report_persisted", "started", "Persisting validation report and execution records.")
        db_persistence = bridge.persist_agent_execution(
            queue_item=queue_item,
            run_record=run_record,
            output_artifact_record=output_artifact_record,
            durable_record=validation_report,
            durable_kind="validation_report",
        )
        add_persistence_event(persistence_boundary, "validation_report_persisted", "passed", "Persisted validation report and execution records.")
        if downstream_queue_path:
            additional_db_records["downstream_queue_item"] = bridge.upsert_agent_queue_item(libby_queue_item)
        elif downstream_queue_paths:
            additional_db_records["downstream_queue_items"] = []
            for child in artifact["normalized_technical_metadata"]["btx_library"]["children"]:
                queued = build_btx_libby_queue_item(queue_item, task, child, timestamp)
                additional_db_records["downstream_queue_items"].append(bridge.upsert_agent_queue_item(queued))
            persistence_boundary["actual_libby_queue_count"] = len(additional_db_records["downstream_queue_items"])
        add_persistence_event(
            persistence_boundary,
            "libby_queue_records_persisted",
            "passed",
            "Persisted downstream Libby queue records.",
            actual_count=persistence_boundary["actual_libby_queue_count"],
        )
        edited_asset_records = []
        for edited_asset in artifact["normalized_technical_metadata"].get("edited_assets") or []:
            attachment = bridge.create_attachment(
                parent_type="validation_report",
                parent_id=report_id,
                filename=edited_asset["file_name"],
                object_key=edited_asset["object_key"],
                content_type=edited_asset["content_type"],
                size_bytes=edited_asset["size_bytes"],
                sha256=edited_asset["sha256"],
            )
            storage_result = bridge.upload_file(
                object_key=edited_asset["object_key"],
                path=edited_asset["path"],
                content_type=edited_asset["content_type"],
                env_file=storage_env_file,
            )
            edited_asset["attachment_id"] = attachment["id"]
            edited_asset["attachment_object_key"] = attachment["object_key"]
            edited_asset["attachment_storage"] = storage_result
            edited_asset_records.append(attachment)
        if edited_asset_records:
            additional_db_records["edited_assets"] = edited_asset_records
        for item in artifact.get("additional_artifacts", []):
            created = bridge.create_agent_output_artifact(
                queue_item_id=queue_item["id"],
                artifact_type=item["artifact_type"],
                schema_version=artifact["schema_version"],
                payload_json=item["payload_json"],
                created_at=completed_at,
            )
            additional_db_records["artifacts"].append(created)
        review_recommendation = artifact.get("review_recommendation")
        if review_recommendation:
            additional_db_records["review_case"] = bridge.create_review_case(
                source_entity_type="validation_report",
                source_entity_id=report_id,
                current_stage=review_recommendation["current_stage"],
                escalation_level=review_recommendation["escalation_level"],
                opened_at=completed_at,
            )
            add_persistence_event(persistence_boundary, "review_case_created", "passed", "Created durable review case.", review_case_id=additional_db_records["review_case"].get("id"))
        else:
            add_persistence_event(persistence_boundary, "review_case_created", "skipped", "No review recommendation required a durable review case.")
        for item in artifact.get("control_exceptions", []):
            created = bridge.create_control_exception(
                source_type="validation_report",
                source_id=report_id,
                severity=item["severity"],
                rule_code=item["rule_code"],
                detail=item["detail"],
                created_at=completed_at,
            )
            additional_db_records["control_exceptions"].append(created)
        add_persistence_event(persistence_boundary, "terminal_durable_success", "passed", "Completed all durable Vlad persistence phases.")
        write_json(runtime_root / "validation_reports" / f"{report_id}.json", validation_report)

    notification_status["completed"] = send_agent_status_update(
        "vlad",
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
        "additional_artifact_paths": additional_artifact_paths,
        "downstream_queue_item_path": str(downstream_queue_path) if downstream_queue_path else None,
        "downstream_queue_item_paths": downstream_queue_paths,
        "downstream_agent": "libby" if downstream_queue_path else None,
        "validation_report_path": str(runtime_root / "validation_reports" / f"{report_id}.json"),
        "db_persistence": db_persistence,
        "additional_db_records": additional_db_records,
        "notifications": notification_status,
        "artifact": artifact
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Run local Vlad validation in task or queue mode.")
    parser.add_argument("--input", help="Path to a JSON task file.")
    parser.add_argument("--output", help="Path to write the JSON validation artifact.")
    parser.add_argument("--queue-item", help="Path to an agent_queue_item JSON record.")
    parser.add_argument("--runtime-root", help="Root directory for local file-backed queue records.")
    parser.add_argument(
        "--cleanup-queue-item",
        action="store_true",
        help="Remove the specified queue item from this agent's runtime/agent_queue_items directory.",
    )
    parser.add_argument(
        "--persist-db",
        action="store_true",
        help="Also mirror queue, run, artifact, and validation records into the Symgov database.",
    )
    parser.add_argument(
        "--db-env-file",
        help="Path to the Symgov database env file used with --persist-db.",
    )
    parser.add_argument(
        "--storage-env-file",
        help="Path to the Symgov storage env file used to upload derivative artifacts.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

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

    artifact = run_validation_task(task)
    write_json(output_path, artifact)


if __name__ == "__main__":
    main()
