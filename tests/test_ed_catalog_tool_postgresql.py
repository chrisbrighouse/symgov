"""Ed's Catalog search tool against the real Catalog search, on PostgreSQL.

The unit tests stub `search_catalog`; this proves the wiring to the real thing:
the public Catalog is searchable, a sentence is cleaned to its keywords,
filters use real facet values, and an organization's organization-wide private
symbols reach only that organization's sessions.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_catalog_browse_search_postgresql import (  # noqa: E402,F401  (fixtures)
    _session_client,
    search_database,
)
from test_wp81_catalog_organization_context_postgresql import _make_organization_wide_symbol  # noqa: E402

from symgov_backend import ed_catalog_tool  # noqa: E402

pytest.importorskip("psycopg")

USER_ID = uuid.uuid4()
SETTINGS = SimpleNamespace(organizations_enabled=True, organization_symbols_enabled=True)


def _run(engine, monkeypatch, *, organization_id=None, **arguments):
    principal = SimpleNamespace(organization_id=organization_id) if organization_id else None
    monkeypatch.setattr(
        ed_catalog_tool, "_resolve_ed_read_authority",
        lambda *args: SimpleNamespace(user=SimpleNamespace(id=USER_ID), principal=principal),
    )
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as session:
        return ed_catalog_tool.search_catalog_for_ed(
            session, object(), SETTINGS, ed_catalog_tool.EdCatalogSearchCall(tool="search_catalog", **arguments)
        )


def _names(result):
    return sorted(item.name for item in result[1:])


def test_a_public_keyword_search_finds_the_published_symbols(search_database, monkeypatch):
    engine, _ = search_database

    result = _run(engine, monkeypatch, query="valve")

    summary = result[0]
    assert _names(result) == ["Gate valve", "Globe valve 100%"]
    assert summary.total_matches == 2 and summary.shown == 2
    assert {item.display_id for item in result[1:]} == {"TST-2", "TST-10"}
    assert all(item.source == "public" for item in result[1:])
    assert summary.facets["disciplines"], "the counts behind each filter come back for narrowing"


def test_a_question_is_cleaned_to_its_keywords(search_database, monkeypatch):
    engine, _ = search_database

    assert _names(_run(engine, monkeypatch, query="What valve symbols are there?")) == [
        "Gate valve", "Globe valve 100%",
    ]
    assert _names(_run(engine, monkeypatch, query="Do you have a centrifugal pump?")) == ["Centrifugal pump"]


def test_a_filter_takes_a_value_from_the_returned_counts(search_database, monkeypatch):
    engine, _ = search_database
    broad = _run(engine, monkeypatch)[0]
    mechanical = next(item.value for item in broad.facets["disciplines"] if item.value.lower().startswith("mech"))

    narrowed = _run(engine, monkeypatch, discipline=mechanical)

    assert "Centrifugal pump" in _names(narrowed)
    assert "Gate valve" not in _names(narrowed)
    assert narrowed[0].filters == {"discipline": mechanical}


def test_nothing_matching_is_a_citable_zero(search_database, monkeypatch):
    engine, _ = search_database

    result = _run(engine, monkeypatch, query="teleporter")

    assert len(result) == 1 and result[0].total_matches == 0
    assert result[0].citation["record_ref"].startswith("live:catalog_search:")


def test_the_limit_bounds_what_is_shown_not_what_is_counted(search_database, monkeypatch):
    engine, _ = search_database

    result = _run(engine, monkeypatch, limit=2)

    assert result[0].shown == 2 and len(result) == 3
    assert result[0].total_matches >= 5


def test_organization_wide_private_symbols_reach_only_their_own_organization(search_database, monkeypatch):
    engine, _ = search_database
    acme = _session_client(
        engine, email="ed-catalog-acme@example.test", code="acme", base_role="admin",
        capabilities=("contributor", "symbol_reviewer"),
    )
    _make_organization_wide_symbol(acme, name="Acme ed strainer")
    other = _session_client(engine, email="ed-catalog-other@example.test", code="other", base_role="admin")
    with engine.connect() as connection:
        ids = {
            code: connection.execute(
                text("SELECT id FROM organizations WHERE normalized_code = :code"), {"code": code}
            ).scalar_one()
            for code in ("acme", "other")
        }
    assert acme is not None and other is not None

    own = _run(engine, monkeypatch, organization_id=ids["acme"], query="acme ed strainer")
    assert _names(own) == ["Acme ed strainer"]
    assert own[1].source == "organization_private"
    assert own[1].display_id

    assert len(_run(engine, monkeypatch, organization_id=ids["other"], query="acme ed strainer")) == 1
    assert len(_run(engine, monkeypatch, query="acme ed strainer")) == 1
