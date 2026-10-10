"""The embedding index's pure parts: what is embedded, storage, and scoring."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from symgov_backend import catalog_embeddings as module


@pytest.fixture(autouse=True)
def _fresh_cache():
    module.clear_index_cache()
    yield
    module.clear_index_cache()


def test_the_embedded_text_is_descriptive_and_carries_no_identifiers():
    body = module.embedding_text(
        name="Check valve",
        category="valve",
        discipline="piping",
        payload={
            "summary": "A valve that stops backflow.",
            "keywords": ["non-return", "backflow", 7, None],
            "downloads": [{"object_key": "symbols/secret-key.svg", "filename": "x.svg"}],
            "id": "S-000042",
        },
        pack_title="Alpha Pack",
        facets={"categories": ["Valves"], "disciplines": ["Piping"], "use_cases": ["P&ID"]},
    )

    assert body == (
        "Symbol: Check valve. Description: A valve that stops backflow. Category: Valves. "
        "Discipline: Piping. Used for: P&ID. Keywords: non-return, backflow. Pack: Alpha Pack."
    )
    assert "secret-key" not in body and "S-000042" not in body


def test_the_text_falls_back_to_the_symbols_own_category_and_discipline():
    body = module.embedding_text(
        name="Pump", category="pump", discipline="mechanical", payload=None, pack_title=None
    )

    assert body == "Symbol: Pump. Category: pump. Discipline: mechanical."


def test_long_fields_and_whitespace_are_bounded():
    body = module.embedding_text(
        name="  Gate \n valve ", category="", discipline="", payload={"summary": "word " * 500}, pack_title=None
    )

    assert body.startswith("Symbol: Gate valve.")
    assert len(body) < 700


def test_the_hash_names_the_exact_text():
    assert module.content_hash("a") == module.content_hash("a")
    assert module.content_hash("a") != module.content_hash("a ")
    assert len(module.content_hash("a")) == 64


def test_vectors_round_trip_through_their_stored_bytes():
    vector = (0.5, -0.25, 0.125)

    assert module.unpack_vector(module.pack_vector(vector), 3) == vector
    with pytest.raises(ValueError):
        module.unpack_vector(module.pack_vector(vector), 4)


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def one(self):
        return self.rows[0]

    def all(self):
        return self.rows


class _IndexSession:
    """Answers the two queries `_load_index` makes."""

    def __init__(self, vectors, *, latest="t1"):
        self.vectors = vectors
        self.latest = latest
        self.loads = 0

    def execute(self, statement, params=None):
        sql = str(statement)
        if "count(*)" in sql:
            return _Rows([SimpleNamespace(n=len(self.vectors), latest=self.latest)])
        self.loads += 1
        return _Rows(
            [
                SimpleNamespace(
                    symbol_revision_id=revision, governed_symbol_id=symbol,
                    dimensions=len(vector), embedding=module.pack_vector(vector),
                )
                for revision, symbol, vector in self.vectors
            ]
        )


def _entry(vector):
    return uuid.uuid4(), uuid.uuid4(), vector


def _hits(session, query, **kwargs):
    return module.semantic_candidates(session, query, model="m", min_similarity=kwargs.pop("min_similarity", 0.0), **kwargs)


def test_candidates_are_ranked_by_cosine_and_cut_at_the_floor():
    near, mid, far = _entry((1.0, 0.0)), _entry((0.8, 0.6)), _entry((0.0, 1.0))
    session = _IndexSession([far, near, mid])

    hits = _hits(session, (1.0, 0.0), min_similarity=0.5)

    assert [hit.symbol_revision_id for hit in hits] == [near[0], mid[0]]
    assert [hit.similarity for hit in hits] == [1.0, 0.8]
    assert [hit.governed_symbol_id for hit in hits] == [near[1], mid[1]]


def test_top_k_bounds_the_candidates():
    session = _IndexSession([_entry((1.0, 0.0)) for _ in range(10)])

    assert len(_hits(session, (1.0, 0.0), top_k=3)) == 3


def test_an_empty_index_or_a_query_of_the_wrong_size_finds_nothing():
    assert _hits(_IndexSession([]), (1.0, 0.0)) == []
    assert _hits(_IndexSession([_entry((1.0, 0.0))]), (1.0, 0.0, 0.0)) == []


def test_the_index_is_reloaded_only_when_it_changes():
    session = _IndexSession([_entry((1.0, 0.0))])

    _hits(session, (1.0, 0.0))
    _hits(session, (1.0, 0.0))
    assert session.loads == 1

    session.vectors.append(_entry((0.0, 1.0)))
    assert len(_hits(session, (0.0, 1.0))) >= 1
    assert session.loads == 2

    session.latest = "t2"
    _hits(session, (1.0, 0.0))
    assert session.loads == 3


def test_scoring_works_without_numpy(monkeypatch):
    monkeypatch.setattr(module, "_np", None)
    near, far = _entry((1.0, 0.0)), _entry((0.0, 1.0))

    hits = _hits(_IndexSession([far, near]), (1.0, 0.0), min_similarity=0.5)

    assert [hit.symbol_revision_id for hit in hits] == [near[0]]


def test_vectors_of_another_size_in_the_same_model_are_ignored():
    good = _entry((1.0, 0.0))
    odd = (uuid.uuid4(), uuid.uuid4(), (1.0, 0.0, 0.0))
    session = _IndexSession([good, odd])

    assert [hit.symbol_revision_id for hit in _hits(session, (1.0, 0.0))] == [good[0]]
