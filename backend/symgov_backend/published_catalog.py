from __future__ import annotations

import json

from .asset_manifest import list_preview_assets, select_preview_asset

MAX_DEXPI_CLASS_LENGTH = 128


def normalize_dexpi_class(value: object) -> str:
    """The DEXPI class a caller asked for: trimmed, empty when absent, capped."""
    return str(value or "").strip()[:MAX_DEXPI_CLASS_LENGTH]


def dexpi_class_parameter(value: str) -> str:
    """The `:dexpi_class_json` bind value `dexpi_class_predicate_sql` compares with."""
    return json.dumps([value])


def dexpi_class_predicate_sql(revision_payload_expr: str) -> str:
    """Whether a revision's payload lists the class in `dexpi.component_classes`.

    The one definition of "this symbol is in DEXPI class X", shared by the
    Catalog search filter (`dexpiClass`) and the Details view's same-class
    count, so the number a link promises is the number the Catalog returns.
    It is a containment test on the stored payload, not a facet: nothing is
    precomputed and no facet rule changes.
    """
    return (
        f"({revision_payload_expr} -> 'dexpi' -> 'component_classes')"
        " @> CAST(:dexpi_class_json AS jsonb)"
    )


# Whether the revision carries a governed discipline / category assignment: a
# `proposed` or `verified` assignment in the scheme. Most current assignments
# are `legacy_backfill` and still `proposed`, and those count; `rejected` and
# `retired` do not. A governed value is the value the Catalog facets use: the
# keyword rules in `catalog_facets` / `catalog_taxonomy` only ever run for a
# symbol that has none.
def governed_assignment_exists_sql(scheme_code: str, revision_id_expr: str = "sr.id") -> str:
    return f"""EXISTS (
            SELECT 1
            FROM symbol_revision_classifications src
            JOIN classification_schemes cs ON cs.id = src.classification_scheme_id
            WHERE src.symbol_revision_id = {revision_id_expr}
              AND src.status IN ('proposed', 'verified')
              AND cs.scheme_code = '{scheme_code}'
        )"""


# The attribution text an imported library asks every share to carry. It lives
# once, on the source package's approved rights record, so a revision's payload
# names only where to read it (`dexpi.attribution_source = "rights_record"`)
# and the response is filled from here when it is built.
RIGHTS_ATTRIBUTION_COLUMN_SQL = """(
            SELECT rr.evidence_json ->> 'attribution_text'
            FROM source_package_entries spe
            JOIN rights_records rr ON rr.source_package_id = spe.source_package_id
            WHERE spe.symbol_revision_id = sr.id
              AND rr.decision_status = 'approved'
              AND rr.evidence_json ->> 'attribution_text' IS NOT NULL
            ORDER BY rr.created_at DESC
            LIMIT 1
        ) AS rights_attribution_text"""

GOVERNED_DISCIPLINE_COLUMN_SQL = governed_assignment_exists_sql("ENGINEERING-DISCIPLINE") + " AS governed_discipline"
GOVERNED_CATEGORY_COLUMN_SQL = governed_assignment_exists_sql("SYMBOL-CATEGORY-FAMILY") + " AS governed_category"


def governed_taxonomy_for_row(row) -> dict | None:
    """`{"discipline": bool, "category": bool}`, or None when the row carries no flags."""
    discipline = getattr(row, "governed_discipline", None)
    category = getattr(row, "governed_category", None)
    if discipline is None and category is None:
        return None
    return {"discipline": bool(discipline), "category": bool(category)}


PUBLISHED_SYMBOLS_SQL = """
    SELECT
        gs.id::text AS symbol_id,
        gs.catalog_symbol_id,
        gs.slug,
        gs.canonical_name,
        gs.category,
        gs.discipline,
        sr.id::text AS symbol_revision_id,
        sr.revision_label,
        sr.created_at AS revision_created_at,
        sr.payload_json,
        sr.rationale,
        pp.id::text AS page_id,
        pp.page_code,
        pp.title AS page_title,
        pp.effective_date,
        pp.updated_at AS page_updated_at,
        pk.id::text AS pack_id,
        pk.pack_code,
        pk.title AS pack_title,
        pk.audience,
        pk.updated_at AS pack_updated_at,
        pe.sort_order,
        GREATEST(gs.updated_at, sr.created_at, pp.updated_at, pk.updated_at) AS last_updated_at
    FROM published_pages pp
    JOIN publication_packs pk ON pk.id = pp.pack_id
    JOIN pack_entries pe ON pe.pack_id = pk.id
        AND pe.published_page_id = pp.id
        AND pe.symbol_revision_id = pp.current_symbol_revision_id
    JOIN symbol_revisions sr ON sr.id = pp.current_symbol_revision_id
    JOIN governed_symbols gs ON gs.id = sr.symbol_id
    JOIN active_public_symbol_projections app
        ON app.governed_symbol_id = gs.id
       AND app.symbol_revision_id = sr.id
       AND app.published_page_id = pp.id
       AND app.pack_entry_id = pe.id
       AND app.publication_pack_id = pk.id
    WHERE pk.status = 'published'
        AND pk.audience = 'public'
        AND sr.lifecycle_state = 'published'
"""


# The served rows: the same query with the governed-assignment flags and the
# rights attribution as extra columns. The routes that build what a user sees
# read this one. `PUBLISHED_SYMBOLS_SQL` stays exactly as it was, because
# visibility tests and candidate scans use it directly and several of them run
# against databases older than the tables the extra columns read.
PUBLISHED_SYMBOLS_WITH_GOVERNANCE_SQL = PUBLISHED_SYMBOLS_SQL.replace(
    "AS last_updated_at\n    FROM published_pages pp",
    "AS last_updated_at,\n        "
    + GOVERNED_DISCIPLINE_COLUMN_SQL
    + ",\n        "
    + GOVERNED_CATEGORY_COLUMN_SQL
    + ",\n        "
    + RIGHTS_ATTRIBUTION_COLUMN_SQL
    + "\n    FROM published_pages pp",
    1,
)
assert PUBLISHED_SYMBOLS_WITH_GOVERNANCE_SQL != PUBLISHED_SYMBOLS_SQL


def payload_with_rights_attribution(payload: dict | None, attribution_text: str | None) -> dict:
    """The payload as served: `dexpi.attribution` filled from the rights record.

    Only a payload that says its attribution lives on the record
    (`attribution_source == "rights_record"`) is touched; a pilot symbol keeps
    the text it stores, byte for byte. The stored payload is never modified.
    """
    payload = payload or {}
    dexpi = payload.get("dexpi")
    if (
        not attribution_text
        or not isinstance(dexpi, dict)
        or dexpi.get("attribution_source") != "rights_record"
        or dexpi.get("attribution")
    ):
        return payload
    return {**payload, "dexpi": {**dexpi, "attribution": attribution_text}}


def published_symbol_display_id(row) -> str:
    identifier = getattr(row, "catalog_symbol_id", None)
    if not isinstance(identifier, str) or not identifier:
        raise RuntimeError("Published row is missing its canonical Catalog symbol ID.")
    return identifier


def published_fallback_source_asset(payload: dict | None) -> dict:
    payload = payload or {}
    return {
        "object_key": payload.get("source_object_key") or payload.get("raw_object_key") or payload.get("origin_object_key"),
        "filename": payload.get("source_file_name") or payload.get("filename"),
        "content_type": payload.get("source_content_type") or payload.get("content_type"),
        "format": payload.get("source_format") or payload.get("format"),
        "role": "source",
    }


def choose_published_preview_asset(payload: dict | None, requested_format: str | None = None) -> dict | None:
    payload = payload or {}
    return select_preview_asset(
        payload,
        requested_format=requested_format,
        fallback_source_asset=published_fallback_source_asset(payload),
    )


def list_published_preview_assets(payload: dict | None) -> list[dict]:
    payload = payload or {}
    return list_preview_assets(payload, fallback_source_asset=published_fallback_source_asset(payload))
