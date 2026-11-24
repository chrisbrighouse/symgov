"""The visual descriptor builder and its worker: the pure parts (no database)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from symgov_backend import catalog_visual_match_worker as worker
from symgov_backend.visual_match.imaging import from_skeleton, normalize, pack_skeleton
from symgov_backend.visual_match.index import build_descriptor, pack_floats, unpack_floats
from symgov_backend.visual_match.rasterizer import RenderConfig, RenderError, render, vendored_font_dir

CIRCLE = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="30" fill="none" stroke="black" stroke-width="3"/><line x1="20" y1="50" x2="80" y2="50" stroke="black" stroke-width="3"/></svg>'
BLANK = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"></svg>'


@pytest.fixture
def cfg(tmp_path):
    return RenderConfig(font_dirs=(vendored_font_dir(),), resources_dir=str(tmp_path), font_set_id="test")


def _vector(seed=1, n=768):
    v = np.random.default_rng(seed).normal(size=n)
    return list(v / np.linalg.norm(v))


def test_a_symbol_gets_both_descriptors_and_its_centreline(cfg):
    built = build_descriptor(CIRCLE, "image/svg+xml", cfg, lambda png: {"vector": _vector(), "model": "m/x"})
    assert len(built.gemini) == 768 and built.gemini_model == "m/x"
    assert built.local.shape == (360,) and abs(np.linalg.norm(built.local) - 1) < 1e-4
    assert len(built.skeleton) == 2048 and built.content_aspect > 0
    assert len(built.source_sha256) == 64


def test_the_stored_centreline_rebuilds_the_exact_normalised_symbol(cfg):
    n = normalize(render(CIRCLE, "image/svg+xml", cfg).image)
    rebuilt = from_skeleton(pack_skeleton(n), n.content_aspect)
    assert np.array_equal(rebuilt.skeleton, n.skeleton)
    assert np.array_equal(rebuilt.dt, n.dt)
    assert np.array_equal(rebuilt.points, n.points) and rebuilt.content_aspect == n.content_aspect


def test_a_wrong_length_centreline_is_refused():
    with pytest.raises(ValueError):
        from_skeleton(b"\x00" * 10, 1.0)


def test_vectors_round_trip_through_bytes():
    v = np.array(_vector(3, 360), dtype=np.float32)
    assert np.array_equal(unpack_floats(pack_floats(v)), v)
    with pytest.raises(ValueError):
        unpack_floats(b"\x00\x00\x00")


def test_an_embedding_failure_degrades_to_the_local_descriptor_without_raising(cfg):
    def boom(png):
        raise RuntimeError("provider detail that must not escape")

    built = build_descriptor(CIRCLE, "image/svg+xml", cfg, boom)
    assert built.gemini is None and built.gemini_model is None and built.local.shape == (360,)
    assert build_descriptor(CIRCLE, "image/svg+xml", cfg, None).gemini is None


def test_the_embedder_receives_a_png_of_the_render(cfg):
    seen = []
    build_descriptor(CIRCLE, "image/svg+xml", cfg, lambda png: seen.append(png) or {"vector": _vector(), "model": "m"})
    assert seen[0].startswith(b"\x89PNG")


def test_blank_and_unsupported_symbols_are_coverage_gaps(cfg):
    with pytest.raises(RenderError):
        build_descriptor(BLANK, "image/svg+xml", cfg, None)
    with pytest.raises(RenderError):
        build_descriptor(b"%PDF-1.4", "application/pdf", cfg, None)


def test_the_worker_is_off_unless_an_interval_is_set():
    assert not worker.worker_enabled(SimpleNamespace(visual_match_sync_seconds=0.0))
    assert worker.worker_enabled(SimpleNamespace(visual_match_sync_seconds=300.0))


def test_sync_without_a_provider_key_does_nothing(monkeypatch):
    monkeypatch.setattr(worker, "provider_api_key", lambda name: None)
    called = []
    monkeypatch.setattr(worker, "create_database_engine", lambda **kw: called.append(1))
    result = worker.sync_once(SimpleNamespace(db_env_file=None))
    assert result.status == "skipped_no_key" and not called


def test_the_loop_backs_off_while_rows_are_degraded_and_survives_a_failing_cycle(monkeypatch):
    waits, results = [], iter([
        worker.VisualSyncResult(status="ran", built=2, degraded=2),
        RuntimeError("db"),
        worker.VisualSyncResult(status="ran", built=1),
    ])
    stop = asyncio.Event()

    async def fake_wait_for(coro, timeout):
        waits.append(timeout)
        coro.close()
        if len(waits) > 3:
            return True
        raise asyncio.TimeoutError

    def sync(settings):
        item = next(results)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(worker.asyncio, "wait_for", fake_wait_for)
    asyncio.run(worker.run_visual_match_worker(
        SimpleNamespace(visual_match_sync_seconds=300.0), stop, sync=sync, first_delay=5.0))
    assert waits[0] == 5.0 and waits[1] == 600.0 and waits[2] == 1200.0 and waits[3] == 300.0


@pytest.mark.parametrize("seconds,expected", [(300.0, 1), (0.0, 0)])
def test_the_app_starts_and_stops_the_visual_worker_only_with_an_interval(monkeypatch, seconds, expected):
    import dataclasses

    from fastapi.testclient import TestClient
    from symgov_backend import app as app_module

    started = []

    async def fake_worker(settings, stop_event):
        started.append(settings)
        await stop_event.wait()

    monkeypatch.setattr(app_module, "run_visual_match_worker", fake_worker)
    real = app_module.get_settings()
    monkeypatch.setattr(app_module, "get_settings", lambda: dataclasses.replace(real, visual_match_sync_seconds=seconds))
    app = app_module.create_app()
    with TestClient(app):
        assert len(started) == expected
        task = getattr(app.state, "visual_match_task", None)
    assert (task is None) == (expected == 0) and (task is None or task.done())
