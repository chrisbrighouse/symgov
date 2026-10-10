"""Ed's read-only search of the symbol Catalog.

Ed's older `search_accessible_symbols` only sees symbols in the active symbol
sets of the selected project. This tool searches the Catalog the way the
Catalog tab does, with the same scope: every published public symbol, plus the
signed-in organization's organization-wide private symbols for an
organization-bound session. It calls `catalog_browse_search.search_catalog`
unchanged, so visibility is decided there and by the checks below, never by
the model, and nothing here can widen it.

The model gets short keyword matches, a total, and the counts behind each
filter so it can narrow a broad search. It never receives a UUID it could pass
back as scope, and it cannot name a user or an organization.

This module is deliberately not an approved-knowledge source: editing it does
not invalidate the steward's bundle.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, computed_field
from sqlalchemy.orm import Session

from .catalog_browse_search import (
    CatalogSearchInputError,
    CatalogSearchRequest,
    CatalogSearchScope,
    search_catalog,
)
from .catalog_embeddings import (
    public_visible_revision_ids,
    revisions_matching_facets,
    semantic_candidates,
)
from .db import create_session_factory
from .ed_read_tools import _resolve_ed_read_authority
from .ed_retrieval import _STOP_WORDS
from .models import GovernedSymbol, SymbolRevision
from .settings import SymgovAPISettings
from .services.llm_embeddings import EmbeddingError, request_llm_embeddings
from .symbol_identity import governed_symbol_human_readable_id


MAX_CATALOG_RESULTS = 25
_MAX_FACET_VALUES = 12
_MAX_SUMMARY_CHARS = 200
_MAX_KEYWORDS = 6
# Words that describe the question rather than the symbol. The Catalog search
# needs every word to match, so "what pump symbols are there" would match
# nothing; "pump" is what the person means.
_GENERIC_WORDS = frozenset(
    """
    symbol symbols catalog catalogue show list find search exist exists
    available please give tell any get
    """.split()
)
# Facet argument -> the Catalog facet key it filters.
_FACET_ARGUMENTS = {
    "discipline": "catalogDisciplines",
    "category": "catalogCategories",
    "use_case": "useCases",
    "format": "availableFormats",
}
# The only facets whose counts go back to the model.
_REPORTED_FACETS = {
    "disciplines": "catalogDisciplines",
    "categories": "catalogCategories",
    "use_cases": "useCases",
    "formats": "availableFormats",
}


class EdCatalogSearchCall(BaseModel):
    """Strict model-facing arguments. Scope is deliberately absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    tool: Literal["search_catalog"]
    query: str | None = Field(default=None, max_length=200)
    discipline: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=120)
    use_case: str | None = Field(default=None, max_length=120)
    format: str | None = Field(default=None, max_length=60)
    limit: int = Field(default=10, ge=1, le=MAX_CATALOG_RESULTS)


def _citation(record_type: str, raw_id: str, as_of: datetime) -> dict[str, str]:
    # The same opaque-reference recipe as `ed_read_tools._EdReadModel`, so a
    # symbol found here and by `search_accessible_symbols` carries one ref.
    opaque_id = uuid.uuid5(uuid.NAMESPACE_URL, f"symgov:ed:{record_type}:{raw_id}")
    return {
        "source_kind": "live_record",
        "record_type": record_type,
        "record_ref": f"live:{record_type}:{opaque_id}",
        "as_of": as_of.isoformat(),
    }


class _CatalogRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EdCatalogFacetValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    value: str
    count: int


class EdCatalogSymbolRead(_CatalogRead):
    id: str = Field(exclude=True)
    display_id: str
    name: str
    category: str
    discipline: str
    source: Literal["public", "organization_private"]
    summary: str | None = None
    keywords: tuple[str, ...] = ()
    # "keyword" matched the words searched for. "similar" matched by meaning
    # and is ranked by similarity (1.0 is identical, higher is closer).
    match: Literal["keyword", "similar"] = "keyword"
    similarity: float | None = None

    @computed_field
    @property
    def citation(self) -> dict[str, str]:
        return _citation("symbol", self.id, self.as_of)


class EdCatalogSearchSummary(_CatalogRead):
    """What the search found, so an empty or broad result can still be cited."""

    key: str = Field(exclude=True)
    searched_for: str
    filters: dict[str, str]
    total_matches: int
    shown: int
    facets: dict[str, tuple[EdCatalogFacetValue, ...]]
    # How many of `shown` are meaning-based ("similar") matches, and whether
    # that search ran. total_matches counts keyword matches only.
    similar_shown: int = 0
    semantic_search: Literal["not_used", "used", "unavailable"] = "not_used"

    @computed_field
    @property
    def citation(self) -> dict[str, str]:
        return _citation("catalog_search", self.key, self.as_of)


def catalog_keywords(query: str | None) -> str:
    """The query without question words, so every remaining word can match."""
    words = (query or "").lower().replace("?", " ").replace(",", " ").split()
    kept = [
        word.strip(".!;:\"'()")
        for word in words
        if word.strip(".!;:\"'()") not in _STOP_WORDS and word.strip(".!;:\"'()") not in _GENERIC_WORDS
    ]
    return " ".join(word for word in kept if word)


def _trimmed(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.split())
    return collapsed[:limit] or None


def _keywords(payload: dict) -> tuple[str, ...]:
    raw = payload.get("keywords") or payload.get("search_terms") or []
    if not isinstance(raw, list):
        return ()
    return tuple(
        text for item in raw[:_MAX_KEYWORDS] if (text := _trimmed(item, 40))
    )


def _symbols_for_entries(
    session: Session, entries: list[dict], organization_id: uuid.UUID | None
) -> list[EdCatalogSymbolRead]:
    """The symbols behind Catalog entries, each re-checked against its owner."""
    symbols: list[EdCatalogSymbolRead] = []
    if not entries:
        return symbols
    revision_ids = [entry["symbol_revision_id"] for entry in entries]
    by_revision = {
        revision.id: (symbol, revision)
        for symbol, revision in session.query(GovernedSymbol, SymbolRevision)
        .join(SymbolRevision, SymbolRevision.symbol_id == GovernedSymbol.id)
        .filter(SymbolRevision.id.in_(revision_ids))
        .all()
    }
    for entry in entries:
        pair = by_revision.get(entry["symbol_revision_id"])
        if pair is None:
            continue
        symbol, revision = pair
        if entry["source"] == "public":
            if symbol.visibility != "public":
                continue
            display_id = symbol.catalog_symbol_id or symbol.slug
            source = "public"
        else:
            # Mirrors the Catalog tab: only this organization's
            # organization-wide private symbols, never another tenant's.
            if (
                organization_id is None
                or symbol.owner_organization_id != organization_id
                or symbol.visibility != "organization_private"
                or symbol.organization_wide is not True
            ):
                continue
            display_id = governed_symbol_human_readable_id(session, symbol) or symbol.slug
            source = "organization_private"
        payload = revision.payload_json if isinstance(revision.payload_json, dict) else {}
        symbols.append(
            EdCatalogSymbolRead(
                id=str(symbol.id),
                display_id=str(display_id),
                name=str(payload.get("name") or payload.get("canonical_name") or symbol.canonical_name),
                category=str(symbol.category),
                discipline=str(symbol.discipline),
                source=source,
                summary=_trimmed(payload.get("summary") or payload.get("description"), _MAX_SUMMARY_CHARS),
                keywords=_keywords(payload),
            )
        )
    return symbols


def _initiator_pseudonym(user_id: Any, settings: SymgovAPISettings) -> str:
    # The same recipe as `services.ed_orchestration._initiator_pseudonym`, so
    # one person's chat and embedding calls share a pseudonym in the ledger.
    secret = settings.auth_login_hash_secret.encode("utf-8")
    return hmac.new(secret, f"ed-guru:{user_id}".encode("utf-8"), hashlib.sha256).hexdigest()


def _similar_symbols(
    session: Session,
    settings: SymgovAPISettings,
    *,
    user_id: Any,
    text_query: str,
    facets: dict[str, list[str]],
    skip_symbol_ids: set[uuid.UUID],
    wanted: int,
) -> list[EdCatalogSymbolRead]:
    """Public symbols whose meaning is close to the query, best first.

    The index is public-only and only ranks: every candidate is re-checked
    against the live public-Catalog rule and the caller's own filters before
    it is loaded, and loaded through the same ownership checks as a keyword
    match. Raises `EmbeddingError` if the query cannot be embedded.
    """
    model = settings.catalog_embedding_model
    embedded = request_llm_embeddings(
        texts=[text_query],
        model=model,
        feature="ed_catalog_search",
        initiator_kind="user",
        initiator_pseudonym=_initiator_pseudonym(user_id, settings),
        timeout=10.0,
        session_factory_provider=lambda: create_session_factory(env_file=settings.db_env_file, nopool=True),
    )
    hits = semantic_candidates(
        session,
        embedded["vectors"][0],
        model=model,
        min_similarity=settings.ed_semantic_min_similarity,
        top_k=max(40, wanted * 4),
    )
    hits = [hit for hit in hits if hit.governed_symbol_id not in skip_symbol_ids]
    if not hits:
        return []
    visible = public_visible_revision_ids(session, [hit.symbol_revision_id for hit in hits])
    hits = [hit for hit in hits if hit.symbol_revision_id in visible]
    if facets and hits:
        allowed = revisions_matching_facets(session, [hit.symbol_revision_id for hit in hits], facets)
        hits = [hit for hit in hits if hit.symbol_revision_id in allowed]
    hits = hits[:wanted]
    loaded = _symbols_for_entries(
        session,
        [{"source": "public", "symbol_revision_id": hit.symbol_revision_id} for hit in hits],
        None,
    )
    scores = {str(hit.governed_symbol_id): hit.similarity for hit in hits}
    return [
        symbol.model_copy(update={"match": "similar", "similarity": scores.get(symbol.id)})
        for symbol in loaded
    ]


def search_catalog_for_ed(
    session: Session,
    request: Request,
    settings: SymgovAPISettings,
    call: EdCatalogSearchCall,
) -> list[BaseModel]:
    """A summary, then the matching symbols the caller may see."""
    authority = _resolve_ed_read_authority(session, request, settings)
    principal = authority.principal
    organization_id = None
    if (
        principal is not None
        and settings.organizations_enabled
        and settings.organization_symbols_enabled
    ):
        organization_id = principal.organization_id

    facets = {
        facet_key: [value.strip()]
        for argument, facet_key in _FACET_ARGUMENTS.items()
        if (value := getattr(call, argument)) and value.strip()
    }
    keywords = catalog_keywords(call.query)
    try:
        page = search_catalog(
            session,
            CatalogSearchScope(user_id=uuid.UUID(str(authority.user.id)), organization_id=organization_id),
            CatalogSearchRequest(query=keywords, facets=facets, page_size=call.limit),
        )
    except CatalogSearchInputError:
        page = None

    entries = page.entries if page is not None else []
    symbols = _symbols_for_entries(session, entries, organization_id)

    semantic_state: Literal["not_used", "used", "unavailable"] = "not_used"
    similar: list[EdCatalogSymbolRead] = []
    query_text = " ".join((call.query or "").split())
    if settings.ed_semantic_search_enabled and keywords and query_text and len(symbols) < call.limit:
        try:
            similar = _similar_symbols(
                session,
                settings,
                user_id=authority.user.id,
                text_query=query_text,
                facets=facets,
                skip_symbol_ids={uuid.UUID(symbol.id) for symbol in symbols},
                wanted=call.limit - len(symbols),
            )
            semantic_state = "used"
        except Exception:
            # Meaning-based search is an extra: if it cannot run, the keyword
            # result stands and the model is told so.
            semantic_state = "unavailable"
    symbols = [*symbols, *similar]

    reported_facets = {
        name: tuple(
            EdCatalogFacetValue(value=str(item["value"])[:120], count=int(item["count"]))
            for item in sorted(
                ((page.facets.get(key) if page is not None else None) or []),
                key=lambda item: (-int(item["count"]), str(item["value"]).casefold()),
            )[:_MAX_FACET_VALUES]
            if int(item["count"]) > 0
        )
        for name, key in _REPORTED_FACETS.items()
    }
    key = "|".join(
        [str(authority.user.id), str(organization_id or ""), keywords, *(f"{k}={v[0]}" for k, v in sorted(facets.items()))]
    )
    summary = EdCatalogSearchSummary(
        key=key,
        searched_for=keywords,
        filters={argument: str(getattr(call, argument)).strip()
                 for argument in _FACET_ARGUMENTS if getattr(call, argument) and str(getattr(call, argument)).strip()},
        total_matches=page.total if page is not None else 0,
        shown=len(symbols),
        facets=reported_facets,
        similar_shown=len(similar),
        semantic_search=semantic_state,
    )
    return [summary, *symbols]
