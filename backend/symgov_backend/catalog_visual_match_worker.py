"""Keeps the visual descriptor index current as symbols are published.

Same shape and safety rules as `catalog_embedding_worker`: nothing hooks
publication; an idempotent pass renders and embeds only published public
revisions whose preview asset is new or changed, and prunes rows for revisions
that left the Catalog. It runs only when `SYMGOV_VISUAL_MATCH_SYNC_SECONDS` is
above zero and a provider key exists, takes a PostgreSQL advisory lock so
overlapping runs skip, builds at most `MAX_PER_CYCLE` symbols a cycle, backs
off on failure, and logs counts only, never symbol content or image bytes.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from .catalog_embedding_worker import next_wait
from .db import create_database_engine
from .runtime import download_object_bytes
from .services.llm_image_embeddings import request_image_embedding
from .services.llm_router import provider_api_key
from .settings import SymgovAPISettings

if TYPE_CHECKING:
    from .visual_match.rasterizer import RenderConfig

LOGGER = logging.getLogger(__name__)

MAX_PER_CYCLE = 64
_FIRST_RUN_DELAY_SECONDS = 90.0
_ADVISORY_LOCK_KEY = 7_330_198_240_077_002
FONT_SET_ID = "24ddb36449ea"   # the pinned DejaVu + Liberation set (visual_match/fonts/SHA256SUMS)


@dataclass(frozen=True)
class VisualSyncResult:
    status: str  # "ran", "skipped_locked", "skipped_no_key" or "skipped_dependencies"
    built: int = 0
    pending: int = 0
    pruned: int = 0
    degraded: int = 0
    gaps: int = 0


def worker_enabled(settings: SymgovAPISettings) -> bool:
    return float(settings.visual_match_sync_seconds) > 0


def render_config(settings: SymgovAPISettings, resources_dir: str) -> "RenderConfig":
    # Imported here: the rasteriser needs numpy and Pillow, and the API must start without the matcher's libraries.
    from .visual_match.rasterizer import RenderConfig, vendored_font_dir

    return RenderConfig(
        font_dirs=(settings.visual_match_font_dir or vendored_font_dir(),),
        resources_dir=resources_dir,
        font_set_id=FONT_SET_ID,
    )


def sync_once(
    settings: SymgovAPISettings,
    *,
    embed: Callable[[bytes], Any] | None = None,
    fetch: Callable[[str], bytes] | None = None,
    engine: Any | None = None,
    limit: int = MAX_PER_CYCLE,
) -> VisualSyncResult:
    """One reconciliation pass. Blocking; run it off the event loop."""
    try:
        from .visual_match.index import MAX_SOURCE_BYTES, index_visual_descriptors
    except ImportError:
        return VisualSyncResult(status="skipped_dependencies")
    if embed is None and not provider_api_key("openrouter"):
        return VisualSyncResult(status="skipped_no_key")
    own_engine = engine is None
    engine = engine or create_database_engine(env_file=settings.db_env_file, nopool=True)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock_connection:
            if not lock_connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}
            ).scalar():
                return VisualSyncResult(status="skipped_locked")
            try:
                session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
                model = settings.visual_match_embedding_model

                def default_embed(png: bytes) -> Any:
                    return request_image_embedding(
                        png=png, model=model, initiator_kind="scheduled_worker",
                        session_factory_provider=lambda: session_factory,
                    )

                def default_fetch(key: str) -> bytes:
                    return download_object_bytes(
                        object_key=key, env_file=settings.storage_env_file, max_bytes=MAX_SOURCE_BYTES + 1
                    )["payload"]

                with tempfile.TemporaryDirectory(prefix="symgov-vm-") as resources, session_factory() as session:
                    report = index_visual_descriptors(
                        session,
                        cfg=render_config(settings, resources),
                        fetch=fetch or default_fetch,
                        embed=embed or default_embed,
                        apply=True,
                        limit=limit,
                    )
                return VisualSyncResult(
                    status="ran", built=report.built, pending=report.pending, pruned=report.pruned,
                    degraded=report.degraded, gaps=sum(report.gaps.values()),
                )
            finally:
                lock_connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY})
    finally:
        if own_engine:
            engine.dispose()


async def run_visual_match_worker(
    settings: SymgovAPISettings,
    stop_event: asyncio.Event,
    *,
    sync: Callable[[SymgovAPISettings], VisualSyncResult] = sync_once,
    first_delay: float = _FIRST_RUN_DELAY_SECONDS,
) -> None:
    interval = float(settings.visual_match_sync_seconds)
    failures = 0
    wait = min(first_delay, interval)
    while True:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait)
            return
        except asyncio.TimeoutError:
            pass
        result: VisualSyncResult | None = None
        try:
            result = await asyncio.to_thread(sync, settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a transient database or provider fault must not end the worker
            failures += 1
            LOGGER.warning("Visual match sync cycle failed (%s).", type(exc).__name__)
        else:
            if result.status == "skipped_no_key":
                LOGGER.warning("Visual match sync is enabled but no provider key is configured.")
            if result.status == "skipped_dependencies":
                LOGGER.warning("Visual match sync is enabled but the matcher's libraries are not installed.")
            # Degraded rows (no stage-1 embedding) are retried on the next pass, with backoff.
            failures = failures + 1 if result.degraded else 0
            if result.built or result.pruned:
                LOGGER.info(
                    "Visual match sync: built=%d degraded=%d pending=%d pruned=%d gaps=%d",
                    result.built, result.degraded, result.pending, result.pruned, result.gaps,
                )
        backlog = result.pending if result is not None and failures == 0 else 0
        wait = min(interval, 30.0) if backlog else next_wait(interval, failures)
