"""Database search behind the Catalog page's tabs.

`GET /published/symbols/search` pages, filters, sorts and counts in the
database. The Catalog page previously did all of that in the browser over
the full list from `GET /published/symbols`, which stays as it is.

Who can see what is decided exactly as the list route decides it: public rows
come from `PUBLISHED_SYMBOLS_SQL` unchanged, and an organization-bound session
additionally gets its own organization-wide private symbols, under the same
predicate as `catalog_organization_context.list_organization_wide_catalog_symbols`.
The facet store (`catalog_facets.py`) is joined only after that, so it decides
which filters a row matches, never whether the row is visible.

Matching follows the browser's rules, so the results do not change when the
page switches over:

- search: the lower-cased query must appear in the symbol's search text
  (`buildCatalogSearchText`), or in its pack title, pack code or page code;
- facet filters: exact values, any ticked value within one filter, every
  filter at once;
- column filters: the lower-cased value must appear in the column as shown;
- the preferred-format order is the primary sort key, as
  `sortSymbolsByPreferredFormats` makes it after the column sort.

Two deliberate differences: date columns sort chronologically (the browser
compared the formatted strings, so "01 Oct" sorted before "30 Sept"), and the
photo and comment columns sort by count.

Each filter's value counts apply every other filter but not its own, so
ticking one discipline still shows how many symbols the others hold. Every
value that occurs anywhere in the scope is listed, with a count of zero when
the current filters exclude it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import uuid

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from .catalog_facets import CATALOG_FACET_RULES_VERSION, compute_catalog_facets
from .published_catalog import PUBLISHED_SYMBOLS_SQL

DEFAULT_PAGE_SIZE = 60
MAX_PAGE_SIZE = 200

# Facet key -> (stored column or live expression, is it a JSON array?)
FACET_FIELDS: dict[str, tuple[str, bool]] = {
    "catalogDisciplines": ("f.disciplines", True),
    "catalogCategories": ("f.categories", True),
    "useCases": ("f.use_cases", True),
    "availableFormats": ("f.formats", True),
    "pack": ("c.pack_title", False),
    "symbolFamily": ("f.symbol_family", False),
}


def _joined(column: str) -> str:
    return f"array_to_string(ARRAY(SELECT jsonb_array_elements_text({column})), ', ')"


def _shown_date(column: str) -> str:
    # `Intl.DateTimeFormat('en-GB', {timeZone: 'Europe/London', day: '2-digit',
    # month: 'short', year: 'numeric'})`, which abbreviates September "Sept".
    return (
        f"COALESCE(replace(to_char({column}, 'DD Mon YYYY'), ' Sep ', ' Sept '), '')"
    )


_PHOTO_COUNT = """
    CASE WHEN c.source = 'public' THEN LEAST(2, (
        SELECT count(*) FROM hannah_photo_candidates hp
        WHERE hp.symbol_id = c.governed_symbol_id
          AND hp.status = 'attached'
          AND hp.object_key IS NOT NULL
    )) ELSE 0 END
"""

_COMMENT_COUNT = """
    CASE WHEN c.source = 'public' THEN (
        SELECT count(*) FROM clarification_records cr
        WHERE cr.symbol_id = c.governed_symbol_id
    ) ELSE 0 END
"""

# Column key -> (text as the browser shows it, sort expression)
COLUMN_FIELDS: dict[str, tuple[str, str]] = {
    "id": ("f.display_id", 'f.id_sort_key COLLATE "C"'),
    "name": ("f.display_name", 'f.name_sort_key COLLATE "C"'),
    "scope": (
        "CASE WHEN c.source = 'organization_private' THEN 'Organization Private' ELSE 'Public' END",
        "CASE WHEN c.source = 'organization_private' THEN 1 ELSE 0 END",
    ),
    "lastUpdatedAt": (
        _shown_date("(c.last_updated_at AT TIME ZONE 'Europe/London')"),
        "c.last_updated_at",
    ),
    "effectiveDate": (_shown_date("c.effective_date"), "c.effective_date"),
    "photoStatus": (
        f"CASE WHEN ({_PHOTO_COUNT}) > 0 THEN ({_PHOTO_COUNT})::text || ' added' ELSE '' END",
        f"({_PHOTO_COUNT})",
    ),
    "commentStatus": (
        f"CASE WHEN ({_COMMENT_COUNT}) = 0 THEN '' WHEN ({_COMMENT_COUNT}) = 1 THEN '1 comment'"
        f" ELSE ({_COMMENT_COUNT})::text || ' comments' END",
        f"({_COMMENT_COUNT})",
    ),
    "catalogCategories": (_joined("f.categories"), f"lower({_joined('f.categories')})"),
    "catalogDisciplines": (_joined("f.disciplines"), f"lower({_joined('f.disciplines')})"),
    "availableFormats": (_joined("f.formats"), f"lower({_joined('f.formats')})"),
    "pack": ("COALESCE(c.pack_title, '')", "lower(COALESCE(c.pack_title, ''))"),
    "revision": ("COALESCE(c.revision_label, '')", "lower(COALESCE(c.revision_label, ''))"),
}

SORT_KEYS = frozenset(COLUMN_FIELDS)


class CatalogSearchInputError(ValueError):
    """A search parameter the endpoint does not accept."""


@dataclass
class CatalogSearchRequest:
    query: str = ""
    facets: dict[str, list[str]] = field(default_factory=dict)
    columns: dict[str, str] = field(default_factory=dict)
    favourites_only: bool = False
    sort: str = "id"
    direction: str = "asc"
    preferred_formats: list[str] = field(default_factory=list)
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    def validate(self) -> None:
        unknown_facets = sorted(set(self.facets) - set(FACET_FIELDS))
        if unknown_facets:
            raise CatalogSearchInputError(f"Unknown filter: {', '.join(unknown_facets)}.")
        unknown_columns = sorted(set(self.columns) - set(COLUMN_FIELDS))
        if unknown_columns:
            raise CatalogSearchInputError(f"Unknown column filter: {', '.join(unknown_columns)}.")
        if self.sort not in SORT_KEYS:
            raise CatalogSearchInputError(f"Unknown sort column: {self.sort}.")
        if self.direction not in {"asc", "desc"}:
            raise CatalogSearchInputError("Sort direction must be asc or desc.")
        if self.page < 1:
            raise CatalogSearchInputError("Page must be 1 or more.")
        if not 1 <= self.page_size <= MAX_PAGE_SIZE:
            raise CatalogSearchInputError(f"Page size must be between 1 and {MAX_PAGE_SIZE}.")


@dataclass(frozen=True)
class CatalogSearchScope:
    """Who is searching, which decides the candidate rows."""

    user_id: uuid.UUID
    organization_id: uuid.UUID | None = None


@dataclass
class CatalogSearchPage:
    entries: list[dict]
    total: int
    facets: dict[str, list[dict]]


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _candidates_sql(scope: CatalogSearchScope) -> str:
    public = f"""
        SELECT
            'public'::text AS source,
            p.symbol_id::uuid AS governed_symbol_id,
            p.symbol_revision_id::uuid AS symbol_revision_id,
            p.page_id,
            p.pack_id,
            p.pack_title,
            p.pack_code,
            p.page_code,
            p.revision_label,
            p.effective_date,
            p.last_updated_at
        FROM ({PUBLISHED_SYMBOLS_SQL}) p
    """
    if scope.organization_id is None:
        return public
    private = """
        SELECT
            'organization_private'::text AS source,
            gs.id AS governed_symbol_id,
            sr.id AS symbol_revision_id,
            NULL::text AS page_id,
            NULL::text AS pack_id,
            NULL::text AS pack_title,
            NULL::text AS pack_code,
            NULL::text AS page_code,
            sr.revision_label,
            NULL::date AS effective_date,
            gs.updated_at AS last_updated_at
        FROM governed_symbols gs
        JOIN symbol_revisions sr ON sr.id = gs.current_revision_id
        WHERE gs.owner_organization_id = :organization_id
          AND gs.visibility = 'organization_private'
          AND gs.organization_wide IS TRUE
          AND sr.lifecycle_state = 'approved'
    """
    return f"{public} UNION ALL {private}"


_FACET_JOIN = """
    JOIN symbol_revisions sr_live ON sr_live.id = c.symbol_revision_id
    JOIN governed_symbols gs_live ON gs_live.id = c.governed_symbol_id
"""


def _stale_revision_ids(session: Session, scope: CatalogSearchScope) -> list[uuid.UUID]:
    sql = f"""
        WITH c AS ({_candidates_sql(scope)})
        SELECT DISTINCT c.symbol_revision_id
        FROM c
        {_FACET_JOIN}
        LEFT JOIN catalog_symbol_facets f
            ON f.symbol_revision_id = c.symbol_revision_id
           AND f.rules_version = :rules_version
           AND f.revision_generation = sr_live.catalog_facet_generation
           AND f.symbol_generation = gs_live.catalog_facet_generation
        WHERE f.symbol_revision_id IS NULL
    """
    params = {"rules_version": CATALOG_FACET_RULES_VERSION, "organization_id": scope.organization_id}
    return [row.symbol_revision_id for row in session.execute(text(sql), params).all()]


def _filter_clauses(request: CatalogSearchRequest, scope: CatalogSearchScope, params: dict) -> tuple[list[str], dict[str, str]]:
    """The always-applied clauses, and one clause per ticked facet."""
    clauses: list[str] = []
    query = request.query.strip().lower()
    if query:
        params["search_pattern"] = _like_pattern(query)
        clauses.append(
            "(f.search_text LIKE :search_pattern ESCAPE '\\'"
            " OR lower(COALESCE(c.pack_title, '')) LIKE :search_pattern ESCAPE '\\'"
            " OR lower(COALESCE(c.pack_code, '')) LIKE :search_pattern ESCAPE '\\'"
            " OR lower(COALESCE(c.page_code, '')) LIKE :search_pattern ESCAPE '\\')"
        )
    for position, (key, value) in enumerate(sorted(request.columns.items())):
        wanted = str(value or "").strip().lower()
        if not wanted:
            continue
        parameter = f"column_{position}"
        params[parameter] = _like_pattern(wanted)
        clauses.append(f"lower({COLUMN_FIELDS[key][0]}) LIKE :{parameter} ESCAPE '\\'")
    if request.favourites_only:
        params["user_id"] = scope.user_id
        clauses.append(
            "EXISTS (SELECT 1 FROM catalog_favourites cf"
            " WHERE cf.user_id = :user_id AND cf.symbol_id = c.governed_symbol_id)"
        )

    facet_clauses: dict[str, str] = {}
    for key, values in sorted(request.facets.items()):
        selected = [str(value).strip() for value in values if str(value or "").strip()]
        if not selected:
            continue
        column, is_array = FACET_FIELDS[key]
        parameter = f"facet_{key}"
        params[parameter] = selected
        if is_array:
            facet_clauses[key] = f"{column} ?| CAST(:{parameter} AS text[])"
        else:
            facet_clauses[key] = f"{column} = ANY(CAST(:{parameter} AS text[]))"
    return clauses, facet_clauses


def _conjunction(clauses: list[str]) -> str:
    return " AND ".join(f"({clause})" for clause in clauses) if clauses else "TRUE"


def _order_by(request: CatalogSearchRequest, params: dict) -> str:
    parts: list[str] = []
    preferred = [str(value).strip().upper() for value in request.preferred_formats if str(value or "").strip()]
    if preferred:
        ranks = " ".join(
            f"WHEN f.formats ? :preferred_{index} THEN {index}" for index in range(len(preferred))
        )
        for index, value in enumerate(preferred):
            params[f"preferred_{index}"] = value
        parts.append(f"CASE {ranks} ELSE {len(preferred)} END")
    direction = "DESC" if request.direction == "desc" else "ASC"
    nulls = "NULLS LAST" if direction == "DESC" else "NULLS FIRST"
    parts.append(f"{COLUMN_FIELDS[request.sort][1]} {direction} {nulls}")
    parts.extend(['f.id_sort_key COLLATE "C"', "c.symbol_revision_id", "c.pack_id NULLS FIRST", "c.page_id NULLS FIRST"])
    return ", ".join(parts)


def search_catalog(session: Session, scope: CatalogSearchScope, request: CatalogSearchRequest) -> CatalogSearchPage:
    request.validate()

    stale = _stale_revision_ids(session, scope)
    if stale:
        compute_catalog_facets(session, stale)
        session.commit()

    params: dict = {"rules_version": CATALOG_FACET_RULES_VERSION, "organization_id": scope.organization_id}
    clauses, facet_clauses = _filter_clauses(request, scope, params)
    base = f"""
        WITH c AS ({_candidates_sql(scope)}),
        base AS (
            SELECT c.pack_title, f.symbol_family, f.disciplines, f.categories, f.formats, f.use_cases,
                {_conjunction(clauses)} AS m_always,
                {", ".join(f"{facet_clauses.get(key, 'TRUE')} AS m_{key}" for key in FACET_FIELDS)}
            FROM c
            {_FACET_JOIN}
            JOIN catalog_symbol_facets f
                ON f.symbol_revision_id = c.symbol_revision_id
               AND f.rules_version = :rules_version
               AND f.revision_generation = sr_live.catalog_facet_generation
               AND f.symbol_generation = gs_live.catalog_facet_generation
        )
    """
    all_match = " AND ".join(["m_always", *(f"m_{key}" for key in FACET_FIELDS)])

    count_parts = [f"SELECT '' AS facet, '' AS value, count(*) FILTER (WHERE {all_match}) AS n FROM base"]
    for key, (column, is_array) in FACET_FIELDS.items():
        others = " AND ".join(["m_always", *(f"m_{other}" for other in FACET_FIELDS if other != key)])
        # The base CTE exposes each facet's column unqualified.
        bare = column.split(".", 1)[1]
        if is_array:
            count_parts.append(
                f"SELECT '{key}', value, count(*) FILTER (WHERE {others})"
                f" FROM base, jsonb_array_elements_text(base.{bare}) AS value GROUP BY value"
            )
        else:
            count_parts.append(
                f"SELECT '{key}', base.{bare}, count(*) FILTER (WHERE {others})"
                f" FROM base WHERE COALESCE(base.{bare}, '') <> '' GROUP BY base.{bare}"
            )
    total = 0
    facets: dict[str, list[dict]] = {key: [] for key in FACET_FIELDS}
    for row in session.execute(text(base + " UNION ALL ".join(count_parts)), params).all():
        facet, value, count = row
        if facet == "":
            total = int(count)
        else:
            facets[facet].append({"value": value, "count": int(count)})
    for values in facets.values():
        values.sort(key=lambda item: (item["value"].casefold(), item["value"]))

    # The page query orders on expressions over `c` and `f`, so it reads the
    # joined rows directly rather than the flattened base CTE.
    page_sql = f"""
        WITH c AS ({_candidates_sql(scope)})
        SELECT c.source, c.governed_symbol_id, c.symbol_revision_id, c.page_id, c.pack_id
        FROM c
        {_FACET_JOIN}
        JOIN catalog_symbol_facets f
            ON f.symbol_revision_id = c.symbol_revision_id
           AND f.rules_version = :rules_version
           AND f.revision_generation = sr_live.catalog_facet_generation
           AND f.symbol_generation = gs_live.catalog_facet_generation
        WHERE {_conjunction(clauses + list(facet_clauses.values()))}
        ORDER BY {_order_by(request, params)}
        LIMIT :limit OFFSET :offset
    """
    params["limit"] = request.page_size
    params["offset"] = (request.page - 1) * request.page_size
    entries = [
        {
            "source": row.source,
            "governed_symbol_id": row.governed_symbol_id,
            "symbol_revision_id": row.symbol_revision_id,
            "page_id": row.page_id,
            "pack_id": row.pack_id,
        }
        for row in session.execute(text(page_sql), params).all()
    ]
    return CatalogSearchPage(entries=entries, total=total, facets=facets)


def load_public_page_rows(session: Session, entries: list[dict]) -> dict[tuple[str, str, str], object]:
    """The `PUBLISHED_SYMBOLS_SQL` rows for the public entries on one page."""
    public = [entry for entry in entries if entry["source"] == "public"]
    if not public:
        return {}
    query = text(
        PUBLISHED_SYMBOLS_SQL + " AND sr.id IN :revision_ids AND pp.id::text IN :page_ids"
    ).bindparams(
        bindparam("revision_ids", expanding=True, type_=UUID(as_uuid=True)),
        bindparam("page_ids", expanding=True),
    )
    rows = session.execute(
        query,
        {
            "revision_ids": sorted({entry["symbol_revision_id"] for entry in public}, key=str),
            "page_ids": sorted({entry["page_id"] for entry in public}),
        },
    ).all()
    return {(row.symbol_revision_id, row.page_id, row.pack_id): row for row in rows}
