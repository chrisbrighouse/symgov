from __future__ import annotations

from .asset_manifest import choose_preview_asset


PUBLISHED_SYMBOLS_SQL = """
    SELECT
        gs.id::text AS symbol_id,
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
    WHERE pk.status = 'published'
        AND pk.audience = 'public'
        AND sr.lifecycle_state = 'published'
"""


def published_symbol_display_id(row) -> str:
    payload = row.payload_json or {}
    package_id = payload.get("package_display_id") or getattr(row, "pack_code", None)
    sequence = payload.get("package_symbol_sequence")
    if sequence is None:
        sequence = getattr(row, "sort_order", None)

    if package_id and sequence is not None:
        try:
            sequence_value = int(sequence)
            return f"{package_id}-{sequence_value}"
        except (TypeError, ValueError):
            pass

    return (
        payload.get("display_name")
        or payload.get("workspace_display_name")
        or payload.get("symbol_display_id")
        or row.slug
    )


def published_fallback_source_asset(payload: dict | None) -> dict:
    payload = payload or {}
    return {
        "object_key": payload.get("source_object_key") or payload.get("raw_object_key") or payload.get("origin_object_key"),
        "filename": payload.get("source_file_name") or payload.get("filename"),
        "content_type": payload.get("source_content_type") or payload.get("content_type"),
        "format": payload.get("source_format") or payload.get("format"),
        "role": "source",
    }


def choose_published_preview_asset(payload: dict | None) -> dict | None:
    payload = payload or {}
    return choose_preview_asset(payload, fallback_source_asset=published_fallback_source_asset(payload))
