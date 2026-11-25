"""The visual match service behind `POST /api/v1/catalog/visual-match`.

Glue between the matcher (`visual_match.retrieval`) and the Catalog's own presentation: each returned
symbol is served as the Catalog serves any symbol (same summary, same links), never rebuilt here. An
organization session's own private symbols (D5) are summarised from the Catalog's private row, with the
same display ID and filter labels its Catalog card shows, and carry `source: "organization_private"`. The
query image is held in memory for the request and is not written, logged or put in a usage event.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from .catalog_search import catalog_symbol_summary
from .db import create_session_factory
from .published_catalog import PUBLISHED_SYMBOLS_WITH_GOVERNANCE_SQL
from .services.llm_image_embeddings import request_image_embedding
from .settings import SymgovAPISettings
from .visual_match import MATCHER_VERSION
from .visual_match.errors import VisualMatchUnavailable

if TYPE_CHECKING:  # the matcher needs numpy, scipy and scikit-image; the API must start without them
    from .visual_match.retrieval import VisualMatchResult

NO_CLOSE_MATCH = "No close match found among the symbols you can search."
MAX_IMAGE_BYTES = 8 * 1024 * 1024
BANDS_SHOWN = ("likely", "possible")
# Each match is seconds of CPU and four provider calls, so both the API-key route and the in-app route share
# one small pool and shed load with a 503 rather than queue.
SLOTS = threading.BoundedSemaphore(4)
MAX_JSON_BODY_BYTES = MAX_IMAGE_BYTES * 4 // 3 + 8192


def default_embed(settings: SymgovAPISettings, session_factory_provider: Callable[[], Any] | None = None):
    """The query embedder for one match. Its calls are recorded in the LLM usage ledger.

    Without a session factory `request_image_embedding` skips the ledger write, which left every user
    query unrecorded, failures included. One unpooled factory is built on first use and shared by the
    match's four rotation calls, as the other request-time LLM callers do.
    """
    if session_factory_provider is None:
        session_factory_provider = _shared_session_factory(settings)

    def embed(png: bytes) -> Mapping[str, Any]:
        return request_image_embedding(
            png=png, model=settings.visual_match_embedding_model, initiator_kind="user",
            session_factory_provider=session_factory_provider,
        )

    return embed


def _shared_session_factory(settings: SymgovAPISettings) -> Callable[[], Any]:
    lock = threading.Lock()
    built: list[Any] = []

    def provider() -> Any:
        with lock:
            if not built:
                built.append(create_session_factory(env_file=settings.db_env_file, nopool=True))
            return built[0]

    return provider


def private_symbol_summary(governed_symbol: Any, revision: Any) -> dict[str, Any]:
    """A private match, in the public summary's shape, from the row the Catalog serves for it."""
    from .catalog_facets import facet_values_for_row
    from .routes.published import organization_private_symbol_row

    row = organization_private_symbol_row(governed_symbol, revision)
    values = facet_values_for_row(row)
    preview_url = f"/api/v1/published/symbols/{governed_symbol.id}/preview"
    return {
        "displayId": values["display_id"],
        "catalogSymbolId": None,
        "symbolId": str(governed_symbol.id),
        "slug": governed_symbol.slug,
        "name": values["display_name"] or row["name"],
        "summary": row["summary"],
        "catalogDisciplines": values["disciplines"],
        "catalogCategories": values["categories"],
        "useCases": values["use_cases"],
        "availableFormats": values["formats"],
        "downloadAvailable": bool(row["downloadAssets"]),
        "preview": {"previewUrl": preview_url} if row["previewAsset"] else None,
        "links": {},
        "source": "organization_private",
    }


def _private_rows(session: Session, organization_id: uuid.UUID, revision_ids: Sequence[uuid.UUID]) -> dict[str, Any]:
    """The organization's own listed private symbols among these revisions, by the Catalog's rule."""
    from .models import GovernedSymbol, SymbolRevision

    if not revision_ids:
        return {}
    pairs = (
        session.query(GovernedSymbol, SymbolRevision)
        .join(SymbolRevision, SymbolRevision.id == GovernedSymbol.current_revision_id)
        .filter(
            SymbolRevision.id.in_(list(revision_ids)),
            GovernedSymbol.owner_organization_id == organization_id,
            GovernedSymbol.visibility == "organization_private",
            GovernedSymbol.organization_wide.is_(True),
            SymbolRevision.lifecycle_state == "approved",
        )
        .all()
    )
    return {str(revision.id): (symbol, revision) for symbol, revision in pairs}


def build_response(
    session: Session, result: VisualMatchResult, *, include_weak: bool, limit: int,
    organization_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    shown = [g for g in result.groups if include_weak or g.band in BANDS_SHOWN][:limit]
    ids = [m.revision_id for g in shown for m in g.members if not m.private]
    private_ids = [m.revision_id for g in shown for m in g.members if m.private]
    rows: dict[str, Any] = {}
    if ids:
        statement = text(PUBLISHED_SYMBOLS_WITH_GOVERNANCE_SQL + " AND sr.id IN :ids").bindparams(
            bindparam("ids", expanding=True, type_=UUID(as_uuid=True))
        )
        rows = {str(r.symbol_revision_id): r for r in session.execute(statement, {"ids": ids}).all()}
    # A private member is only ever summarised for its own organization, by the Catalog's own rule.
    private = _private_rows(session, organization_id, private_ids) if organization_id is not None else {}
    groups = []
    for g in shown:
        symbols = []
        for m in g.members:
            key = str(m.revision_id)
            if m.private:
                if key in private:
                    symbols.append(private_symbol_summary(*private[key]))
            elif key in rows:
                symbols.append({**catalog_symbol_summary(rows[key]), "source": "public"})
        if symbols:
            groups.append({"rank": len(groups) + 1, "band": g.band, "symbols": symbols})
    response: dict[str, Any] = {
        "matcherVersion": MATCHER_VERSION,
        "groups": groups,
        "degraded": result.degraded,
        "warnings": list(result.warnings),
        "strongerMatchOutsideFilters": result.stronger_match_outside_filters,
        "symbolsSearched": result.eligible,
    }
    if result.private_pending:
        response["privateSymbolsPending"] = result.private_pending
    if not groups:
        response["notice"] = NO_CLOSE_MATCH
    return response


def run_visual_match(
    session: Session,
    settings: SymgovAPISettings,
    *,
    image: bytes,
    filters: Mapping[str, Sequence[str]] | None,
    include_weak: bool = False,
    limit: int = 10,
    embed: Callable[[bytes], Mapping[str, Any]] | None = None,
    organization_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """`organization_id` is passed only for a signed-in organization session (D5): never for an API key."""
    try:
        from .visual_match.retrieval import visual_match
    except ImportError:
        raise VisualMatchUnavailable("dependencies_missing", "The matcher's libraries are not installed.") from None
    result = visual_match(
        session, image=image, embed=embed or default_embed(settings), filters=filters, organization_id=organization_id
    )
    return build_response(session, result, include_weak=include_weak, limit=limit, organization_id=organization_id)
