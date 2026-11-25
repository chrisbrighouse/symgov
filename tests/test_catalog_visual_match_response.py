"""The response builder: bands shown, the weak toggle, the no-close-match notice, and served-symbol summaries."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from symgov_backend import catalog_visual_match as service
from symgov_backend.visual_match.retrieval import Group, Member, VisualMatchResult


def _member():
    return Member(uuid.uuid4(), uuid.uuid4(), 1.0, 1.0, 0, 1, 0.9)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, statement, params=None):
        return SimpleNamespace(all=lambda: [r for r in self.rows if r.symbol_revision_id in {str(i) for i in params["ids"]}])


def _row(member, name):
    return SimpleNamespace(
        symbol_id=member.symbol_id, symbol_revision_id=str(member.revision_id), slug=name.lower(), canonical_name=name,
        category="valve", discipline="piping", payload_json={"name": name}, catalog_symbol_id=f"TST-{name}",
    )


def test_weak_groups_are_hidden_unless_asked_and_a_notice_replaces_an_empty_list(monkeypatch):
    a, b = _member(), _member()
    monkeypatch.setattr(service, "catalog_symbol_summary", lambda row: {"displayId": row.catalog_symbol_id})
    session = Rows([_row(a, "A"), _row(b, "B")])
    result = VisualMatchResult([Group(1, "likely", [a]), Group(2, "weak", [b])], False, 2, 2, 1.0)

    shown = service.build_response(session, result, include_weak=False, limit=10)
    assert [g["band"] for g in shown["groups"]] == ["likely"] and "notice" not in shown
    assert shown["groups"][0]["symbols"] == [{"displayId": "TST-A", "source": "public"}]

    everything = service.build_response(session, result, include_weak=True, limit=10)
    assert [g["band"] for g in everything["groups"]] == ["likely", "weak"]

    only_weak = VisualMatchResult([Group(1, "weak", [b])], False, 1, 2, 1.0)
    empty = service.build_response(session, only_weak, include_weak=False, limit=10)
    assert empty["groups"] == [] and empty["notice"] == service.NO_CLOSE_MATCH
    assert set(empty) >= {"matcherVersion", "degraded", "warnings", "strongerMatchOutsideFilters", "symbolsSearched"}


def test_a_symbol_that_stopped_being_published_is_dropped_from_its_group(monkeypatch):
    a = _member()
    monkeypatch.setattr(service, "catalog_symbol_summary", lambda row: {})
    result = VisualMatchResult([Group(1, "likely", [a])], False, 1, 1, 1.0)
    response = service.build_response(Rows([]), result, include_weak=False, limit=10)
    assert response["groups"] == [] and response["notice"]


def test_default_embed_records_each_query_call_in_the_usage_ledger(monkeypatch):
    built, calls = [], []

    def fake_factory(**kwargs):
        built.append(kwargs)
        return "factory"

    def fake_request(**kwargs):
        calls.append(kwargs)
        return {"vector": [1.0]}

    monkeypatch.setattr(service, "create_session_factory", fake_factory)
    monkeypatch.setattr(service, "request_image_embedding", fake_request)
    settings = SimpleNamespace(visual_match_embedding_model="google/gemini-embedding-2", db_env_file="/env")
    embed = service.default_embed(settings)
    assert built == []  # nothing is built until a call is made
    for _ in range(4):
        embed(b"png")
    assert [c["session_factory_provider"]() for c in calls] == ["factory"] * 4
    assert built == [{"env_file": "/env", "nopool": True}]  # one factory shared by the match's calls
    assert {c["initiator_kind"] for c in calls} == {"user"}
