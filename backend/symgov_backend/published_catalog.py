from __future__ import annotations

from .asset_manifest import list_preview_assets, select_preview_asset


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


GOVERNED_DISCIPLINE_COLUMN_SQL = governed_assignment_exists_sql("ENGINEERING-DISCIPLINE") + " AS governed_discipline"
GOVERNED_CATEGORY_COLUMN_SQL = governed_assignment_exists_sql("SYMBOL-CATEGORY-FAMILY") + " AS governed_category"


def governed_taxonomy_for_row(row) -> dict | None:
    """`{"discipline": bool, "category": bool}`, or None when the row carries no flags."""
    discipline = getattr(row, "governed_discipline", None)
    category = getattr(row, "governed_category", None)
    if discipline is None and category is None:
        return None
    return {"discipline": bool(discipline), "category": bool(category)}


PUBLISHED_SYMBOLS_SQL = f"""
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
        GREATEST(gs.updated_at, sr.created_at, pp.updated_at, pk.updated_at) AS last_updated_at,
        {GOVERNED_DISCIPLINE_COLUMN_SQL},
        {GOVERNED_CATEGORY_COLUMN_SQL}
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
