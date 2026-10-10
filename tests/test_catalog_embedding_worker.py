"""The background worker that keeps the embedding index current: loop, backoff, wiring."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from symgov_backend import catalog_embedding_worker as module


def _settings(**overrides):
    values = {"ed_semantic_search_enabled": True, "catalog_embedding_sync_seconds": 0.01}
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("flag", "seconds", "expected"),
    [(True, 300.0, True), (False, 300.0, False), (True, 0.0, False), (False, 0.0, False)],
)
def test_the_worker_runs_only_with_semantic_search_on_and_an_interval(flag, seconds, expected):
    assert module.worker_enabled(_settings(ed_semantic_search_enabled=flag, catalog_embedding_sync_seconds=seconds)) is expected


def test_the_wait_doubles_with_each_failure_up_to_an_hour():
    assert module.next_wait(300, 0) == 300
    assert module.next_wait(300, 1) == 600
    assert module.next_wait(300, 2) == 1200
    assert module.next_wait(300, 9) == 3600
    assert module.next_wait(7200, 3) == 7200  # never below the configured interval


async def _run(sync, *, seconds=0.5, **settings):
    stop = asyncio.Event()
    task = asyncio.create_task(
        module.run_catalog_embedding_worker(_settings(**settings), stop, sync=sync, first_delay=0.0)
    )
    await asyncio.sleep(seconds)
    stop.set()
    await asyncio.wait_for(task, timeout=2)


def test_the_loop_runs_sync_repeatedly_with_the_settings_and_stops_on_the_event():
    calls = []

    def sync(settings):
        calls.append(settings)
        return module.SyncResult(status="ran")

    asyncio.run(_run(sync))

    assert len(calls) >= 3 and calls[0].catalog_embedding_sync_seconds == 0.01


def test_a_failing_cycle_does_not_end_the_worker_and_logs_only_the_error_type(caplog):
    calls = []

    def sync(settings):
        calls.append(1)
        raise RuntimeError("secret detail that must not be logged")

    with caplog.at_level(logging.WARNING, logger=module.LOGGER.name):
        asyncio.run(_run(sync, seconds=0.3, catalog_embedding_sync_seconds=0.001))

    assert len(calls) >= 2
    assert any("RuntimeError" in record.getMessage() for record in caplog.records)
    assert "secret detail" not in caplog.text


def test_failed_batches_back_off_and_a_success_resets(caplog):
    results = iter(
        [module.SyncResult(status="ran", failed_batches=1)] * 2 + [module.SyncResult(status="ran", embedded=3)] * 50
    )

    def sync(settings):
        return next(results)

    with caplog.at_level(logging.INFO, logger=module.LOGGER.name):
        asyncio.run(_run(sync, seconds=0.5, catalog_embedding_sync_seconds=0.02))

    messages = [record.getMessage() for record in caplog.records]
    assert any("batch(es) failed" in message for message in messages)
    assert any("embedded=3" in message for message in messages)


def test_a_missing_key_is_reported_not_raised(caplog):
    with caplog.at_level(logging.WARNING, logger=module.LOGGER.name):
        asyncio.run(_run(lambda settings: module.SyncResult(status="skipped_no_key"), seconds=0.2))

    assert any("no provider key" in record.getMessage() for record in caplog.records)


def test_sync_without_a_provider_key_does_nothing(monkeypatch):
    monkeypatch.setattr(module, "provider_api_key", lambda provider: "")

    assert module.sync_once(_settings()) == module.SyncResult(status="skipped_no_key")


# --- Wiring into the app ---


def _app(monkeypatch, **overrides):
    from symgov_backend import app as app_module

    started = []

    async def fake_worker(settings, stop_event):
        started.append(settings)
        await stop_event.wait()

    monkeypatch.setattr(app_module, "run_catalog_embedding_worker", fake_worker)
    real = app_module.get_settings()
    monkeypatch.setattr(app_module, "get_settings", lambda: dataclasses.replace(real, **overrides))
    return app_module.create_app(), started


def test_the_app_starts_and_stops_the_worker_when_enabled(monkeypatch):
    app, started = _app(monkeypatch, ed_semantic_search_enabled=True, catalog_embedding_sync_seconds=300.0)

    with TestClient(app):
        assert len(started) == 1
        assert not app.state.catalog_embedding_task.done()
        task = app.state.catalog_embedding_task

    assert task.done()


@pytest.mark.parametrize(
    "overrides",
    [
        {"ed_semantic_search_enabled": False, "catalog_embedding_sync_seconds": 300.0},
        {"ed_semantic_search_enabled": True, "catalog_embedding_sync_seconds": 0.0},
    ],
)
def test_the_app_does_not_start_the_worker_otherwise(monkeypatch, overrides):
    app, started = _app(monkeypatch, **overrides)

    with TestClient(app):
        assert started == []
        assert getattr(app.state, "catalog_embedding_task", None) is None
