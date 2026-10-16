"""Runs `symbol_usage_maintenance.run_maintenance` on a timer.

Modelled on `catalog_embedding_worker`: one pass at a time across processes
(an advisory lock on its own connection), a first run shortly after start-up,
failures never end the loop and double the wait up to an hour, and only counts
are logged. `SYMGOV_USAGE_MAINTENANCE_SECONDS=0` turns it off.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from .catalog_embedding_worker import next_wait
from .db import create_database_engine
from .settings import SymgovAPISettings
from .symbol_usage_maintenance import MaintenanceResult, run_maintenance

LOGGER = logging.getLogger(__name__)

_FIRST_RUN_DELAY_SECONDS = 120.0
_ADVISORY_LOCK_KEY = 7_330_198_240_077_002


@dataclass(frozen=True)
class PassResult:
    status: str  # "ran" or "skipped_locked"
    counts: MaintenanceResult = MaintenanceResult()


def worker_enabled(settings: SymgovAPISettings) -> bool:
    return float(settings.usage_maintenance_seconds) > 0


def maintain_once(settings: SymgovAPISettings, *, engine: Any | None = None) -> PassResult:
    """One pass. Blocking; run it off the event loop."""
    own_engine = engine is None
    engine = engine or create_database_engine(env_file=settings.db_env_file, nopool=True)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock_connection:
            if not lock_connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}).scalar():
                return PassResult(status="skipped_locked")
            try:
                with sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)() as session:
                    counts = run_maintenance(session)
                    session.commit()
                return PassResult(status="ran", counts=counts)
            finally:
                lock_connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY})
    finally:
        if own_engine:
            engine.dispose()


async def run_usage_maintenance_worker(
    settings: SymgovAPISettings,
    stop_event: asyncio.Event,
    *,
    maintain: Callable[[SymgovAPISettings], PassResult] = maintain_once,
    first_delay: float = _FIRST_RUN_DELAY_SECONDS,
) -> None:
    interval = float(settings.usage_maintenance_seconds)
    failures = 0
    wait = min(first_delay, interval)
    while True:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait)
            return
        except asyncio.TimeoutError:
            pass
        try:
            result = await asyncio.to_thread(maintain, settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a transient database fault must not end the worker
            failures += 1
            LOGGER.warning("Usage maintenance pass failed (%s).", type(exc).__name__)
        else:
            failures = 0
            counts = result.counts
            if any((counts.purged_symbol_events, counts.purged_product_events, counts.purged_rate_windows)):
                LOGGER.info(
                    "Usage maintenance: purged symbol_events=%d product_events=%d rate_windows=%d",
                    counts.purged_symbol_events, counts.purged_product_events, counts.purged_rate_windows,
                )
        wait = next_wait(interval, failures)
