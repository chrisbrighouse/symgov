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

The Set tab (`scope=set`) runs the same search over one Project's effective
palette, as resolved by `effective_palette.resolve_effective_palette` -- the
same membership the palette route serves, including a set's approved
organization-private items that the Catalog tab never lists. It adds two
filters, the set group and the palette source (set item or
organization-wide), a `setOrder` sort, and also searches the set's own
display label for each symbol.

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
    "catalogDisciplines": ("c.disciplines", True),
    "catalogCategories": ("c.categories", True),
    "useCases": ("c.use_cases", True),
    "availableFormats": ("c.formats", True),
    "pack": ("c.pack_title", False),
    "symbolFamily": ("c.symbol_family", False),
}

# Only the Set tab has these: a Catalog row belongs to no set.
SET_FACET_FIELDS: dict[str, tuple[str, bool]] = {
    "setGroup": ("c.group_name", False),
    "paletteSource": ("c.palette_source", False),
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
    "id": ("c.display_id", 'c.id_sort_key COLLATE "C"'),
    "name": ("c.display_name", 'c.name_sort_key COLLATE "C"'),
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
    "catalogCategories": (_joined("c.categories"), f"lower({_joined('c.categories')})"),
    "catalogDisciplines": (_joined("c.disciplines"), f"lower({_joined('c.disciplines')})"),
    "availableFormats": (_joined("c.formats"), f"lower({_joined('c.formats')})"),
    "pack": ("COALESCE(c.pack_title, '')", "lower(COALESCE(c.pack_title, ''))"),
    "revision": ("COALESCE(c.revision_label, '')", "lower(COALESCE(c.revision_label, ''))"),
}

SORT_KEYS = frozenset(COLUMN_FIELDS)
# The set's own order, grouped as the set groups it. Set tab only.
SET_ORDER_SORT = "setOrder"
SET_SORT_KEYS = SORT_KEYS | {SET_ORDER_SORT}


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

    def validate(self, scope: "CatalogSearchScope") -> None:
        unknown_facets = sorted(set(self.facets) - set(scope.facet_fields))
        if unknown_facets:
            raise CatalogSearchInputError(f"Unknown filter: {', '.join(unknown_facets)}.")
        unknown_columns = sorted(set(self.columns) - set(COLUMN_FIELDS))
        if unknown_columns:
            raise CatalogSearchInputError(f"Unknown column filter: {', '.join(unknown_columns)}.")
        if self.sort not in (SET_SORT_KEYS if scope.is_set else SORT_KEYS):
            raise CatalogSearchInputError(f"Unknown sort column: {self.sort}.")
        if self.direction not in {"asc", "desc"}:
            raise CatalogSearchInputError("Sort direction must be asc or desc.")
        if self.page < 1:
            raise CatalogSearchInputError("Page must be 1 or more.")
        if not 1 <= self.page_size <= MAX_PAGE_SIZE:
            raise CatalogSearchInputError(f"Page size must be between 1 and {MAX_PAGE_SIZE}.")


@dataclass(frozen=True)
class CatalogSearchScope:
    """Who is searching and over what, which decides the candidate rows.

    `set_members` is None for the Catalog tab. For the Set tab it is the
    effective palette, already resolved and authorized by
    `effective_palette.resolve_effective_palette`: this module only reads
    those symbols' Catalog rows, it never decides membership itself.
    """

    user_id: uuid.UUID
    organization_id: uuid.UUID | None = None
    set_members: tuple[dict, ...] | None = None

    @property
    def is_set(self) -> bool:
        return self.set_members is not None

    @property
    def facet_fields(self) -> dict[str, tuple[str, bool]]:
        return {**FACET_FIELDS, **SET_FACET_FIELDS} if self.is_set else FACET_FIELDS

    def parameters(self) -> dict:
        params: dict = {"organization_id": self.organization_id}
        if self.is_set:
            members = self.set_members or ()
            params.update(
                member_symbol_ids=[member["governedSymbolId"] for member in members],
                member_revision_ids=[member["currentRevisionId"] for member in members],
                member_sources=[member["source"] for member in members],
                member_orders=[member["sortOrder"] for member in members],
                member_groups=[member.get("groupName") for member in members],
                member_labels=[member.get("displayLabel") for member in members],
                member_formats=[member.get("preferredFormat") for member in members],
                member_notes=[member.get("notes") for member in members],
            )
        return params


@dataclass
class CatalogSearchPage:
    entries: list[dict]
    total: int
    facets: dict[str, list[dict]]


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


_NO_PALETTE_COLUMNS = """
            NULL::text AS palette_source,
            NULL::integer AS set_order,
            NULL::text AS group_name,
            NULL::text AS display_label,
            NULL::text AS preferred_format,
            NULL::text AS notes
"""

_MEMBERS = """
    (SELECT * FROM unnest(
        CAST(:member_symbol_ids AS uuid[]),
        CAST(:member_revision_ids AS uuid[]),
        CAST(:member_sources AS text[]),
        CAST(:member_orders AS integer[]),
        CAST(:member_groups AS text[]),
        CAST(:member_labels AS text[]),
        CAST(:member_formats AS text[]),
        CAST(:member_notes AS text[])
    ) AS members(governed_symbol_id, symbol_revision_id, palette_source, set_order,
                 group_name, display_label, preferred_format, notes)) m
"""

_MEMBER_COLUMNS = """
            m.palette_source,
            m.set_order,
            m.group_name,
            m.display_label,
            m.preferred_format,
            m.notes
"""


def _candidates_sql(scope: CatalogSearchScope) -> str:
    if scope.is_set:
        return _set_candidates_sql()
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
            p.last_updated_at,
            {_NO_PALETTE_COLUMNS}
        FROM ({PUBLISHED_SYMBOLS_SQL}) p
    """
    if scope.organization_id is None:
        return public
    private = f"""
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
            gs.updated_at AS last_updated_at,
            {_NO_PALETTE_COLUMNS}
        FROM governed_symbols gs
        JOIN symbol_revisions sr ON sr.id = gs.current_revision_id
        WHERE gs.owner_organization_id = :organization_id
          AND gs.visibility = 'organization_private'
          AND gs.organization_wide IS TRUE
          AND sr.lifecycle_state = 'approved'
    """
    return f"{public} UNION ALL {private}"


def _set_candidates_sql() -> str:
    """One row per palette member.

    A public symbol listed in several packs appears once, from its most
    recently effective publication. A private member must still belong to the
    caller's organization; its eligibility was checked when the palette was
    resolved.

    The public half does not scan `PUBLISHED_SYMBOLS_SQL`: the palette has
    just confirmed each member's current public revision through
    `current_public_symbols`, so this only fetches their publication rows,
    under the same conditions `active_public_symbol_projections` applies.
    Reading every public row to find about 1,000 members cost 0.41 s at
    52,500 public symbols. The rows a page returns are still loaded through
    `PUBLISHED_SYMBOLS_SQL` itself (`load_public_page_rows`).
    """
    return f"""
        (SELECT DISTINCT ON (m.governed_symbol_id)
            'public'::text AS source,
            m.governed_symbol_id,
            m.symbol_revision_id,
            pp.id::text AS page_id,
            pk.id::text AS pack_id,
            pk.title AS pack_title,
            pk.pack_code,
            pp.page_code,
            sr.revision_label,
            pp.effective_date,
            GREATEST(gs.updated_at, sr.created_at, pp.updated_at, pk.updated_at) AS last_updated_at,
            {_MEMBER_COLUMNS}
        FROM {_MEMBERS}
        JOIN governed_symbols gs
            ON gs.id = m.governed_symbol_id
           AND gs.visibility = 'public'
           AND gs.current_revision_id = m.symbol_revision_id
        JOIN symbol_revisions sr
            ON sr.id = m.symbol_revision_id
           AND sr.symbol_id = gs.id
           AND sr.lifecycle_state = 'published'
        JOIN published_pages pp
            ON pp.current_symbol_revision_id = sr.id
           AND pp.publication_state = 'active'
        JOIN publication_packs pk
            ON pk.id = pp.pack_id
           AND pk.status = 'published'
           AND pk.audience = 'public'
        JOIN pack_entries pe
            ON pe.pack_id = pk.id
           AND pe.published_page_id = pp.id
           AND pe.symbol_revision_id = sr.id
           AND pe.publication_state = 'active'
        ORDER BY m.governed_symbol_id, pp.effective_date DESC, pk.pack_code, pp.id)
        UNION ALL
        SELECT
            'organization_private'::text AS source,
            m.governed_symbol_id,
            m.symbol_revision_id,
            NULL::text AS page_id,
            NULL::text AS pack_id,
            NULL::text AS pack_title,
            NULL::text AS pack_code,
            NULL::text AS page_code,
            sr.revision_label,
            NULL::date AS effective_date,
            gs.updated_at AS last_updated_at,
            {_MEMBER_COLUMNS}
        FROM {_MEMBERS}
        JOIN governed_symbols gs ON gs.id = m.governed_symbol_id
        JOIN symbol_revisions sr ON sr.id = m.symbol_revision_id AND sr.symbol_id = gs.id
        WHERE gs.visibility = 'organization_private'
          AND gs.owner_organization_id = :organization_id
    """


_FACET_COLUMNS = (
    "display_id", "display_name", "id_sort_key", "name_sort_key", "search_text",
    "symbol_family", "disciplines", "categories", "formats", "use_cases",
)


def _candidates_with_facets_sql(scope: "CatalogSearchScope") -> str:
    """The candidates, each with its current facet row if there is one.

    A row whose facet values are missing or outdated has `facet_missing`
    set; the search fills those in and builds the table again.
    """
    facet_columns = ", ".join(f"f.{column}" for column in _FACET_COLUMNS)
    return f"""
        SELECT raw.*, {facet_columns}, f.symbol_revision_id IS NULL AS facet_missing
        FROM ({_candidates_sql(scope)}) raw
        JOIN symbol_revisions sr_live ON sr_live.id = raw.symbol_revision_id
        JOIN governed_symbols gs_live ON gs_live.id = raw.governed_symbol_id
        LEFT JOIN catalog_symbol_facets f
            ON f.symbol_revision_id = raw.symbol_revision_id
           AND f.rules_version = :rules_version
           AND f.revision_generation = sr_live.catalog_facet_generation
           AND f.symbol_generation = gs_live.catalog_facet_generation
    """


CANDIDATES_TABLE = "catalog_search_candidates"


def _build_candidates(session: Session, scope: CatalogSearchScope) -> None:
    """Evaluate the visibility rules once for this search, into a temporary table.

    Each row carries its facet values, so the stale check, the counts and the
    page all read this one table, and the live visibility join runs once per
    request rather than three times. The table is dropped when the
    transaction ends.

    Nested loops are switched off for this one statement.
    `PUBLISHED_SYMBOLS_SQL` joins the publication tables twice (directly and
    through `active_public_symbol_projections`), and the planner estimates a
    single row for the result, so with nested loops it probes the indexes
    once per public symbol, or worse. Measured on 52,500 public rows
    (2026-09-25): the Catalog build took 988 ms with nested loops and 336 ms
    without; a 1,000-item Set build took 40 s, and 1.6 s or 14.8 s with the
    member IDs pushed into the query, against well under a second with hash
    joins. Hash joins make both scopes one predictable pass over the public
    rows.
    """
    session.execute(text(f"DROP TABLE IF EXISTS pg_temp.{CANDIDATES_TABLE}"))
    # The counts query unnests every facet array, and the planner assumes
    # 100 elements per array, so its cost estimate crosses the JIT threshold
    # and about 700 ms goes on compiling a query that runs in about 400 ms
    # (measured on 53,001 rows, 2026-09-25). Off for this transaction only.
    session.execute(text("SET LOCAL jit = off"))
    session.execute(text("SET LOCAL enable_nestloop = off"))
    session.execute(
        text(f"CREATE TEMP TABLE {CANDIDATES_TABLE} ON COMMIT DROP AS {_candidates_with_facets_sql(scope)}"),
        {"rules_version": CATALOG_FACET_RULES_VERSION, **scope.parameters()},
    )
    session.execute(text("RESET enable_nestloop"))


def _stale_revision_ids(session: Session) -> list[uuid.UUID]:
    sql = f"SELECT DISTINCT symbol_revision_id FROM {CANDIDATES_TABLE} WHERE facet_missing"
    return [row.symbol_revision_id for row in session.execute(text(sql)).all()]


def _filter_clauses(request: CatalogSearchRequest, scope: CatalogSearchScope, params: dict) -> tuple[list[str], dict[str, str]]:
    """The always-applied clauses, and one clause per ticked facet."""
    clauses: list[str] = []
    query = request.query.strip().lower()
    if query:
        params["search_pattern"] = _like_pattern(query)
        clauses.append(
            "(c.search_text LIKE :search_pattern ESCAPE '\\'"
            " OR lower(COALESCE(c.pack_title, '')) LIKE :search_pattern ESCAPE '\\'"
            " OR lower(COALESCE(c.pack_code, '')) LIKE :search_pattern ESCAPE '\\'"
            " OR lower(COALESCE(c.page_code, '')) LIKE :search_pattern ESCAPE '\\'"
            # A set's own label for the symbol (Set tab only; null otherwise).
            " OR lower(COALESCE(c.display_label, '')) LIKE :search_pattern ESCAPE '\\')"
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
        column, is_array = scope.facet_fields[key]
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
    if request.sort == SET_ORDER_SORT:
        column_sort = "c.set_order"
    else:
        column_sort = COLUMN_FIELDS[request.sort][1]
    parts: list[str] = []
    preferred = [str(value).strip().upper() for value in request.preferred_formats if str(value or "").strip()]
    if preferred:
        ranks = " ".join(
            f"WHEN c.formats ? :preferred_{index} THEN {index}" for index in range(len(preferred))
        )
        for index, value in enumerate(preferred):
            params[f"preferred_{index}"] = value
        parts.append(f"CASE {ranks} ELSE {len(preferred)} END")
    direction = "DESC" if request.direction == "desc" else "ASC"
    nulls = "NULLS LAST" if direction == "DESC" else "NULLS FIRST"
    parts.append(f"{column_sort} {direction} {nulls}")
    parts.extend(['c.id_sort_key COLLATE "C"', "c.symbol_revision_id", "c.pack_id NULLS FIRST", "c.page_id NULLS FIRST"])
    return ", ".join(parts)


def search_catalog(session: Session, scope: CatalogSearchScope, request: CatalogSearchRequest) -> CatalogSearchPage:
    request.validate(scope)
    facet_fields = scope.facet_fields

    _build_candidates(session, scope)
    stale = _stale_revision_ids(session)
    if stale:
        # Committed with the rest of the search, below. Rare once the
        # backfill has run, so rebuilding the candidates costs little.
        compute_catalog_facets(session, stale)
        _build_candidates(session, scope)

    params: dict = {"rules_version": CATALOG_FACET_RULES_VERSION, **scope.parameters()}
    clauses, facet_clauses = _filter_clauses(request, scope, params)
    base = f"""
        WITH base AS (
            SELECT c.pack_title, c.group_name, c.palette_source,
                c.symbol_family, c.disciplines, c.categories, c.formats, c.use_cases,
                {_conjunction(clauses)} AS m_always,
                {", ".join(f"{facet_clauses.get(key, 'TRUE')} AS m_{key}" for key in facet_fields)}
            FROM {CANDIDATES_TABLE} c
        )
    """
    all_match = " AND ".join(["m_always", *(f"m_{key}" for key in facet_fields)])

    count_parts = [f"SELECT '' AS facet, '' AS value, count(*) FILTER (WHERE {all_match}) AS n FROM base"]
    for key, (column, is_array) in facet_fields.items():
        others = " AND ".join(["m_always", *(f"m_{other}" for other in facet_fields if other != key)])
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
    facets: dict[str, list[dict]] = {key: [] for key in facet_fields}
    for row in session.execute(text(base + " UNION ALL ".join(count_parts)), params).all():
        facet, value, count = row
        if facet == "":
            total = int(count)
        else:
            facets[facet].append({"value": value, "count": int(count)})
    for values in facets.values():
        values.sort(key=lambda item: (item["value"].casefold(), item["value"]))

    # The page query orders on expressions over the candidate rows, so it
    # reads the table directly rather than the flattened base CTE.
    page_sql = f"""
        SELECT c.source, c.governed_symbol_id, c.symbol_revision_id, c.page_id, c.pack_id,
            c.palette_source, c.set_order, c.group_name, c.display_label, c.preferred_format, c.notes
        FROM {CANDIDATES_TABLE} c
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
            "palette_entry": (
                {
                    "source": row.palette_source,
                    "sortOrder": row.set_order,
                    "groupName": row.group_name,
                    "displayLabel": row.display_label,
                    "preferredFormat": row.preferred_format,
                    "notes": row.notes,
                }
                if scope.is_set
                else None
            ),
        }
        for row in session.execute(text(page_sql), params).all()
    ]
    # Keeps any facet rows filled above, and drops the candidates table.
    session.commit()
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
