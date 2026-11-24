"""The visual match service behind `POST /api/v1/catalog/visual-match`.

Glue between the matcher (`visual_match.retrieval`) and the Catalog's own presentation: each returned
symbol is served as the Catalog serves any symbol (same summary, same links), never rebuilt here. The
query image is held in memory for the request and is not written, logged or put in a usage event.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from .catalog_search import catalog_symbol_summary
from .published_catalog import PUBLISHED_SYMBOLS_WITH_GOVERNANCE_SQL
from .services.llm_image_embeddings import request_image_embedding
from .settings import SymgovAPISettings
from .visual_match import MATCHER_VERSION
from .visual_match.retrieval import VisualMatchResult, visual_match

NO_CLOSE_MATCH = "No close match found among the symbols you can search."
MAX_IMAGE_BYTES = 8 * 1024 * 1024
BANDS_SHOWN = ("likely", "possible")
# Each match is seconds of CPU and four provider calls, so both the API-key route and the in-app route share
# one small pool and shed load with a 503 rather than queue.
SLOTS = threading.BoundedSemaphore(4)
MAX_JSON_BODY_BYTES = MAX_IMAGE_BYTES * 4 // 3 + 8192


def default_embed(settings: SymgovAPISettings, session_factory_provider: Callable[[], Any] | None = None):
    def embed(png: bytes) -> Mapping[str, Any]:
        return request_image_embedding(
            png=png, model=settings.visual_match_embedding_model, initiator_kind="user",
            session_factory_provider=session_factory_provider,
        )

    return embed


def build_response(
    session: Session, result: VisualMatchResult, *, include_weak: bool, limit: int
) -> dict[str, Any]:
    shown = [g for g in result.groups if include_weak or g.band in BANDS_SHOWN][:limit]
    ids = [m.revision_id for g in shown for m in g.members]
    rows: dict[str, Any] = {}
    if ids:
        statement = text(PUBLISHED_SYMBOLS_WITH_GOVERNANCE_SQL + " AND sr.id IN :ids").bindparams(
            bindparam("ids", expanding=True, type_=UUID(as_uuid=True))
        )
        rows = {str(r.symbol_revision_id): r for r in session.execute(statement, {"ids": ids}).all()}
    groups = []
    for g in shown:
        symbols = [catalog_symbol_summary(rows[str(m.revision_id)]) for m in g.members if str(m.revision_id) in rows]
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
) -> dict[str, Any]:
    result = visual_match(session, image=image, embed=embed or default_embed(settings), filters=filters)
    return build_response(session, result, include_weak=include_weak, limit=limit)
