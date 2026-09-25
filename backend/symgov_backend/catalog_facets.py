"""The Catalog facet store: the browser's facet rules, stored per revision.

The Catalog page used to download every published symbol and derive each
one's disciplines, categories, formats and use cases in the browser
(`catalogTaxonomyForSymbol` in `frontend/src/catalogWorkbench.js`), then
search, filter and sort in memory. Searching in the database needs those
values stored, and they cannot be computed in SQL: the rules are keyword maps
and regular expressions over several payload fields.

This module is a line-for-line port of the browser's rules, applied to the
same row the Catalog list serves (`published_symbol_row` /
`organization_private_symbol_row`), so the values a user filters on are the
values the browser already shows on each card. It is deliberately *not*
`catalog_taxonomy.py`: that module drives the `/catalog` API and
classification mapping, and its rules differ from the browser's in several
places (it ignores preview assets, maps content types, and recurses into
nested values). Changing it would change those callers. The golden fixture
`tests/fixtures/catalog_browser_facets_golden.json` is generated from the
real browser functions and checked by both the frontend and backend suites,
so the two copies cannot drift unnoticed.

Rows are keyed by symbol revision and are only used while the generation
counters they were computed from still match the live rows (see migration
`20260925_0064`). The search fills in any missing or outdated rows before it
runs (`catalog_browse_search.search_catalog`); `backfill_catalog_facets`
fills them ahead of time.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import math
import re
from types import SimpleNamespace
from typing import Any, Iterable
import uuid

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Session

# Bump when any rule below changes. Rows computed under another version are
# treated as missing and recomputed on the next search (or by the backfill).
CATALOG_FACET_RULES_VERSION = 1

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
    "annotation": ["Annotations / Tags"],
    "tag": ["Annotations / Tags"],
    "tags": ["Annotations / Tags"],
    "": [],
}

# JavaScript's `\b` and `\s` differ from Python's Unicode-aware defaults only
# outside ASCII; ASCII mode matches the browser for `\b`. `\s` stays Unicode
# in both.
_FIRE_CONTEXT = re.compile(r"fire|smoke|heat|detector|call\s?point|break\s?glass|sounder|beacon|alarm")
_SENSOR_CONTEXT = re.compile(r"detector|sensor|smoke|heat|co\b|carbon", re.ASCII)
_EXTENSION = re.compile(r"\.([a-z0-9]+)(?:$|[?#])", re.IGNORECASE)
_KEY_SEPARATORS = re.compile(r"[\s-]+")
_SHORT_DISPLAY_ID = re.compile(r"^[0-9A-F]{4}-\d+$", re.IGNORECASE)
_DIGIT_RUN = re.compile(r"\d+")


# -- JavaScript value semantics ------------------------------------------------


def _truthy(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0 and not (isinstance(value, float) and math.isnan(value))
    if isinstance(value, str):
        return value != ""
    return True


def _js_or(*values: Any) -> Any:
    """`a || b || ...`: the first truthy value, else the last one."""
    for value in values[:-1]:
        if _truthy(value):
            return value
    return values[-1] if values else None


def _js_string(value: Any) -> str:
    """`String(value)` for the value shapes a JSON payload can hold."""
    if value is None:
        return "undefined"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if value.is_integer() and abs(value) < 1e21:
            return str(int(value))
        return repr(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return ",".join("" if item is None else _js_string(item) for item in value)
    if isinstance(value, dict):
        return "[object Object]"
    return str(value)


def _js_text(value: Any) -> str:
    """`String(value || '')`."""
    return _js_string(value) if _truthy(value) else ""


def _get(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, dict) else None


def _list(value: Any) -> list:
    """`(value || [])` where the browser then iterates it."""
    return value if isinstance(value, list) else []


def compact_unique(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = _js_text(value).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def _locale_key(value: str) -> tuple[str, str]:
    # An approximation of `localeCompare`: case-insensitive first, then case.
    return (value.casefold(), value.swapcase())


def sort_by_preferred_order(values: Iterable[Any], preferred_order: list[str]) -> list[str]:
    order = {value.lower(): index for index, value in enumerate(preferred_order)}
    return sorted(
        compact_unique(values),
        key=lambda value: (order.get(value.lower(), math.inf), _locale_key(value)),
    )


# -- catalogWorkbench.js ------------------------------------------------------


def _text_tokens(*values: Any) -> list[str]:
    tokens: list[Any] = []
    for value in values:
        if isinstance(value, list):
            tokens.extend(value)
        elif isinstance(value, dict):
            tokens.extend(value.values())
        else:
            tokens.append(value)
    return [_js_text(token).lower() for token in tokens]


def symbol_context_text(symbol: dict) -> str:
    payload = _get(symbol, "payload") or {}
    return " ".join(
        _text_tokens(
            symbol.get("name"),
            symbol.get("displayName"),
            symbol.get("category"),
            symbol.get("discipline"),
            symbol.get("summary"),
            symbol.get("description"),
            symbol.get("keywords"),
            symbol.get("downloads"),
            symbol.get("downloadAssets"),
            _get(payload, "name"),
            _get(payload, "description"),
            _get(payload, "summary"),
            _get(payload, "keywords"),
            _get(payload, "source_file"),
            _get(payload, "source_file_name"),
        )
    )


def _normalized_key(raw: str) -> str:
    return _KEY_SEPARATORS.sub("_", raw.lower())


def normalize_catalog_discipline(value: Any) -> list[str]:
    raw = _js_text(value).strip()
    mapped = _DISCIPLINE_MAP.get(_normalized_key(raw))
    if mapped is not None:
        return list(mapped)
    return [raw] if raw else []


def normalize_catalog_category(value: Any, symbol: dict) -> list[str]:
    raw = _js_text(value).strip()
    context = symbol_context_text(symbol)
    categories: list[str] = []
    if _FIRE_CONTEXT.search(context):
        categories.append("Fire Alarm Devices")
    if _SENSOR_CONTEXT.search(context):
        categories.append("Sensors / Detectors")
    mapped = _CATEGORY_MAP.get(_normalized_key(raw))
    categories.extend(mapped if mapped is not None else ([raw] if raw else []))
    return sort_by_preferred_order(categories or ["Miscellaneous / Unclassified"], CATALOG_CATEGORY_ORDER)


def _asset_format_source(asset: Any) -> Any:
    return _js_or(
        _get(asset, "format"),
        _get(asset, "filename"),
        _get(asset, "content_type"),
        _get(asset, "contentType"),
        _get(asset, "object_key"),
    )


def available_formats_for_symbol(symbol: dict) -> list[str]:
    formats: list[str] = []

    def push_format(value: Any) -> None:
        cleaned = _js_text(value).strip()
        if not cleaned:
            return
        match = _EXTENSION.search(cleaned)
        candidate = match.group(1) if match else cleaned
        candidate = re.sub(r"^image/", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"^application/", "", candidate, flags=re.IGNORECASE)
        mapped = "JPG" if candidate.lower() == "jpeg" else candidate.upper()
        if mapped and len(mapped) <= 8:
            formats.append(mapped)

    push_format(symbol.get("format"))
    push_format(symbol.get("contentType"))
    for value in _list(symbol.get("availableFormats")):
        push_format(value)
    for value in _list(symbol.get("downloads")):
        push_format(value)
    for asset in _list(symbol.get("downloadAssets")):
        push_format(_asset_format_source(asset))
    previews = list(_list(symbol.get("previewAssets"))) + [symbol.get("previewAsset")]
    for asset in previews:
        if _truthy(asset):
            push_format(_asset_format_source(asset))
    payload = _get(symbol, "payload") or {}
    push_format(_get(payload, "format"))
    push_format(_get(payload, "source_format"))
    for asset in _list(_get(payload, "downloads")):
        push_format(asset if isinstance(asset, str) else _asset_format_source(asset))

    return sort_by_preferred_order(formats, FORMAT_ORDER)


def use_cases_for_formats(formats: Iterable[Any]) -> list[str]:
    normalized = {_js_text(value).upper() for value in formats}
    use_cases: list[str] = []
    if normalized & {"DXF", "DWG", "RVT", "RFA", "IFC"}:
        use_cases.append("Insert into CAD drawing")
    if normalized & {"PNG", "JPG", "JPEG", "SVG", "PDF"}:
        use_cases.append("Mark up / annotate drawing")
    if normalized & {"PNG", "JPG", "JPEG", "PDF", "SVG"}:
        use_cases.append("Use in PDF/report")
    return sort_by_preferred_order(use_cases, CATALOG_USE_CASE_ORDER)


def catalog_taxonomy_for_symbol(symbol: dict) -> dict[str, list[str]]:
    context = symbol_context_text(symbol)
    disciplines = [
        *normalize_catalog_discipline(symbol.get("discipline")),
        *normalize_catalog_discipline(symbol.get("engineeringDiscipline")),
    ]
    for value in _list(symbol.get("disciplines")):
        disciplines.extend(normalize_catalog_discipline(value))
    if _FIRE_CONTEXT.search(context):
        disciplines.append("Fire & Life Safety")
    categories = list(normalize_catalog_category(symbol.get("category"), symbol))
    for value in _list(symbol.get("categories")):
        categories.extend(normalize_catalog_category(value, symbol))
    formats = available_formats_for_symbol(symbol)
    return {
        "disciplines": sort_by_preferred_order(disciplines, CATALOG_DISCIPLINE_ORDER),
        "categories": sort_by_preferred_order(categories, CATALOG_CATEGORY_ORDER),
        "availableFormats": formats,
        "useCases": use_cases_for_formats(formats),
    }


def _workbench_display_id(symbol: dict) -> Any:
    package_id = _js_or(symbol.get("packageDisplayId"), symbol.get("package_display_id"))
    sequence = symbol.get("packageSymbolSequence")
    if sequence is None:
        sequence = symbol.get("package_symbol_sequence")
    package_display = f"{_js_string(package_id)}-{_js_string(sequence)}" if _truthy(package_id) and sequence is not None else ""
    return _js_or(
        symbol.get("displayName"),
        symbol.get("display_name"),
        symbol.get("symbolDisplayId"),
        symbol.get("symbol_display_id"),
        package_display,
        symbol.get("id"),
        symbol.get("symbolId"),
        "",
    )


def _workbench_display_name(symbol: dict) -> Any:
    payload = _get(symbol, "payload")
    return _js_or(
        symbol.get("name"),
        _get(payload, "name"),
        _get(payload, "canonical_name"),
        symbol.get("canonicalName"),
        symbol.get("slug"),
        _workbench_display_id(symbol),
    )


def build_catalog_search_text(symbol: dict) -> str:
    taxonomy = catalog_taxonomy_for_symbol(symbol)
    keywords = symbol.get("keywords")
    keyword_values = keywords if isinstance(keywords, list) else (list(keywords) if isinstance(keywords, str) else [])
    return " ".join(
        compact_unique(
            [
                _workbench_display_id(symbol),
                _workbench_display_name(symbol),
                symbol.get("id"),
                symbol.get("symbolId"),
                symbol.get("slug"),
                symbol.get("category"),
                symbol.get("discipline"),
                symbol.get("pack"),
                symbol.get("packCode"),
                symbol.get("pageCode"),
                symbol.get("summary"),
                symbol.get("description"),
                *keyword_values,
                *taxonomy["disciplines"],
                *taxonomy["categories"],
                *taxonomy["availableFormats"],
                *taxonomy["useCases"],
            ]
        )
    )


# -- App.jsx (the ID and name columns) ----------------------------------------


def app_display_symbol_id(record: dict) -> str:
    package_id = _js_or(record.get("packageDisplayId"), record.get("package_display_id"))
    sequence = record.get("packageSymbolSequence")
    if sequence is None:
        sequence = record.get("package_symbol_sequence")
    package_display = f"{_js_string(package_id)}-{_js_string(sequence)}" if _truthy(package_id) and sequence is not None else ""
    candidates = [
        record.get(key)
        for key in (
            "publishedDisplayId",
            "published_display_id",
            "symbolDisplayId",
            "symbol_display_id",
            "displayName",
            "display_name",
            "workspaceDisplayName",
            "workspace_display_name",
        )
    ] + [package_display] + [record.get(key) for key in ("symbolId", "proposedSymbolId", "symbol_slug", "slug")]
    for candidate in candidates:
        if _SHORT_DISPLAY_ID.match(_js_text(candidate).strip()):
            return _js_string(candidate)
    return _js_text(
        _js_or(
            record.get("symbolDisplayId"),
            record.get("symbol_display_id"),
            record.get("publishedDisplayId"),
            record.get("published_display_id"),
            record.get("displayName"),
            record.get("display_name"),
            record.get("workspaceDisplayName"),
            record.get("workspace_display_name"),
            package_display,
            package_id,
            record.get("symbolId"),
            record.get("proposedSymbolId"),
            record.get("id"),
            "",
        )
    )


def app_display_symbol_name(record: dict) -> str:
    payload = _get(record, "payload")
    return _js_text(
        _js_or(
            record.get("name"),
            _get(payload, "name"),
            _get(payload, "canonical_name"),
            _get(_get(record, "symbolProperties"), "name"),
            record.get("proposedSymbolName"),
            record.get("title"),
            "",
        )
    )


def natural_sort_key(value: str) -> str:
    """A text key whose byte order approximates `localeCompare(..., {numeric: true, sensitivity: 'base'})`.

    Digit runs are zero-padded so `SYM-2` sorts before `SYM-10` under plain
    `ORDER BY ... COLLATE "C"`.
    """
    return _DIGIT_RUN.sub(lambda match: match.group(0).lstrip("0").rjust(20, "0"), value.casefold())


# -- facet rows -----------------------------------------------------------------


def facet_values_for_row(row: dict) -> dict:
    """The stored values for one served Catalog row.

    `pack`, `packCode` and `pageCode` are left out of the search text: they
    come from the live publication join, not from the revision, and the
    search matches them from those columns directly.
    """
    taxonomy = catalog_taxonomy_for_symbol(row)
    revision_only = {**row, "pack": None, "packCode": None, "pageCode": None}
    display_id = app_display_symbol_id(row)
    display_name = app_display_symbol_name(row)
    return {
        "display_id": display_id,
        "display_name": display_name,
        "id_sort_key": natural_sort_key(display_id),
        "name_sort_key": natural_sort_key(display_name),
        "search_text": build_catalog_search_text(revision_only).lower(),
        # `getSymbolField(symbol, 'symbolFamily')`: no served row carries
        # `symbolFamily` or `family`, so the browser falls through to category.
        "symbol_family": _js_text(_js_or(row.get("symbolFamily"), row.get("family"), row.get("category"), "")).strip(),
        "disciplines": taxonomy["disciplines"],
        "categories": taxonomy["categories"],
        "formats": taxonomy["availableFormats"],
        "use_cases": taxonomy["useCases"],
    }


_STALE_CANDIDATES_SQL = """
    SELECT sr.id AS symbol_revision_id
    FROM symbol_revisions sr
    JOIN governed_symbols gs ON gs.id = sr.symbol_id
    LEFT JOIN catalog_symbol_facets f
        ON f.symbol_revision_id = sr.id
       AND f.rules_version = :rules_version
       AND f.revision_generation = sr.catalog_facet_generation
       AND f.symbol_generation = gs.catalog_facet_generation
    WHERE sr.id IN :revision_ids
      AND f.symbol_revision_id IS NULL
"""

_SOURCE_ROWS_SQL = """
    SELECT
        gs.id AS governed_symbol_id,
        gs.id::text AS symbol_id,
        gs.catalog_symbol_id,
        gs.slug,
        gs.canonical_name,
        gs.category,
        gs.discipline,
        gs.visibility,
        gs.updated_at AS symbol_updated_at,
        gs.catalog_facet_generation AS symbol_generation,
        sr.id AS symbol_revision_id,
        sr.revision_label,
        sr.lifecycle_state,
        sr.created_at AS revision_created_at,
        sr.payload_json,
        sr.rationale,
        sr.catalog_facet_generation AS revision_generation
    FROM symbol_revisions sr
    JOIN governed_symbols gs ON gs.id = sr.symbol_id
    WHERE sr.id IN :revision_ids
"""

_UPSERT_SQL = """
    INSERT INTO catalog_symbol_facets (
        symbol_revision_id, governed_symbol_id, rules_version,
        revision_generation, symbol_generation,
        display_id, display_name, id_sort_key, name_sort_key, search_text, symbol_family,
        disciplines, categories, formats, use_cases, computed_at
    ) VALUES (
        :symbol_revision_id, :governed_symbol_id, :rules_version,
        :revision_generation, :symbol_generation,
        :display_id, :display_name, :id_sort_key, :name_sort_key, :search_text, :symbol_family,
        :disciplines, :categories, :formats, :use_cases, :computed_at
    )
    ON CONFLICT (symbol_revision_id) DO UPDATE SET
        governed_symbol_id = EXCLUDED.governed_symbol_id,
        rules_version = EXCLUDED.rules_version,
        revision_generation = EXCLUDED.revision_generation,
        symbol_generation = EXCLUDED.symbol_generation,
        display_id = EXCLUDED.display_id,
        display_name = EXCLUDED.display_name,
        id_sort_key = EXCLUDED.id_sort_key,
        name_sort_key = EXCLUDED.name_sort_key,
        search_text = EXCLUDED.search_text,
        symbol_family = EXCLUDED.symbol_family,
        disciplines = EXCLUDED.disciplines,
        categories = EXCLUDED.categories,
        formats = EXCLUDED.formats,
        use_cases = EXCLUDED.use_cases,
        computed_at = EXCLUDED.computed_at
    -- Never replace a row with one computed from older data.
    WHERE catalog_symbol_facets.rules_version <> EXCLUDED.rules_version
       OR (catalog_symbol_facets.revision_generation <= EXCLUDED.revision_generation
           AND catalog_symbol_facets.symbol_generation <= EXCLUDED.symbol_generation)
"""

_BATCH_SIZE = 500


def _served_row(source) -> dict:
    """Build the row the Catalog list would serve for this revision.

    Uses the real row builders so the facet values follow the served shape.
    The page and pack fields are placeholders: nothing stored here reads them.
    """
    from .routes.published import organization_private_symbol_row, published_symbol_row

    if source.visibility == "public":
        placeholder = SimpleNamespace(
            symbol_id=source.symbol_id,
            catalog_symbol_id=source.catalog_symbol_id,
            slug=source.slug,
            canonical_name=source.canonical_name,
            category=source.category,
            discipline=source.discipline,
            symbol_revision_id=str(source.symbol_revision_id),
            revision_label=source.revision_label,
            revision_created_at=source.revision_created_at,
            payload_json=source.payload_json,
            rationale=source.rationale,
            page_id=None,
            page_code=None,
            page_title=None,
            effective_date=date(1970, 1, 1),
            pack_id=None,
            pack_code=None,
            pack_title=None,
            sort_order=None,
            last_updated_at=None,
        )
        return published_symbol_row(placeholder)
    governed = SimpleNamespace(
        id=source.governed_symbol_id,
        slug=source.slug,
        canonical_name=source.canonical_name,
        category=source.category,
        discipline=source.discipline,
        updated_at=source.symbol_updated_at,
    )
    revision = SimpleNamespace(
        id=source.symbol_revision_id,
        payload_json=source.payload_json,
        lifecycle_state=source.lifecycle_state,
        revision_label=source.revision_label,
        created_at=source.revision_created_at,
        rationale=source.rationale,
    )
    return organization_private_symbol_row(governed, revision)


def _as_uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def compute_catalog_facets(session: Session, revision_ids: Iterable[Any]) -> int:
    """Compute and store facet rows for these revisions, whatever their state."""
    ids = [_as_uuid(value) for value in revision_ids]
    written = 0
    upsert = text(_UPSERT_SQL).bindparams(
        bindparam("symbol_revision_id", type_=UUID(as_uuid=True)),
        bindparam("governed_symbol_id", type_=UUID(as_uuid=True)),
        bindparam("disciplines", type_=JSONB),
        bindparam("categories", type_=JSONB),
        bindparam("formats", type_=JSONB),
        bindparam("use_cases", type_=JSONB),
    )
    source_query = text(_SOURCE_ROWS_SQL).bindparams(
        bindparam("revision_ids", expanding=True, type_=UUID(as_uuid=True))
    )
    now = datetime.now(timezone.utc)
    for start in range(0, len(ids), _BATCH_SIZE):
        batch = ids[start:start + _BATCH_SIZE]
        rows = []
        for source in session.execute(source_query, {"revision_ids": batch}).all():
            values = facet_values_for_row(_served_row(source))
            rows.append(
                {
                    **values,
                    "symbol_revision_id": source.symbol_revision_id,
                    "governed_symbol_id": source.governed_symbol_id,
                    "rules_version": CATALOG_FACET_RULES_VERSION,
                    "revision_generation": source.revision_generation,
                    "symbol_generation": source.symbol_generation,
                    "computed_at": now,
                }
            )
        if rows:
            session.execute(upsert, rows)
            written += len(rows)
    return written


def _catalog_revision_ids_sql() -> str:
    from .published_catalog import PUBLISHED_SYMBOLS_SQL

    # Every revision any session's Catalog could list: the public rows, and
    # every organization's organization-wide private symbols.
    return f"""
        SELECT p.symbol_revision_id::uuid AS symbol_revision_id FROM ({PUBLISHED_SYMBOLS_SQL}) p
        UNION
        SELECT sr.id
        FROM governed_symbols gs
        JOIN symbol_revisions sr ON sr.id = gs.current_revision_id
        WHERE gs.visibility = 'organization_private'
          AND gs.organization_wide IS TRUE
          AND sr.lifecycle_state = 'approved'
    """


def backfill_catalog_facets(session: Session, *, apply: bool) -> dict:
    """Fill the facet store ahead of the first search after a deploy.

    Without it the first search computes every row itself, which is correct
    but slow for a large Catalog. A dry run reports what would be written.
    """
    candidates = [row.symbol_revision_id for row in session.execute(text(_catalog_revision_ids_sql())).all()]
    stale_query = text(_STALE_CANDIDATES_SQL).bindparams(
        bindparam("revision_ids", expanding=True, type_=UUID(as_uuid=True))
    )
    stale: list[uuid.UUID] = []
    for start in range(0, len(candidates), _BATCH_SIZE):
        stale.extend(
            row.symbol_revision_id
            for row in session.execute(
                stale_query,
                {"revision_ids": candidates[start:start + _BATCH_SIZE], "rules_version": CATALOG_FACET_RULES_VERSION},
            ).all()
        )
    written = 0
    if apply and stale:
        written = compute_catalog_facets(session, stale)
        session.commit()
    return {
        "apply": apply,
        "rulesVersion": CATALOG_FACET_RULES_VERSION,
        "catalogRevisions": len(candidates),
        "missingOrOutdated": len(stale),
        "written": written,
    }
