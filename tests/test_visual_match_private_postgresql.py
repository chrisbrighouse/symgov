"""Visual match over organization-private symbols (D5; P1-P6 decided 2026-11-25), on a real PostgreSQL.

Two organizations each publish one organization-wide private symbol through the real review flow. The
index holds both; a query only ever reaches the caller's own organization's symbol, by the Catalog's rule,
and personal sessions and the API-key path never reach either.
"""
from __future__ import annotations

import base64
import dataclasses
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_catalog_browse_search_postgresql import _session_client, search_database  # noqa: E402,F401  (fixture)
from test_visual_match_retrieval_postgresql import SHAPES, _cfg, _query_png, _svg, fake_embed  # noqa: E402
from test_wp81_catalog_organization_context_postgresql import _make_organization_wide_symbol  # noqa: E402

from symgov_backend import catalog_visual_match  # noqa: E402
from symgov_backend.settings import get_settings  # noqa: E402
from symgov_backend.visual_match import retrieval  # noqa: E402
from symgov_backend.visual_match.index import index_status, index_visual_descriptors  # noqa: E402

pytest.importorskip("psycopg")

ACME_SHAPE = _svg('<circle cx="30" cy="30" r="18"/><rect x="50" y="50" width="35" height="35"/><line x1="30" y1="48" x2="50" y2="67"/>')
BETA_SHAPE = _svg('<polyline points="10,90 30,20 50,90 70,20 90,90"/><circle cx="50" cy="10" r="6"/>')


@pytest.fixture(scope="module")
def world(search_database):
    engine, seeded = search_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme = _session_client(engine, email="vm-acme@example.test", code="vmacme", base_role="admin",
                           capabilities=("contributor", "symbol_reviewer"))
    beta = _session_client(engine, email="vm-beta@example.test", code="vmbeta", base_role="admin",
                           capabilities=("contributor", "symbol_reviewer"))
    personal = _session_client(engine, email="vm-personal@example.test")
    acme_symbol = _make_organization_wide_symbol(acme, name="Acme visual strainer")
    beta_symbol = _make_organization_wide_symbol(beta, name="Beta visual zigzag")
    shapes = {}
    with engine.begin() as c:
        for i, name in enumerate(list(seeded)[: len(SHAPES)]):
            shapes[f"k/{i}.svg"] = SHAPES[i]
            c.execute(text("UPDATE symbol_revisions SET payload_json = payload_json || CAST(:p AS jsonb) WHERE id = :r"),
                      {"p": '{"source_object_key": "k/%d.svg", "source_content_type": "image/svg+xml"}' % i,
                       "r": seeded[name]["revision_id"]})
        owners = {}
        for key, symbol_id, shape in (("acme", acme_symbol, ACME_SHAPE), ("beta", beta_symbol, BETA_SHAPE)):
            shapes[f"p/{key}.svg"] = shape
            row = c.execute(text("SELECT current_revision_id, owner_organization_id FROM governed_symbols WHERE id = :s"),
                            {"s": symbol_id}).one()
            owners[key] = {"symbol": symbol_id, "revision": str(row.current_revision_id), "org": row.owner_organization_id}
            c.execute(text("UPDATE symbol_revisions SET payload_json = payload_json || CAST(:p AS jsonb) WHERE id = :r"),
                      {"p": '{"source_object_key": "p/%s.svg", "source_content_type": "image/svg+xml"}' % key,
                       "r": row.current_revision_id})
    return {"engine": engine, "Session": Session, "seeded": seeded, "shapes": shapes, "owners": owners,
            "clients": {"acme": acme, "beta": beta, "personal": personal}}


@pytest.fixture
def built(world, tmp_path):
    with world["engine"].begin() as c:
        c.execute(text("DELETE FROM catalog_symbol_visual_descriptors"))
    retrieval.clear_index_cache()
    with world["Session"]() as s:
        report = index_visual_descriptors(s, cfg=_cfg(tmp_path), fetch=lambda k: world["shapes"][k], embed=fake_embed, apply=True)
    retrieval.clear_index_cache()
    return report


def _returned(result):
    return {str(m.revision_id) for g in result.groups for m in g.members}


def test_the_index_holds_each_private_symbol_under_its_owner(world, built, tmp_path):
    assert built.private == 2 and built.published >= len(SHAPES)
    with world["engine"].connect() as c:
        rows = {str(r.symbol_revision_id): r for r in c.execute(text(
            "SELECT symbol_revision_id, scope, owner_organization_id FROM catalog_symbol_visual_descriptors "
            "WHERE scope = 'organization_private'"))}
    for owner in world["owners"].values():
        assert rows[owner["revision"]].owner_organization_id == owner["org"]
    with world["Session"]() as s:
        status = index_status(s, cfg=_cfg(tmp_path))
    assert status["private"] == 2 and status["indexedPrivate"] == 2 and status["needsBuild"] == 0


def test_an_organization_finds_its_own_private_symbol_and_never_anothers(world, built, tmp_path):
    acme, beta = world["owners"]["acme"], world["owners"]["beta"]
    with world["Session"]() as s:
        own = retrieval.visual_match(s, image=_query_png(ACME_SHAPE, tmp_path), embed=fake_embed, organization_id=acme["org"])
        top = own.groups[0].members[0]
        assert str(top.revision_id) == acme["revision"] and top.private
        # Beta's exact drawing, asked by Acme: Beta's symbol is not in the pool at all.
        other = retrieval.visual_match(s, image=_query_png(BETA_SHAPE, tmp_path), embed=fake_embed, organization_id=acme["org"])
        assert beta["revision"] not in _returned(other)
        assert own.eligible == other.eligible == len(SHAPES) + 1
        # No organization (personal session, API key): nothing private at all.
        public = retrieval.visual_match(s, image=_query_png(ACME_SHAPE, tmp_path), embed=fake_embed)
        assert not ({acme["revision"], beta["revision"]} & _returned(public))
        assert not any(m.private for g in public.groups for m in g.members)


def test_the_shared_cache_never_carries_private_rows(world, built, tmp_path):
    acme = world["owners"]["acme"]
    with world["Session"]() as s:
        retrieval.visual_match(s, image=_query_png(ACME_SHAPE, tmp_path), embed=fake_embed, organization_id=acme["org"])
        cached = retrieval._load_index(s, retrieval.MATCHER_VERSION)
    assert cached.private is None
    assert acme["revision"] not in {str(r) for r in cached.revision_ids}


def test_an_unindexed_private_symbol_is_a_warning_not_an_outage(world, built, tmp_path):
    acme = world["owners"]["acme"]
    with world["engine"].begin() as c:
        c.execute(text("DELETE FROM catalog_symbol_visual_descriptors WHERE symbol_revision_id = CAST(:r AS uuid)"),
                  {"r": acme["revision"]})
    with world["Session"]() as s:
        result = retrieval.visual_match(s, image=_query_png(ACME_SHAPE, tmp_path), embed=fake_embed, organization_id=acme["org"])
    assert result.private_pending == 1 and "private_symbols_pending" in result.warnings
    assert acme["revision"] not in _returned(result)


def test_a_symbol_no_longer_organization_wide_is_dropped_at_once_and_pruned_next_pass(world, built, tmp_path):
    acme = world["owners"]["acme"]
    with world["engine"].begin() as c:
        c.execute(text("UPDATE governed_symbols SET organization_wide = false WHERE id = CAST(:s AS uuid)"), {"s": acme["symbol"]})
    try:
        with world["Session"]() as s:
            result = retrieval.visual_match(s, image=_query_png(ACME_SHAPE, tmp_path), embed=fake_embed, organization_id=acme["org"])
            assert acme["revision"] not in _returned(result)
            report = index_visual_descriptors(s, cfg=_cfg(tmp_path), fetch=lambda k: world["shapes"][k], embed=fake_embed, apply=True)
        assert report.pruned == 1 and report.private == 1
    finally:
        with world["engine"].begin() as c:
            c.execute(text("UPDATE governed_symbols SET organization_wide = true WHERE id = CAST(:s AS uuid)"), {"s": acme["symbol"]})


def test_a_row_whose_scope_changed_is_rescoped_without_refetching(world, built, tmp_path):
    """Promotion and demotion can carry a revision across scopes; the row follows without a new embedding."""
    acme = world["owners"]["acme"]
    with world["engine"].begin() as c:
        c.execute(text("UPDATE catalog_symbol_visual_descriptors SET scope = 'public', owner_organization_id = NULL "
                       "WHERE symbol_revision_id = CAST(:r AS uuid)"), {"r": acme["revision"]})

    def no_fetch(key):
        raise AssertionError("a rescope must not refetch")

    with world["Session"]() as s:
        report = index_visual_descriptors(s, cfg=_cfg(tmp_path), fetch=no_fetch, embed=fake_embed, apply=True)
    assert report.rescoped == 1 and report.built == 0
    with world["engine"].connect() as c:
        row = c.execute(text("SELECT scope, owner_organization_id FROM catalog_symbol_visual_descriptors "
                             "WHERE symbol_revision_id = CAST(:r AS uuid)"), {"r": acme["revision"]}).one()
    assert row.scope == "organization_private" and row.owner_organization_id == acme["org"]


def test_a_filter_reaches_a_private_symbol_that_had_no_labels_yet(world, built, tmp_path):
    acme = world["owners"]["acme"]
    with world["engine"].begin() as c:
        c.execute(text("DELETE FROM catalog_symbol_facets WHERE symbol_revision_id = CAST(:r AS uuid)"), {"r": acme["revision"]})
    with world["Session"]() as s:
        everything = retrieval.visual_match(s, image=_query_png(ACME_SHAPE, tmp_path), embed=fake_embed, organization_id=acme["org"])
        assert acme["revision"] in _returned(everything)
        with world["engine"].connect() as c:
            labels = c.execute(text("SELECT disciplines FROM catalog_symbol_facets WHERE symbol_revision_id = CAST(:r AS uuid)"),
                               {"r": acme["revision"]}).scalar_one_or_none()
        assert labels is None   # unfiltered queries do not write
        filtered = retrieval.visual_match(s, image=_query_png(ACME_SHAPE, tmp_path), embed=fake_embed,
                                          organization_id=acme["org"], filters={"catalogDisciplines": ["Civil / Structural"]})
    assert acme["revision"] in _returned(filtered)


def _match(client, monkeypatch, shape, tmp_path):
    monkeypatch.setattr(catalog_visual_match, "default_embed", lambda settings: fake_embed)
    real = client.app.dependency_overrides[get_settings]()
    client.app.dependency_overrides[get_settings] = lambda: dataclasses.replace(real, visual_match_enabled=True)
    try:
        body = {"imageBase64": base64.b64encode(_query_png(shape, tmp_path)).decode(), "includeWeak": True}
        response = client.post("/api/v1/published/symbols/visual-match", json=body)
    finally:
        client.app.dependency_overrides[get_settings] = lambda: real
    assert response.status_code == 200, response.text
    return response.json()


def test_the_in_app_route_serves_the_callers_own_private_symbol_badged_with_its_session_preview(world, built, monkeypatch, tmp_path):
    acme = world["owners"]["acme"]
    body = _match(world["clients"]["acme"], monkeypatch, ACME_SHAPE, tmp_path)
    top = body["groups"][0]["symbols"][0]
    assert top["symbolId"] == acme["symbol"] and top["source"] == "organization_private"
    assert top["catalogSymbolId"] is None and top["displayId"]
    assert top["previewUrl"] == f"/api/v1/published/symbols/{acme['symbol']}/preview"
    assert "Civil / Structural" in top["catalogDisciplines"]
    others = [s for g in body["groups"] for s in g["symbols"] if s["symbolId"] != acme["symbol"]]
    assert others and all(s["source"] == "public" for s in others)

    for who in ("beta", "personal"):
        seen = {s["symbolId"] for g in _match(world["clients"][who], monkeypatch, ACME_SHAPE, tmp_path)["groups"] for s in g["symbols"]}
        assert acme["symbol"] not in seen, who


def test_the_preview_link_a_private_result_carries_opens_for_its_own_organization_only(world, built):
    acme = world["owners"]["acme"]
    url = f"/api/v1/published/symbols/{acme['symbol']}/preview"
    assert world["clients"]["beta"].get(url).status_code == 404
    assert world["clients"]["personal"].get(url).status_code == 404


def test_the_response_builder_never_summarises_a_private_member_for_another_organization(world, built):
    acme, beta = world["owners"]["acme"], world["owners"]["beta"]
    member = retrieval.Member(uuid.UUID(acme["revision"]), uuid.UUID(acme["symbol"]), 1.0, 0.0, 0, 1, 1.0, private=True)
    result = retrieval.VisualMatchResult([retrieval.Group(1, "likely", [member])], False, 1, 1, 1.0)
    with world["Session"]() as s:
        for org in (beta["org"], None):
            response = catalog_visual_match.build_response(s, result, include_weak=True, limit=10, organization_id=org)
            assert response["groups"] == [], org
        own = catalog_visual_match.build_response(s, result, include_weak=True, limit=10, organization_id=acme["org"])
    assert own["groups"][0]["symbols"][0]["symbolId"] == acme["symbol"]
