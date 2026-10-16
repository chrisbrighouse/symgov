"""The background worker that tidies the usage ledgers: loop, backoff, wiring."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from symgov_backend import usage_maintenance_worker as module
from symgov_backend.symbol_usage_maintenance import MaintenanceResult


def _settings(**overrides):
    values = {"usage_maintenance_seconds": 0.01}
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(("seconds", "expected"), [(3600.0, True), (0.01, True), (0.0, False)])
def test_the_worker_runs_only_with_an_interval(seconds, expected):
    assert module.worker_enabled(_settings(usage_maintenance_seconds=seconds)) is expected


async def _run(maintain, *, seconds=0.5):
    stop = asyncio.Event()
    task = asyncio.create_task(module.run_usage_maintenance_worker(_settings(), stop, maintain=maintain, first_delay=0.0))
    await asyncio.sleep(seconds)
    stop.set()
    await asyncio.wait_for(task, timeout=2)


def test_the_loop_runs_repeatedly_and_stops_on_the_event():
    calls = []

    def maintain(settings):
        calls.append(settings)
        return module.PassResult(status="ran")

    asyncio.run(_run(maintain, seconds=0.8))
    assert len(calls) >= 2


def test_a_failing_pass_does_not_end_the_worker_and_logs_only_the_error_type(caplog):
    calls = []

    def maintain(settings):
        calls.append(1)
        raise RuntimeError("password=hunter2 in a connection string")

    with caplog.at_level(logging.WARNING):
        asyncio.run(_run(maintain, seconds=0.3))
    assert calls
    assert "RuntimeError" in caplog.text and "hunter2" not in caplog.text


def test_purges_are_logged_as_counts_only():
    # A handler on the module's own logger, not caplog: other tests configure logging for the whole app.
    records = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = logging.getLogger(module.__name__)
    handler, old_level = Collect(level=logging.INFO), logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        def maintain(settings):
            return module.PassResult(status="ran", counts=MaintenanceResult(purged_symbol_events=7, purged_product_events=2, purged_rate_windows=3))

        asyncio.run(_run(maintain, seconds=0.6))
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    assert any("symbol_events=7 product_events=2 rate_windows=3" in message for message in records)


def _app(monkeypatch, **overrides):
    from symgov_backend import app as app_module

    started = []

    async def fake_worker(settings, stop_event):
        started.append(settings)
        await stop_event.wait()

    monkeypatch.setattr(app_module, "run_usage_maintenance_worker", fake_worker)
    real = app_module.get_settings()
    monkeypatch.setattr(app_module, "get_settings", lambda: dataclasses.replace(real, **overrides))
    return app_module.create_app(), started


def test_the_app_starts_and_stops_the_worker_when_enabled(monkeypatch):
    app, started = _app(monkeypatch, usage_maintenance_seconds=3600.0)
    with TestClient(app):
        assert len(started) == 1
        task = app.state.usage_maintenance_task
        assert not task.done()
    assert task.done()


def test_the_app_does_not_start_the_worker_when_the_interval_is_zero(monkeypatch):
    app, started = _app(monkeypatch, usage_maintenance_seconds=0.0)
    with TestClient(app):
        assert started == []
        assert getattr(app.state, "usage_maintenance_task", None) is None
