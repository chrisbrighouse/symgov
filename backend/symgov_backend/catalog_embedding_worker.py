"""Keeps the Catalog embedding index current as symbols are published.

Symbols reach the public Catalog by several routes (the normal publication
handoff, bulk imports), and an embedding call is a network round trip that
must never sit inside a publication transaction. So nothing hooks
publication. This worker reconciles instead: every few minutes it runs the
same idempotent indexer the `catalog-embeddings index` command uses, which
embeds only published public revisions whose text is new or changed and prunes
vectors for revisions that left the Catalog. A symbol published at any time is
searchable by meaning within one interval, a changed description is re-embedded,
and a withdrawn symbol's vector is removed.

Safety:
- It runs only when Ed's semantic search is enabled, a provider key exists and
  the interval is above zero (`SYMGOV_CATALOG_EMBEDDING_SYNC_SECONDS`, 0 = off).
- A PostgreSQL advisory lock on its own connection makes overlapping runs
  (a second API process, or the command line) skip rather than double-embed.
- A cycle embeds at most `MAX_PER_CYCLE` symbols, so a large import is spread
  over a few cycles rather than one long call burst.
- A provider fault never raises out of the loop. After consecutive failures the
  wait doubles, up to an hour, and resets on success.
- The only things it logs are counts, never symbol text.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from .catalog_embeddings import index_public_symbols
from .db import create_database_engine
from .services.llm_embeddings import request_llm_embeddings
from .services.llm_router import provider_api_key
from .settings import SymgovAPISettings


LOGGER = logging.getLogger(__name__)

MAX_PER_CYCLE = 256
BATCH_SIZE = 32
_MAX_BACKOFF_SECONDS = 3600.0
_FIRST_RUN_DELAY_SECONDS = 60.0
# One fixed key for "the Catalog embedding sync": any int64 will do.
_ADVISORY_LOCK_KEY = 7_330_198_240_077_001


@dataclass(frozen=True)
class SyncResult:
    status: str  # "ran", "skipped_locked" or "skipped_no_key"
    embedded: int = 0
    pending: int = 0
    pruned: int = 0
    failed_batches: int = 0


def worker_enabled(settings: SymgovAPISettings) -> bool:
    return bool(settings.ed_semantic_search_enabled) and float(settings.catalog_embedding_sync_seconds) > 0


def sync_once(
    settings: SymgovAPISettings,
    *,
    embed: Callable[[list[str]], Any] | None = None,
    engine: Any | None = None,
    limit: int = MAX_PER_CYCLE,
) -> SyncResult:
    """One reconciliation pass. Blocking; run it off the event loop."""
    if embed is None and not provider_api_key("openrouter"):
        return SyncResult(status="skipped_no_key")
    own_engine = engine is None
    engine = engine or create_database_engine(env_file=settings.db_env_file, nopool=True)
    try:
        # The lock lives on its own autocommit connection, so it survives the
        # indexer's commits and leaves no transaction open while it is held.
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock_connection:
            if not lock_connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}
            ).scalar():
                return SyncResult(status="skipped_locked")
            try:
                session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
                model = settings.catalog_embedding_model

                def default_embed(texts: list[str]) -> Any:
                    return request_llm_embeddings(
                        texts=texts,
                        model=model,
                        feature="catalog_embedding_sync",
                        initiator_kind="scheduled_worker",
                        session_factory_provider=lambda: session_factory,
                    )

                with session_factory() as session:
                    report = index_public_symbols(
                        session,
                        model=model,
                        embed=embed or default_embed,
                        apply=True,
                        batch_size=BATCH_SIZE,
                        limit=limit,
                    )
                return SyncResult(
                    status="ran",
                    embedded=report.embedded,
                    pending=report.pending,
                    pruned=report.pruned,
                    failed_batches=report.failed_batches,
                )
            finally:
                lock_connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY})
    finally:
        if own_engine:
            engine.dispose()


def next_wait(interval: float, consecutive_failures: int) -> float:
    """The interval, doubled for each consecutive failure, capped at an hour."""
    if consecutive_failures <= 0:
        return interval
    return min(interval * (2 ** min(consecutive_failures, 10)), max(interval, _MAX_BACKOFF_SECONDS))


async def run_catalog_embedding_worker(
    settings: SymgovAPISettings,
    stop_event: asyncio.Event,
    *,
    sync: Callable[[SymgovAPISettings], SyncResult] = sync_once,
    first_delay: float = _FIRST_RUN_DELAY_SECONDS,
) -> None:
    interval = float(settings.catalog_embedding_sync_seconds)
    failures = 0
    wait = min(first_delay, interval)
    while True:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait)
            return
        except asyncio.TimeoutError:
            pass
        result: SyncResult | None = None
        try:
            result = await asyncio.to_thread(sync, settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a transient database or provider fault must not end the worker
            failures += 1
            LOGGER.warning("Catalog embedding sync cycle failed (%s).", type(exc).__name__)
        else:
            if result.status == "skipped_no_key":
                LOGGER.warning("Catalog embedding sync is enabled but no provider key is configured.")
            if result.failed_batches:
                failures += 1
                LOGGER.warning(
                    "Catalog embedding sync: %d batch(es) failed; will retry.", result.failed_batches
                )
            else:
                failures = 0
            if result.embedded or result.pruned:
                LOGGER.info(
                    "Catalog embedding sync: embedded=%d pending=%d pruned=%d",
                    result.embedded, result.pending, result.pruned,
                )
        # A backlog left by the per-cycle limit is worked off promptly.
        backlog = result.pending if result is not None and failures == 0 else 0
        wait = min(interval, 30.0) if backlog else next_wait(interval, failures)
