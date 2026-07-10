from __future__ import annotations

from pathlib import Path
import re
from typing import Any

CATALOG_DISCIPLINE_ORDER = [
    "Electrical",
    "Fire & Life Safety",
    "Piping / P&ID",
    "Process",
    "Instrumentation & Controls",
    "Mechanical",
    "HVAC",
    "Civil / Structural",
    "Architectural",
    "Safety / Signage",
    "General / Annotation",
]

CATALOG_CATEGORY_ORDER = [
    "Valves",
    "Pumps",
    "Vessels / Tanks",
    "Pipework / Fittings",
    "Instruments",
    "Fire Alarm Devices",
    "Sensors / Detectors",
    "Motors / Drives",
    "Electrical Devices",
    "Switchgear / Distribution",
    "Lighting",
    "Controls",
    "Actuators",
    "Heating / HVAC",
    "Safety Devices",
    "Annotations / Tags",
    "Drawing Symbols",
    "Doors",
    "Equipment",
    "Miscellaneous / Unclassified",
]

CATALOG_USE_CASE_ORDER = [
    "Insert into CAD drawing",
    "Mark up / annotate drawing",
    "Use in PDF/report",
    "Use as web/app icon",
    "Use as reference only",
    "Compare against standard",
]

FORMAT_ORDER = ["DXF", "DWG", "SVG", "PNG", "JPG", "JPEG", "PDF", "RVT", "RFA", "IFC", "ZIP", "JSON"]

_DISCIPLINE_MAP = {
    "electrical": ["Electrical"],
    "elec": ["Electrical"],
    "fire": ["Fire & Life Safety"],
    "fire_alarm": ["Fire & Life Safety", "Electrical"],
    "fire_alarms": ["Fire & Life Safety", "Electrical"],
    "fire_life_safety": ["Fire & Life Safety"],
    "piping": ["Piping / P&ID"],
    "p_id": ["Piping / P&ID"],
    "pid": ["Piping / P&ID"],
    "process": ["Process"],
    "process_instrumentation": ["Instrumentation & Controls", "Piping / P&ID"],
    "instrumentation": ["Instrumentation & Controls"],
    "controls": ["Instrumentation & Controls"],
    "instrumentation_controls": ["Instrumentation & Controls"],
    "mechanical": ["Mechanical"],
    "mech": ["Mechanical"],
    "hvac": ["HVAC"],
    "civil": ["Civil / Structural"],
    "structural": ["Civil / Structural"],
    "architectural": ["Architectural"],
    "safety": ["Safety / Signage"],
    "signage": ["Safety / Signage"],
    "general": ["General / Annotation"],
    "unknown_discipline": ["General / Annotation"],
    "": [],
}

_CATEGORY_MAP = {
    "valve": ["Valves"],
    "valves": ["Valves"],
    "valve_symbol": ["Valves"],
    "gate_valve": ["Valves"],
    "gate_valves": ["Valves"],
    "pump": ["Pumps"],
    "pumps": ["Pumps"],
    "vessel": ["Vessels / Tanks"],
    "vessels": ["Vessels / Tanks"],
    "tank": ["Vessels / Tanks"],
    "tanks": ["Vessels / Tanks"],
    "pipework": ["Pipework / Fittings"],
    "pipe": ["Pipework / Fittings"],
    "fitting": ["Pipework / Fittings"],
    "fittings": ["Pipework / Fittings"],
    "instrument": ["Instruments"],
    "instruments": ["Instruments"],
    "motor": ["Motors / Drives"],
    "motors": ["Motors / Drives"],
    "drive": ["Motors / Drives"],
    "drives": ["Motors / Drives"],
    "smallpower": ["Electrical Devices"],
    "lighting": ["Lighting"],
    "heating": ["Heating / HVAC"],
    "hvac": ["Heating / HVAC"],
    "actuator": ["Actuators"],
    "actuators": ["Actuators"],
    "control": ["Controls"],
    "controls": ["Controls"],
    "counter": ["Instruments"],
    "cylinder": ["Equipment"],
    "envelope": ["Equipment"],
    "stirrer": ["Equipment"],
    "symbol": ["Drawing Symbols"],
    "symbol_sheet": ["Drawing Symbols"],
    "door": ["Doors"],
    "doors": ["Doors"],
    "annotation": ["Annotations / Tags"],
    "tag": ["Annotations / Tags"],
    "tags": ["Annotations / Tags"],
    "": [],
}

_CONTENT_TYPE_FORMATS = {
    "image/svg+xml": "SVG",
    "image/png": "PNG",
    "image/jpeg": "JPG",
    "image/jpg": "JPG",
    "application/pdf": "PDF",
    "application/dxf": "DXF",
    "application/x-dxf": "DXF",
    "application/zip": "ZIP",
    "application/x-zip-compressed": "ZIP",
    "multipart/x-zip": "ZIP",
    "application/json": "JSON",
}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def compact_unique(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    compacted: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        compacted.append(text)
    return compacted


def sort_by_preferred_order(values: list[Any], preferred_order: list[str]) -> list[str]:
    order = {value.lower(): index for index, value in enumerate(preferred_order)}

    def sort_key(value: str) -> tuple[int, str]:
        return (order.get(value.lower(), 10**9), value.lower())

    return sorted(compact_unique(values), key=sort_key)


def _normalized_key(value: Any) -> str:
    return re.sub(r"[\s-]+", "_", str(value or "").strip().lower())


def _text_tokens(*values: Any) -> list[str]:
    tokens: list[str] = []
    for value in values:
        if isinstance(value, list):
            tokens.extend(_text_tokens(*value))
        elif isinstance(value, dict):
            tokens.extend(_text_tokens(*value.values()))
        else:
            tokens.append(str(value or "").lower())
    return tokens


def symbol_context_text(symbol: Any = None) -> str:
    symbol = symbol or {}
    payload = _get(symbol, "payload") or {}
    return " ".join(
        _text_tokens(
            _get(symbol, "name"),
            _get(symbol, "displayName"),
            _get(symbol, "display_name"),
            _get(symbol, "category"),
            _get(symbol, "discipline"),
            _get(symbol, "summary"),
            _get(symbol, "description"),
            _get(symbol, "keywords"),
            _get(symbol, "downloads"),
            _get(symbol, "downloadAssets"),
            _get(symbol, "download_assets"),
            _get(payload, "name"),
            _get(payload, "description"),
            _get(payload, "summary"),
            _get(payload, "keywords"),
            _get(payload, "source_file"),
            _get(payload, "source_file_name"),
        )
    )


def normalize_catalog_discipline(value: Any) -> list[str]:
    raw = str(value or "").strip()
    normalized = _normalized_key(raw)
    return list(_DISCIPLINE_MAP.get(normalized, [raw] if raw else []))


def normalize_catalog_category(value: Any, symbol: Any = None) -> list[str]:
    raw = str(value or "").strip()
    normalized = _normalized_key(raw)
    context = symbol_context_text(symbol)
    categories: list[str] = []

    if re.search(r"fire|smoke|heat|detector|call\s?point|break\s?glass|sounder|beacon|alarm", context):
        categories.append("Fire Alarm Devices")
    if re.search(r"detector|sensor|smoke|heat|co\b|carbon", context):
        categories.append("Sensors / Detectors")

    categories.extend(_CATEGORY_MAP.get(normalized, [raw] if raw else []))
    return sort_by_preferred_order(categories or ["Miscellaneous / Unclassified"], CATALOG_CATEGORY_ORDER)


def _format_from_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    content_type = text.lower().split(";", 1)[0]
    if content_type in _CONTENT_TYPE_FORMATS:
        return _CONTENT_TYPE_FORMATS[content_type]

    path_text = text.split("?", 1)[0].split("#", 1)[0]
    suffix = Path(path_text).suffix
    clean = (suffix[1:] if suffix else text).strip().lower()
    clean = re.sub(r"^(image|application)/", "", clean)
    if clean == "svg+xml":
        return "SVG"
    if clean in {"jpeg", "jpe"}:
        return "JPG"
    mapped = clean.upper()
    if mapped and len(mapped) <= 8:
        return mapped
    return None


def _push_format(formats: list[str], value: Any) -> None:
    mapped = _format_from_text(value)
    if mapped:
        formats.append(mapped)


def _push_asset_format(formats: list[str], asset: Any) -> None:
    if isinstance(asset, str):
        _push_format(formats, asset)
        return
    if not isinstance(asset, dict):
        return
    _push_format(
        formats,
        asset.get("format")
        or asset.get("filename")
        or asset.get("content_type")
        or asset.get("contentType")
        or asset.get("object_key"),
    )


def available_formats_for_symbol(symbol: Any = None) -> list[str]:
    symbol = symbol or {}
    formats: list[str] = []
    _push_format(formats, _get(symbol, "format"))
    _push_format(formats, _get(symbol, "contentType") or _get(symbol, "content_type"))

    for download in _as_list(_get(symbol, "downloads")):
        _push_asset_format(formats, download)
    for asset in _as_list(_get(symbol, "downloadAssets") or _get(symbol, "download_assets")):
        _push_asset_format(formats, asset)

    payload = _get(symbol, "payload") or {}
    _push_format(formats, _get(payload, "format"))
    _push_format(formats, _get(payload, "source_format"))
    _push_format(formats, _get(payload, "content_type") or _get(payload, "contentType"))
    for asset in _as_list(_get(payload, "downloads")):
        _push_asset_format(formats, asset)

    return sort_by_preferred_order(formats, FORMAT_ORDER)


def use_cases_for_formats(formats: list[Any] | None = None) -> list[str]:
    normalized = {str(format or "").upper() for format in formats or []}
    use_cases: list[str] = []
    if any(format in normalized for format in ["DXF", "DWG", "RVT", "RFA", "IFC"]):
        use_cases.append("Insert into CAD drawing")
    if any(format in normalized for format in ["PNG", "JPG", "JPEG", "SVG", "PDF"]):
        use_cases.append("Mark up / annotate drawing")
    if any(format in normalized for format in ["PNG", "JPG", "JPEG", "PDF", "SVG"]):
        use_cases.append("Use in PDF/report")
    return sort_by_preferred_order(use_cases, CATALOG_USE_CASE_ORDER)


def _raw_values(symbol: Any, *keys: str) -> list[str]:
    values: list[Any] = []
    for key in keys:
        value = _get(symbol, key)
        if isinstance(value, list):
            values.extend(value)
        elif value:
            values.append(value)
    return compact_unique(values)


def catalog_taxonomy_for_symbol(symbol: Any = None) -> dict[str, list[str]]:
    symbol = symbol or {}
    context = symbol_context_text(symbol)
    raw_disciplines = _raw_values(symbol, "discipline", "engineeringDiscipline", "engineering_discipline", "disciplines")
    raw_categories = _raw_values(symbol, "category", "categories")

    disciplines: list[str] = []
    for value in raw_disciplines:
        disciplines.extend(normalize_catalog_discipline(value))
    if re.search(r"fire|smoke|heat|detector|call\s?point|break\s?glass|sounder|beacon|alarm", context):
        disciplines.append("Fire & Life Safety")

    categories: list[str] = []
    if raw_categories:
        for value in raw_categories:
            categories.extend(normalize_catalog_category(value, symbol))
    else:
        categories.extend(normalize_catalog_category(None, symbol))

    available_formats = available_formats_for_symbol(symbol)
    return {
        "disciplines": sort_by_preferred_order(disciplines, CATALOG_DISCIPLINE_ORDER),
        "categories": sort_by_preferred_order(categories, CATALOG_CATEGORY_ORDER),
        "available_formats": available_formats,
        "use_cases": use_cases_for_formats(available_formats),
        "raw_disciplines": raw_disciplines,
        "raw_categories": raw_categories,
    }
