from __future__ import annotations

from dataclasses import dataclass
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from .asset_manifest import list_download_assets
from .catalog_taxonomy import catalog_taxonomy_for_symbol
from .published_catalog import (
    PUBLISHED_SYMBOLS_SQL,
    choose_published_preview_asset,
    published_fallback_source_asset,
    published_symbol_display_id,
)
from .settings import get_settings


@dataclass(frozen=True)
class CatalogSearchResult:
    items: list[dict]
    interpreted_filters: dict
    ranking_explanation: list[str]
    warnings: list[str]


def catalog_symbol_ref(row) -> str:
    return published_symbol_display_id(row)


def row_taxonomy_input(row) -> dict:
    payload = row.payload_json or {}
    return {
        "name": payload.get("name") or payload.get("canonical_name") or row.canonical_name,
        "displayName": published_symbol_display_id(row),
        "category": row.category,
        "discipline": row.discipline,
        "summary": payload.get("summary") or payload.get("description") or row.canonical_name,
        "keywords": payload.get("keywords") or payload.get("search_terms") or [],
        "downloads": payload.get("downloads") or [],
        "payload": payload,
    }


def catalog_symbol_summary(row) -> dict:
    payload = row.payload_json or {}
    display_id = catalog_symbol_ref(row)
    taxonomy = catalog_taxonomy_for_symbol(row_taxonomy_input(row))
    preview_asset = choose_published_preview_asset(payload)
    download_available = bool(
        list_download_assets(payload, fallback_source_asset=published_fallback_source_asset(payload))
    )
    preview = None
    links = {"api": f"/api/v1/catalog/symbols/{display_id}"}
    if preview_asset:
        preview = {
            "thumbnailUrl": f"/api/v1/catalog/symbols/{display_id}/thumbnail",
            "previewUrl": f"/api/v1/catalog/symbols/{display_id}/preview",
        }
        links["thumbnail"] = preview["thumbnailUrl"]
        links["preview"] = preview["previewUrl"]
    if download_available:
        links["download"] = "/api/v1/catalog/symbols/download"

    return {
        "displayId": display_id,
        "catalogSymbolId": display_id,
        "symbolId": str(row.symbol_id),
        "slug": row.slug,
        "name": payload.get("name") or payload.get("canonical_name") or row.canonical_name,
        "summary": payload.get("summary") or payload.get("description") or row.canonical_name,
        "catalogDisciplines": taxonomy["disciplines"],
        "catalogCategories": taxonomy["categories"],
        "useCases": taxonomy["use_cases"],
        "availableFormats": taxonomy["available_formats"],
        "downloadAvailable": download_available,
        "preview": preview,
        "links": links,
    }


# Specification section 12.1 phase M5: a catalogue facet filter should match a
# symbol whose *governed classification* names the facet, not only one whose
# legacy text column happens to contain the string. The legacy column and the
# payload-JSON match stay as the fallback section 10.1 asks for, so this can
# only ever widen the matches among symbols the surrounding query already
# admits -- it adds no table to the FROM chain and cannot reach a symbol
# `active_public_symbol_projections` excludes (section 14.2).
#
# `preferred_label ILIKE` rather than an equality on `node_code`, because the
# facet values the catalogue serves are the labels, and the surrounding
# filters are all substring matches.
_CLASSIFICATION_MATCH_SQL = """
                OR EXISTS (
                    SELECT 1
                    FROM symbol_revision_classifications src
                    JOIN classification_nodes cn ON cn.id = src.classification_node_id
                    JOIN classification_schemes cs ON cs.id = src.classification_scheme_id
                    WHERE src.symbol_revision_id = sr.id
                      AND src.assignment_role = 'primary'
                      AND src.status IN ('verified', 'proposed')
                      AND cs.scheme_code = :{parameter}_scheme
                      AND cn.preferred_label ILIKE :{parameter}
                )"""

_DISCIPLINE_SCHEME = "ENGINEERING-DISCIPLINE"
_SYMBOL_CATEGORY_SCHEME = "SYMBOL-CATEGORY-FAMILY"


def _facet_filter(
    *,
    column: str,
    parameter: str,
    scheme_code: str,
    assignments_enabled: bool,
    params: dict,
) -> str:
    """Build one facet filter, with or without the assignment match.

    With the flag off the string is byte-identical to what this module built
    before SM-P0-09, and no extra parameter is bound -- which is what makes
    the rollout stageable and the "same SQL as today" claim checkable.
    """
    clause = f"(gs.{column} ILIKE :{parameter} OR CAST(sr.payload_json AS TEXT) ILIKE :{parameter}"
    if assignments_enabled:
        clause += _CLASSIFICATION_MATCH_SQL.format(parameter=parameter)
        params[f"{parameter}_scheme"] = scheme_code
    return clause + ")"


def catalog_symbol_filters(
    *,
    q: str | None,
    discipline: str | None,
    category: str | None,
    use_case: str | None,
    format_: str | None,
    pack: str | None,
    symbol_family: str | None,
    has_preview: bool | None,
    updated_since: str | None,
    assignments_enabled: bool | None = None,
) -> tuple[list[str], dict, dict]:
    if assignments_enabled is None:
        assignments_enabled = bool(
            getattr(get_settings(), "catalog_classification_assignments_enabled", False)
        )
    filters: list[str] = []
    params: dict = {}
    response_filters: dict = {}
    if q:
        filters.append(
            """
            (
                gs.slug ILIKE :query
                OR gs.canonical_name ILIKE :query
                OR gs.category ILIKE :query
                OR gs.discipline ILIKE :query
                OR pk.pack_code ILIKE :query
                OR pk.title ILIKE :query
                OR pp.page_code ILIKE :query
                OR CAST(sr.payload_json AS TEXT) ILIKE :query
            )
            """
        )
        params["query"] = f"%{q}%"
    if discipline:
        filters.append(
            _facet_filter(
                column="discipline",
                parameter="discipline",
                scheme_code=_DISCIPLINE_SCHEME,
                assignments_enabled=assignments_enabled,
                params=params,
            )
        )
        params["discipline"] = f"%{discipline}%"
        response_filters["discipline"] = discipline
    if category:
        filters.append(
            _facet_filter(
                column="category",
                parameter="category",
                scheme_code=_SYMBOL_CATEGORY_SCHEME,
                assignments_enabled=assignments_enabled,
                params=params,
            )
        )
        params["category"] = f"%{category}%"
        response_filters["category"] = category
    if use_case:
        filters.append("CAST(sr.payload_json AS TEXT) ILIKE :use_case")
        params["use_case"] = f"%{use_case}%"
        response_filters["useCase"] = use_case
    if format_:
        filters.append("CAST(sr.payload_json AS TEXT) ILIKE :format")
        params["format"] = f"%{format_}%"
        response_filters["format"] = format_
    if pack:
        filters.append("(pk.pack_code = :pack OR pk.id::text = :pack)")
        params["pack"] = pack
        response_filters["pack"] = pack
    if symbol_family:
        filters.append(
            "(gs.slug ILIKE :symbol_family OR gs.canonical_name ILIKE :symbol_family "
            "OR CAST(sr.payload_json AS TEXT) ILIKE :symbol_family)"
        )
        params["symbol_family"] = f"%{symbol_family}%"
        response_filters["symbolFamily"] = symbol_family
    if has_preview is not None:
        preview_filter = """
        (
            sr.payload_json ? 'preview_object_key'
            OR sr.payload_json #> '{visual_assets,preview}' IS NOT NULL
            OR CAST(sr.payload_json AS TEXT) ILIKE '%preview%'
        )
        """
        filters.append(preview_filter if has_preview else f"NOT {preview_filter}")
        response_filters["hasPreview"] = has_preview
    if updated_since:
        filters.append(
            "GREATEST(gs.updated_at, sr.created_at, pp.updated_at, pk.updated_at) "
            ">= CAST(:updated_since AS timestamptz)"
        )
        params["updated_since"] = updated_since
        response_filters["updatedSince"] = updated_since
    return filters, params, response_filters


def _contextual_search_context(context: object) -> tuple[dict, list[str]]:
    context = context if isinstance(context, dict) else {}
    warnings: list[str] = []
    discipline = context.get("discipline")
    catalog_disciplines = catalog_taxonomy_for_symbol({"discipline": discipline})["disciplines"] if discipline else []
    preferred_formats = [str(value).strip().upper() for value in context.get("preferredFormats", []) if str(value).strip()]
    preferred_formats = list(dict.fromkeys(preferred_formats))
    interpreted = {
        "application": str(context.get("application") or "").strip() or None,
        "catalogDisciplines": catalog_disciplines,
        "drawingType": str(context.get("drawingType") or "").strip() or None,
        "selectedLayer": str(context.get("selectedLayer") or "").strip() or None,
        "units": str(context.get("units") or "").strip() or None,
        "preferredFormats": preferred_formats,
    }
    return {key: value for key, value in interpreted.items() if value not in (None, [], "")}, warnings


def _contextual_search_score(row, summary: dict, *, query: str, interpreted_filters: dict) -> int:
    searchable = " ".join(
        [
            str(row.slug or ""),
            str(row.canonical_name or ""),
            str(summary.get("summary") or ""),
            " ".join(str(value) for value in (row.payload_json or {}).get("keywords", [])),
        ]
    ).lower()
    score = sum(3 for token in re.findall(r"[a-z0-9]+", query.lower()) if token in searchable)
    requested_disciplines = set(interpreted_filters.get("catalogDisciplines", []))
    score += 10 * len(requested_disciplines.intersection(summary.get("catalogDisciplines", [])))
    requested_formats = set(interpreted_filters.get("preferredFormats", []))
    score += 4 * len(requested_formats.intersection(summary.get("availableFormats", [])))
    selected_layer = str(interpreted_filters.get("selectedLayer") or "").replace("_", " ").lower()
    score += sum(1 for token in re.findall(r"[a-z0-9]+", selected_layer) if token in searchable)
    return score


def search_catalog_symbols_for_context(
    session: Session,
    *,
    query: str,
    context: dict,
    limit: int,
) -> CatalogSearchResult:
    capped_limit = min(max(int(limit), 1), 100)
    interpreted_filters, warnings = _contextual_search_context(context)
    discipline = (interpreted_filters.get("catalogDisciplines") or [None])[0]
    filters, params, _ = catalog_symbol_filters(
        q=None,
        discipline=discipline,
        category=None,
        use_case=None,
        format_=None,
        pack=None,
        symbol_family=None,
        has_preview=None,
        updated_since=None,
    )
    where_extension = (" AND " + " AND ".join(filters)) if filters else ""
    params["limit"] = 100

    rows = session.execute(
        text(
            PUBLISHED_SYMBOLS_SQL
            + where_extension
            + """
            ORDER BY pk.effective_date DESC, pk.pack_code, pe.sort_order, gs.canonical_name
            LIMIT :limit
            """
        ),
        params,
    ).all()
    ranked = [
        (_contextual_search_score(row, catalog_symbol_summary(row), query=query, interpreted_filters=interpreted_filters), row)
        for row in rows
    ]
    ranked.sort(key=lambda entry: (-entry[0], str(entry[1].canonical_name).lower()))
    items = [catalog_symbol_summary(row) for _, row in ranked[:capped_limit]]

    requested_formats = interpreted_filters.get("preferredFormats", [])
    available_formats = {format_ for item in items for format_ in item["availableFormats"]}
    for format_ in requested_formats:
        if format_ not in available_formats:
            warnings.append(f"Preferred format {format_} is not available among the ranked results.")
    ranking_explanation = ["Results are ranked by query term matches."]
    if interpreted_filters.get("catalogDisciplines"):
        ranking_explanation.append("Requested discipline is applied as a Catalog filter and ranking preference.")
    if requested_formats:
        ranking_explanation.append("Preferred formats boost matching symbols and can be used with the symbol download endpoint.")
    if interpreted_filters.get("selectedLayer"):
        ranking_explanation.append("Selected layer terms provide an additional ranking signal.")

    return CatalogSearchResult(
        items=items,
        interpreted_filters=interpreted_filters,
        ranking_explanation=ranking_explanation,
        warnings=warnings,
    )
