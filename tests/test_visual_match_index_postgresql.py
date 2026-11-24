"""The visual descriptor index on a real PostgreSQL: coverage, freshness, degradation, pruning."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_catalog_browse_search_postgresql import search_database  # noqa: E402,F401  (fixture)

from symgov_backend.visual_match.index import index_status, index_visual_descriptors  # noqa: E402
from symgov_backend.visual_match.rasterizer import RenderConfig, vendored_font_dir  # noqa: E402

pytest.importorskip("psycopg")

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="30" fill="none" stroke="black" stroke-width="3"/></svg>'


def _cfg(tmp_path, fonts="fs1"):
    return RenderConfig(font_dirs=(vendored_font_dir(),), resources_dir=str(tmp_path), font_set_id=fonts)


def _embed(png):
    v = np.random.default_rng(len(png)).normal(size=768)
    return {"vector": list(v / np.linalg.norm(v)), "model": "google/gemini-embedding-2"}


@pytest.fixture
def session(search_database):
    engine, _ = search_database
    with sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)() as opened:
        yield opened


@pytest.fixture(autouse=True)
def _empty_index(search_database):
    # The database is shared by the module, so every test starts from an empty index.
    engine, _ = search_database
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM catalog_symbol_visual_descriptors"))


@pytest.fixture
def with_assets(search_database):
    """Give every public symbol but one an SVG preview source; the last has none."""
    engine, seeded = search_database
    names = list(seeded)
    with engine.begin() as connection:
        for index, name in enumerate(names[:-1]):
            connection.execute(
                text("UPDATE symbol_revisions SET payload_json = payload_json || CAST(:p AS jsonb) WHERE id = :r"),
                {"p": '{"source_object_key": "k/%d.svg", "source_content_type": "image/svg+xml"}' % index,
                 "r": seeded[name]["revision_id"]},
            )
    return engine, seeded, len(names) - 1


def _rows(engine, where=""):
    with engine.connect() as c:
        return c.execute(text("SELECT count(*) FROM catalog_symbol_visual_descriptors " + where)).scalar_one()


def test_dry_run_writes_nothing_apply_builds_once_and_reports_the_gap(with_assets, session, tmp_path):
    engine, seeded, buildable = with_assets
    fetched = []
    fetch = lambda key: fetched.append(key) or SVG  # noqa: E731
    cfg = _cfg(tmp_path)

    dry = index_visual_descriptors(session, cfg=cfg, fetch=fetch, embed=_embed)
    assert dry.published == len(seeded) and dry.to_build == buildable and not fetched and _rows(engine) == 0
    assert dry.gaps == {"no_preview_asset": 1}

    applied = index_visual_descriptors(session, cfg=cfg, fetch=fetch, embed=_embed, apply=True)
    assert applied.built == buildable and applied.degraded == 0 and _rows(engine) == buildable

    fetched.clear()
    again = index_visual_descriptors(session, cfg=cfg, fetch=fetch, embed=_embed, apply=True)
    assert again.built == 0 and again.already_current == buildable and not fetched   # never refetches a current symbol
    status = index_status(session, cfg=cfg)
    assert status["indexed"] == buildable and status["withEmbedding"] == buildable and status["needsBuild"] == 0


def test_an_embedding_outage_writes_degraded_rows_and_the_next_pass_completes_them(with_assets, session, tmp_path):
    engine, _, buildable = with_assets
    cfg = _cfg(tmp_path)

    def down(png):
        raise RuntimeError("provider down")

    first = index_visual_descriptors(session, cfg=cfg, fetch=lambda k: SVG, embed=down, apply=True)
    assert first.built == buildable and first.degraded == buildable
    assert _rows(engine, "WHERE gemini_embedding IS NULL") == buildable
    assert index_status(session, cfg=cfg)["withEmbedding"] == 0

    second = index_visual_descriptors(session, cfg=cfg, fetch=lambda k: SVG, embed=_embed, apply=True)
    assert second.built == buildable and second.degraded == 0
    assert _rows(engine, "WHERE gemini_embedding IS NOT NULL") == buildable


def test_a_new_font_set_or_a_changed_asset_rebuilds_and_a_departed_symbol_is_pruned(with_assets, session, tmp_path):
    engine, seeded, buildable = with_assets
    index_visual_descriptors(session, cfg=_cfg(tmp_path), fetch=lambda k: SVG, embed=_embed, apply=True)

    rebuilt = index_visual_descriptors(session, cfg=_cfg(tmp_path, "fs2"), fetch=lambda k: SVG, embed=_embed, apply=True)
    assert rebuilt.built == buildable and rebuilt.already_current == 0

    revision = seeded[list(seeded)[0]]["revision_id"]
    with engine.begin() as c:
        c.execute(text("UPDATE symbol_revisions SET lifecycle_state = 'withdrawn' WHERE id = :r"), {"r": revision})
    try:
        pruned = index_visual_descriptors(session, cfg=_cfg(tmp_path, "fs2"), fetch=lambda k: SVG, embed=_embed, apply=True)
        assert pruned.pruned == 1 and _rows(engine) == buildable - 1
    finally:
        with engine.begin() as c:
            c.execute(text("UPDATE symbol_revisions SET lifecycle_state = 'published' WHERE id = :r"), {"r": revision})


def test_an_unrenderable_file_is_a_gap_and_an_unreadable_object_is_retried(with_assets, session, tmp_path):
    engine, _, buildable = with_assets
    cfg = _cfg(tmp_path)
    report = index_visual_descriptors(
        session, cfg=cfg, fetch=lambda k: b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 9"/>', embed=_embed, apply=True)
    assert report.built == 0 and report.gaps.get("render_failed") == buildable and _rows(engine) == 0

    def unreadable(key):
        raise OSError("storage down")

    report = index_visual_descriptors(session, cfg=cfg, fetch=unreadable, embed=_embed, apply=True)
    assert report.built == 0 and _rows(engine) == 0 and report.pending == buildable
