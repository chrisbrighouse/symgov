from __future__ import annotations

from pathlib import Path
from typing import Any

PREVIEWABLE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/svg+xml"}
PREVIEWABLE_FORMATS = {"png", "jpg", "jpeg", "svg"}
NON_PREVIEWABLE_CONTENT_TYPES = {
    "application/dxf",
    "application/x-dxf",
    "application/zip",
    "application/x-zip-compressed",
    "multipart/x-zip",
}
NON_PREVIEWABLE_FORMATS = {"dxf", "zip"}


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _extension(filename: Any) -> str | None:
    text = _clean(filename)
    if not text:
        return None
    suffix = Path(text).suffix
    return suffix[1:] if suffix else None


def _clean_content_type(value: Any) -> str | None:
    text = _clean(value)
    if not text:
        return None
    return text.split(";", 1)[0].strip() or None


def _format_from_content_type(content_type: str | None) -> str | None:
    normalized = _clean_content_type(content_type)
    if normalized == "image/svg+xml":
        return "svg"
    if normalized == "image/jpeg" or normalized == "image/jpg":
        return "jpg"
    if normalized == "image/png":
        return "png"
    if normalized in {"application/dxf", "application/x-dxf"}:
        return "dxf"
    if normalized in {"application/zip", "application/x-zip-compressed", "multipart/x-zip"}:
        return "zip"
    return None


def canonical_asset_format(value: Any) -> str | None:
    """Return the workspace/API canonical format label for an asset."""
    normalized = _clean(value)
    if normalized in {"jpeg", "jpe"}:
        return "jpg"
    return normalized


def content_type_for_format(format: Any, filename: Any = None, content_type: Any = None) -> str | None:
    """Prefer browser-useful media types for known formats even when storage says octet-stream."""
    normalized_format = canonical_asset_format(format) or canonical_asset_format(_extension(filename))
    normalized_content_type = _clean_content_type(content_type)
    if normalized_format == "jpg":
        return "image/jpeg"
    if normalized_format == "png":
        return "image/png"
    if normalized_format == "svg":
        return "image/svg+xml"
    if normalized_format == "dxf":
        return "application/dxf"
    return normalized_content_type


def _infer_format(content_type: Any = None, format: Any = None, filename: Any = None) -> str | None:
    return canonical_asset_format(format) or canonical_asset_format(_extension(filename)) or _format_from_content_type(_clean(content_type))


def is_browser_previewable(
    content_type: str | None = None,
    format: str | None = None,
    filename: str | None = None,
) -> bool:
    """Return whether an asset can be shown directly by browser image preview UI."""
    normalized_content_type = _clean_content_type(content_type)
    normalized_format = _clean(format)
    ext = _extension(filename)

    if normalized_content_type in NON_PREVIEWABLE_CONTENT_TYPES:
        return False
    if normalized_format in NON_PREVIEWABLE_FORMATS:
        return False
    if ext in NON_PREVIEWABLE_FORMATS:
        return False

    return bool(
        normalized_content_type in PREVIEWABLE_CONTENT_TYPES
        or normalized_format in PREVIEWABLE_FORMATS
        or ext in PREVIEWABLE_FORMATS
    )


def _asset_from_mapping(asset: Any) -> dict[str, Any] | None:
    if not isinstance(asset, dict):
        return None
    raw_object_key = asset.get("object_key") or asset.get("key")
    if raw_object_key is None:
        return None
    object_key = str(raw_object_key).strip()
    if not object_key:
        return None

    normalized: dict[str, Any] = dict(asset)
    normalized["object_key"] = object_key
    normalized.pop("key", None)
    if not normalized.get("filename"):
        normalized["filename"] = Path(object_key).name
    if normalized.get("content_type"):
        normalized["content_type"] = _clean_content_type(normalized.get("content_type")) or normalized.get("content_type")
    inferred = _infer_format(
        normalized.get("content_type"),
        format=normalized.get("format"),
        filename=normalized.get("filename") or object_key,
    )
    if inferred:
        normalized["format"] = inferred
        normalized["content_type"] = content_type_for_format(
            inferred,
            filename=normalized.get("filename") or object_key,
            content_type=normalized.get("content_type"),
        ) or normalized.get("content_type")
    return normalized


def normalize_asset(asset: Any) -> dict[str, Any] | None:
    """Normalize an asset mapping into a stable object-key keyed manifest item."""
    return _asset_from_mapping(asset)


def _is_previewable_asset(asset: dict[str, Any] | None) -> bool:
    if not asset:
        return False
    return is_browser_previewable(
        content_type=asset.get("content_type"),
        format=asset.get("format"),
        filename=asset.get("filename") or asset.get("object_key"),
    )


def _first_previewable(assets: Any) -> dict[str, Any] | None:
    if not isinstance(assets, list):
        return None
    for item in assets:
        asset = _asset_from_mapping(item)
        if _is_previewable_asset(asset):
            return asset
    return None


def _explicit_preview_asset(payload: dict[str, Any]) -> dict[str, Any] | None:
    object_key = payload.get("preview_object_key")
    asset = {
        "object_key": object_key,
        "filename": payload.get("preview_filename") or payload.get("filename"),
        "content_type": payload.get("preview_content_type") or payload.get("content_type"),
        "format": payload.get("preview_format"),
        "role": payload.get("preview_role") or "preview",
    }
    return _asset_from_mapping(asset)


def choose_preview_asset(
    payload: dict[str, Any] | None,
    fallback_source_asset: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Choose the best browser-previewable asset from a symbol asset payload."""
    payload = payload or {}
    raw_visual_assets = payload.get("visual_assets")
    visual_assets: dict[str, Any] = raw_visual_assets if isinstance(raw_visual_assets, dict) else {}

    candidates = [
        _explicit_preview_asset(payload),
        _asset_from_mapping(visual_assets.get("preview")),
        _first_previewable(visual_assets.get("derivatives")),
        _first_previewable(visual_assets.get("source_assets")),
        _asset_from_mapping(fallback_source_asset),
    ]
    for candidate in candidates:
        if _is_previewable_asset(candidate):
            return candidate
    return None


def _add_download(seen: set[str], downloads: list[dict[str, Any]], asset: Any) -> None:
    normalized = _asset_from_mapping(asset)
    if not normalized:
        return
    object_key = normalized["object_key"]
    if object_key in seen:
        return
    seen.add(object_key)
    downloads.append(normalized)


def _add_asset(seen: set[str], assets: list[dict[str, Any]], asset: Any) -> None:
    normalized = _asset_from_mapping(asset)
    if not normalized:
        return
    object_key = normalized["object_key"]
    if object_key in seen:
        return
    seen.add(object_key)
    assets.append(normalized)


def _is_generated_non_downloadable_preview(asset: Any) -> bool:
    if not isinstance(asset, dict):
        return False
    role = _clean(asset.get("role")) or ""
    if asset.get("downloadable") is True:
        return False
    return role.startswith("generated") or role in {"derivative", "preview_derivative"}


def list_download_assets(
    payload: dict[str, Any] | None,
    fallback_source_asset: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """List downloadable assets, excluding generated preview derivatives by default."""
    payload = payload or {}
    raw_visual_assets = payload.get("visual_assets")
    visual_assets: dict[str, Any] = raw_visual_assets if isinstance(raw_visual_assets, dict) else {}
    seen: set[str] = set()
    downloads: list[dict[str, Any]] = []

    _add_download(seen, downloads, fallback_source_asset)

    source_assets = visual_assets.get("source_assets")
    if isinstance(source_assets, list):
        for asset in source_assets:
            _add_download(seen, downloads, asset)

    preview = visual_assets.get("preview")
    if not _is_generated_non_downloadable_preview(preview):
        _add_download(seen, downloads, preview)

    structured_downloads = payload.get("downloads")
    if isinstance(structured_downloads, list):
        for asset in structured_downloads:
            _add_download(seen, downloads, asset)

    derivatives = visual_assets.get("derivatives")
    if isinstance(derivatives, list):
        for asset in derivatives:
            if isinstance(asset, dict) and asset.get("downloadable") is True:
                _add_download(seen, downloads, asset)

    return downloads


def list_available_assets(
    payload: dict[str, Any] | None,
    fallback_source_asset: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """List all distinct source, preview, and derivative assets carried by a symbol payload."""
    payload = payload or {}
    raw_visual_assets = payload.get("visual_assets")
    visual_assets: dict[str, Any] = raw_visual_assets if isinstance(raw_visual_assets, dict) else {}
    seen: set[str] = set()
    assets: list[dict[str, Any]] = []

    source_assets = visual_assets.get("source_assets")
    if isinstance(source_assets, list):
        for asset in source_assets:
            _add_asset(seen, assets, asset)

    _add_asset(seen, assets, visual_assets.get("preview"))

    structured_downloads = payload.get("downloads")
    if isinstance(structured_downloads, list):
        for asset in structured_downloads:
            _add_asset(seen, assets, asset)

    derivatives = visual_assets.get("derivatives")
    if isinstance(derivatives, list):
        for asset in derivatives:
            _add_asset(seen, assets, asset)

    _add_asset(seen, assets, fallback_source_asset)

    return assets
