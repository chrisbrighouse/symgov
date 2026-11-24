"""Visual match retrieval on a real PostgreSQL: visibility, filters, rotation, degradation, 503."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_catalog_browse_search_postgresql import search_database  # noqa: E402,F401  (fixture)

from symgov_backend.visual_match import retrieval  # noqa: E402
from symgov_backend.visual_match.descriptor import descriptor  # noqa: E402
from symgov_backend.visual_match.errors import VisualMatchInputError, VisualMatchUnavailable  # noqa: E402
from symgov_backend.visual_match.imaging import decode_gray, normalize  # noqa: E402
from symgov_backend.visual_match.index import index_visual_descriptors  # noqa: E402
from symgov_backend.visual_match.rasterizer import RenderConfig, render, vendored_font_dir  # noqa: E402

pytest.importorskip("psycopg")


def _svg(body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" fill="none" stroke="black" stroke-width="3">'
            + body + "</svg>").encode()


# Distinct, asymmetric line drawings, one per seeded symbol (the last symbol has no preview asset).
SHAPES = [
    _svg('<circle cx="50" cy="50" r="30"/><line x1="50" y1="50" x2="85" y2="20"/>'),
    _svg('<rect x="20" y="25" width="60" height="45"/><line x1="20" y1="25" x2="50" y2="5"/>'),
    _svg('<polygon points="15,85 85,85 40,20"/><line x1="40" y1="20" x2="40" y2="5"/>'),
    _svg('<path d="M20 20 L20 80 L75 80 M20 50 L55 50"/><circle cx="75" cy="30" r="8"/>'),
]


def _cfg(tmp_path):
    return RenderConfig(font_dirs=(vendored_font_dir(),), resources_dir=str(tmp_path), font_set_id="fs1")


def fake_embed(png):
    """A deterministic stand-in for the provider: a padded, unit-length image descriptor."""
    n = normalize(decode_gray(png))
    v = np.zeros(768, np.float32)
    v[:360] = descriptor(n)
    return {"vector": list(v / np.linalg.norm(v)), "model": "fake/image"}


def _query_png(svg, tmp_path, turns=0):
    gray = render(svg, "image/svg+xml", _cfg(tmp_path)).image
    out = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(np.rot90(gray, turns))).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def session(search_database):
    engine, _ = search_database
    with sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)() as opened:
        yield opened


@pytest.fixture
def built(search_database, session, tmp_path):
    engine, seeded = search_database
    names = list(seeded)
    shape_by_key = {}
    with engine.begin() as c:
        c.execute(text("DELETE FROM catalog_symbol_visual_descriptors"))
        for i, name in enumerate(names[: len(SHAPES)]):
            shape_by_key[f"k/{i}.svg"] = SHAPES[i]
            c.execute(
                text("UPDATE symbol_revisions SET payload_json = payload_json || CAST(:p AS jsonb) WHERE id = :r"),
                {"p": '{"source_object_key": "k/%d.svg", "source_content_type": "image/svg+xml"}' % i,
                 "r": seeded[name]["revision_id"]})
    retrieval.clear_index_cache()
    index_visual_descriptors(session, cfg=_cfg(tmp_path), fetch=lambda k: shape_by_key[k], embed=fake_embed, apply=True)
    retrieval.clear_index_cache()
    return engine, seeded, names


def _revision(seeded, name):
    return str(seeded[name]["revision_id"])


def test_the_drawn_symbol_comes_first_and_an_exact_look_is_likely(built, session, tmp_path):
    _, seeded, names = built
    result = retrieval.visual_match(session, image=_query_png(SHAPES[2], tmp_path), embed=fake_embed)
    assert str(result.groups[0].members[0].revision_id) == _revision(seeded, names[2])
    assert result.groups[0].band == "likely" and not result.degraded
    assert all(g.band in {"likely", "possible", "weak"} for g in result.groups)


def test_a_quarter_turned_query_still_finds_the_symbol(built, session, tmp_path):
    _, seeded, names = built
    result = retrieval.visual_match(session, image=_query_png(SHAPES[3], tmp_path, turns=1), embed=fake_embed)
    assert str(result.groups[0].members[0].revision_id) == _revision(seeded, names[3])


def test_an_embedding_outage_degrades_to_the_local_descriptor_and_never_says_likely(built, session, tmp_path):
    _, seeded, names = built

    def down(png):
        raise RuntimeError("provider detail")

    result = retrieval.visual_match(session, image=_query_png(SHAPES[0], tmp_path), embed=down)
    assert result.degraded and "degraded" in result.warnings
    assert str(result.groups[0].members[0].revision_id) == _revision(seeded, names[0])
    assert result.groups[0].band != "likely"


def test_a_withdrawn_symbol_is_never_returned_even_though_its_row_remains(built, session, tmp_path):
    engine, seeded, names = built
    target = _revision(seeded, names[1])
    with engine.begin() as c:
        c.execute(text("UPDATE symbol_revisions SET lifecycle_state = 'withdrawn' WHERE id = :r"), {"r": target})
    try:
        retrieval.clear_index_cache()
        result = retrieval.visual_match(session, image=_query_png(SHAPES[1], tmp_path), embed=fake_embed, min_coverage=0.5)
        assert target not in {str(m.revision_id) for g in result.groups for m in g.members}
    finally:
        with engine.begin() as c:
            c.execute(text("UPDATE symbol_revisions SET lifecycle_state = 'published' WHERE id = :r"), {"r": target})
        retrieval.clear_index_cache()


def test_filters_narrow_before_ranking_and_flag_a_stronger_match_outside(built, session, tmp_path):
    engine, seeded, names = built
    disciplines = {names[0]: "Piping", names[1]: "Piping", names[2]: "Mechanical", names[3]: "Piping"}
    with engine.begin() as c:
        c.execute(text("DELETE FROM catalog_symbol_facets WHERE symbol_revision_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": [_revision(seeded, n) for n in disciplines]})
        for name, discipline in disciplines.items():
            c.execute(text(
                "INSERT INTO catalog_symbol_facets (symbol_revision_id, governed_symbol_id, rules_version, "
                "revision_generation, symbol_generation, display_id, display_name, id_sort_key, name_sort_key, "
                "search_text, symbol_family, disciplines, categories, formats, use_cases, computed_at) VALUES "
                "(CAST(:r AS uuid), CAST(:s AS uuid), 1, 0, 0, :n, :n, :n, :n, :n, 'x', CAST(:d AS jsonb), "
                "'[]'::jsonb, '[]'::jsonb, '[]'::jsonb, now())"),
                {"r": _revision(seeded, name), "s": str(seeded[name]["symbol_id"]), "n": name,
                 "d": '["%s"]' % discipline})
    try:
        piping = {"catalogDisciplines": ["Piping"]}
        result = retrieval.visual_match(session, image=_query_png(SHAPES[2], tmp_path), embed=fake_embed, filters=piping)
        returned = {str(m.revision_id) for g in result.groups for m in g.members}
        assert _revision(seeded, names[2]) not in returned and returned
        assert result.eligible == 3 and result.stronger_match_outside_filters
        unfiltered = retrieval.visual_match(session, image=_query_png(SHAPES[2], tmp_path), embed=fake_embed)
        assert not unfiltered.stronger_match_outside_filters and unfiltered.eligible == 4
        nothing = retrieval.visual_match(session, image=_query_png(SHAPES[2], tmp_path), embed=fake_embed,
                                         filters={"catalogDisciplines": ["Fire"]})
        assert nothing.groups == [] and nothing.eligible == 0
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM catalog_symbol_facets WHERE symbol_revision_id = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": [_revision(seeded, n) for n in disciplines]})


def test_bad_input_is_a_4xx_style_error_and_an_empty_index_is_unavailable(built, session, tmp_path):
    with pytest.raises(VisualMatchInputError) as caught:
        retrieval.visual_match(session, image=b"not an image", embed=fake_embed)
    assert caught.value.code == "unreadable_image"
    blank = io.BytesIO()
    Image.new("L", (64, 64), 255).save(blank, format="PNG")
    with pytest.raises(VisualMatchInputError) as caught:
        retrieval.visual_match(session, image=blank.getvalue(), embed=fake_embed)
    assert caught.value.code == "no_line_work"
    with pytest.raises(VisualMatchInputError):
        retrieval.visual_match(session, image=_query_png(SHAPES[0], tmp_path), embed=fake_embed, filters={"bogus": ["x"]})

    engine, _, _ = built
    with engine.begin() as c:
        c.execute(text("DELETE FROM catalog_symbol_visual_descriptors"))
    retrieval.clear_index_cache()
    with pytest.raises(VisualMatchUnavailable) as caught:
        retrieval.visual_match(session, image=_query_png(SHAPES[0], tmp_path), embed=fake_embed)
    assert caught.value.code == "index_empty"
