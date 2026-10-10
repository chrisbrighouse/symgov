"""The embedding index and Ed's meaning-based search, on a real PostgreSQL.

A deterministic fake embedder (hashed bag of words) stands in for the provider,
so this proves the SQL, the index maintenance and the visibility rules rather
than model quality:

- only published public revisions are indexed, once, and again only when their
  text changes; a revision that leaves the Catalog is pruned;
- a vector never makes a symbol visible: the live public-Catalog rule decides,
  at query time, whatever the index holds;
- stored filter labels narrow similar matches exactly as the Catalog search does;
- Ed's tool adds "similar" matches after the keyword ones, never for a private
  symbol, and degrades to the keyword result when embedding fails.
"""

from __future__ import annotations

import math
import re
import sys
import zlib
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

from symgov_backend import catalog_embeddings as embeddings  # noqa: E402
from symgov_backend import ed_catalog_tool  # noqa: E402
from symgov_backend.services.llm_embeddings import EmbeddingError  # noqa: E402

pytest.importorskip("psycopg")

MODEL = "fake/bag-of-words"
# Wide enough that distinct words never share a slot, so only real overlap scores.
DIMENSIONS = 4096
FLOOR = 0.10


def fake_vector(value: str) -> list[float]:
    counts = [0.0] * DIMENSIONS
    for word in re.findall(r"[a-z]+", value.lower()):
        counts[zlib.crc32(word.encode()) % DIMENSIONS] += 1.0
    length = math.sqrt(sum(count * count for count in counts)) or 1.0
    return [count / length for count in counts]


def fake_embed(texts):
    return {
        "vectors": [fake_vector(item) for item in texts],
        "dimensions": DIMENSIONS,
        "usage": {"prompt_tokens": sum(len(item.split()) for item in texts)},
    }


def _settings(**overrides):
    values = dict(
        organizations_enabled=True, organization_symbols_enabled=True, ed_semantic_search_enabled=True,
        catalog_embedding_model=MODEL, ed_semantic_min_similarity=FLOOR,
        auth_login_hash_secret="test-secret-not-real-0123456789", db_env_file=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def session(search_database):
    engine, _ = search_database
    with sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)() as opened:
        yield opened


def _stored(engine) -> int:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT count(*) FROM catalog_symbol_embeddings WHERE model = :m"), {"m": MODEL}
        ).scalar_one()


def _index(session, **kwargs):
    embeddings.clear_index_cache()
    return embeddings.index_public_symbols(session, model=MODEL, embed=fake_embed, **kwargs)


def _run(session, monkeypatch, *, organization_id=None, settings=None, embed=None, **arguments):
    principal = SimpleNamespace(organization_id=organization_id) if organization_id else None
    monkeypatch.setattr(
        ed_catalog_tool, "_resolve_ed_read_authority",
        lambda *args: SimpleNamespace(user=SimpleNamespace(id="22222222-2222-2222-2222-222222222222"), principal=principal),
    )
    monkeypatch.setattr(
        ed_catalog_tool, "request_llm_embeddings", embed or (lambda **kwargs: fake_embed(kwargs["texts"]))
    )
    return ed_catalog_tool.search_catalog_for_ed(
        session, object(), settings or _settings(),
        ed_catalog_tool.EdCatalogSearchCall(tool="search_catalog", **arguments),
    )


def test_a_dry_run_reports_and_writes_nothing_then_apply_embeds_each_public_symbol_once(search_database, session):
    engine, seeded = search_database

    dry = _index(session)
    assert dry.published == len(seeded) and dry.to_embed == len(seeded) and dry.embedded == 0
    assert _stored(engine) == 0
    assert dry.sample and dry.sample[0].startswith("Symbol: ")

    applied = _index(session, apply=True)
    assert applied.embedded == len(seeded) and applied.dimensions == DIMENSIONS and applied.failed_batches == 0
    assert applied.input_tokens > 0 and _stored(engine) == len(seeded)

    again = _index(session, apply=True)
    assert again.to_embed == 0 and again.embedded == 0 and again.already_current == len(seeded)
    status = embeddings.index_status(session, model=MODEL)
    assert status == {
        "model": MODEL, "published": len(seeded), "indexed": len(seeded), "dimensions": DIMENSIONS,
        "current": len(seeded), "needsEmbedding": 0,
    }


def test_a_changed_description_is_embedded_again_and_nothing_else(search_database, session):
    engine, seeded = search_database
    revision = seeded["Gate valve"]["revision_id"]
    with engine.begin() as connection:
        before = connection.execute(
            text("SELECT content_hash FROM catalog_symbol_embeddings WHERE symbol_revision_id = :r AND model = :m"),
            {"r": revision, "m": MODEL},
        ).scalar_one()
        connection.execute(
            text("UPDATE symbol_revisions SET payload_json = payload_json || CAST(:p AS jsonb) WHERE id = :r"),
            {"p": '{"summary": "Isolates flow completely when closed."}', "r": revision},
        )
    try:
        report = _index(session, apply=True)
        assert report.to_embed == 1 and report.embedded == 1
        with engine.connect() as connection:
            after = connection.execute(
                text("SELECT content_hash FROM catalog_symbol_embeddings WHERE symbol_revision_id = :r AND model = :m"),
                {"r": revision, "m": MODEL},
            ).scalar_one()
        assert after != before
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE symbol_revisions SET payload_json = payload_json - 'summary' WHERE id = :r"), {"r": revision}
            )
        _index(session, apply=True)


def test_nearest_symbols_are_ranked_and_visible_ones_only(search_database, session):
    engine, seeded = search_database
    embeddings.clear_index_cache()
    hits = embeddings.semantic_candidates(session, fake_vector("valve"), model=MODEL, min_similarity=0.2, top_k=5)
    names_by_revision = {entry["revision_id"]: name for name, entry in seeded.items()}
    ranked = [names_by_revision[hit.symbol_revision_id] for hit in hits]
    assert set(ranked[:2]) == {"Gate valve", "Globe valve 100%"}

    visible = embeddings.public_visible_revision_ids(session, [hit.symbol_revision_id for hit in hits])
    assert visible == {hit.symbol_revision_id for hit in hits}

    # Out of its pack the symbol is not public any more, although its vector stays.
    entry = seeded["Gate valve"]
    with engine.begin() as connection:
        row = connection.execute(text("SELECT * FROM pack_entries WHERE id = :id"), {"id": entry["entry_id"]}).mappings().one()
        connection.execute(text("DELETE FROM pack_entries WHERE id = :id"), {"id": entry["entry_id"]})
    try:
        still_indexed = embeddings.semantic_candidates(session, fake_vector("valve"), model=MODEL, min_similarity=0.2)
        assert entry["revision_id"] in {hit.symbol_revision_id for hit in still_indexed}
        assert entry["revision_id"] not in embeddings.public_visible_revision_ids(
            session, [hit.symbol_revision_id for hit in still_indexed]
        )
        # And the next index run prunes it.
        report = _index(session, apply=True)
        assert report.pruned == 1 and _stored(engine) == len(seeded) - 1
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO pack_entries (id,pack_id,symbol_revision_id,published_page_id,sort_order,created_at) "
                    "VALUES (:id,:pack_id,:symbol_revision_id,:published_page_id,:sort_order,:created_at)"
                ),
                dict(row),
            )
        assert _index(session, apply=True).embedded == 1
    assert _stored(engine) == len(seeded)


def test_stored_filter_labels_narrow_similar_matches_like_the_catalog_search(search_database, session, monkeypatch):
    engine, seeded = search_database
    # Fill the filter store, as any Catalog search does, and read a real label.
    broad = _run(session, monkeypatch, settings=_settings(ed_semantic_search_enabled=False))[0]
    mechanical = next(item.value for item in broad.facets["disciplines"] if item.value.lower().startswith("mech"))
    revisions = [entry["revision_id"] for entry in seeded.values()]

    allowed = embeddings.revisions_matching_facets(session, revisions, {"catalogDisciplines": [mechanical]})

    assert allowed == {seeded["Centrifugal pump"]["revision_id"]}
    assert embeddings.revisions_matching_facets(session, revisions, {}) == set(revisions)
    assert embeddings.revisions_matching_facets(session, revisions, {"unknownFacet": ["x"]}) == set(revisions)


def test_the_tool_adds_similar_matches_after_the_keyword_ones(search_database, session, monkeypatch):
    # "valve closing" matches no symbol by words (every word must match), but
    # is near both valves by meaning.
    result = _run(session, monkeypatch, query="valve closing")

    summary, *found = result
    assert summary.total_matches == 0 and summary.semantic_search == "used"
    assert {item.name for item in found} == {"Gate valve", "Globe valve 100%"}
    assert all(item.match == "similar" and item.similarity >= FLOOR for item in found)
    assert summary.similar_shown == len(found) == summary.shown
    assert [item.similarity for item in found] == sorted((item.similarity for item in found), reverse=True)


def test_keyword_matches_come_first_and_are_not_repeated_as_similar(search_database, session, monkeypatch):
    result = _run(session, monkeypatch, query="gate valve", limit=5)

    summary, *found = result
    keyword = [item for item in found if item.match == "keyword"]
    similar = [item for item in found if item.match == "similar"]
    assert [item.name for item in keyword] == ["Gate valve"]
    assert "Gate valve" not in {item.name for item in similar}
    assert found[: len(keyword)] == keyword


def test_a_full_keyword_page_does_not_call_the_embedding_service(search_database, session, monkeypatch):
    calls = []
    result = _run(
        session, monkeypatch, query="valve", limit=2,
        embed=lambda **kwargs: calls.append(kwargs) or fake_embed(kwargs["texts"]),
    )

    assert result[0].shown == 2 and result[0].semantic_search == "not_used" and calls == []


def test_an_unrelated_query_is_below_the_floor_and_finds_nothing(search_database, session, monkeypatch):
    result = _run(session, monkeypatch, query="teleporter")

    assert result[0].shown == 0 and result[0].semantic_search == "used"


def test_filters_apply_to_similar_matches(search_database, session, monkeypatch):
    broad = _run(session, monkeypatch, settings=_settings(ed_semantic_search_enabled=False))[0]
    mechanical = next(item.value for item in broad.facets["disciplines"] if item.value.lower().startswith("mech"))

    result = _run(session, monkeypatch, query="valve closing", discipline=mechanical)

    assert result[0].shown == 0


def test_a_failed_embedding_leaves_the_keyword_result_and_says_so(search_database, session, monkeypatch):
    def broken(**kwargs):
        raise EmbeddingError("The embedding request failed.")

    result = _run(session, monkeypatch, query="valve", embed=broken)

    assert result[0].semantic_search == "unavailable"
    assert {item.name for item in result[1:]} == {"Gate valve", "Globe valve 100%"}
    assert all(item.match == "keyword" for item in result[1:])


def test_the_flag_off_means_keyword_search_only(search_database, session, monkeypatch):
    result = _run(session, monkeypatch, query="valve closing", settings=_settings(ed_semantic_search_enabled=False))

    assert result[0].semantic_search == "not_used" and result[0].shown == 0


def test_a_private_symbol_is_never_indexed_and_never_a_similar_match(search_database, session, monkeypatch):
    engine, seeded = search_database
    acme = _session_client(
        engine, email="ed-embed-acme@example.test", code="acme", base_role="admin",
        capabilities=("contributor", "symbol_reviewer"),
    )
    _make_organization_wide_symbol(acme, name="Acme closing valve")
    with engine.connect() as connection:
        organization_id = connection.execute(
            text("SELECT id FROM organizations WHERE normalized_code = 'acme'")
        ).scalar_one()

    report = _index(session, apply=True)
    assert report.published == len(seeded) and _stored(engine) == len(seeded)

    keyword = _run(session, monkeypatch, query="acme closing valve", organization_id=organization_id)
    assert [item.name for item in keyword[1:] if item.match == "keyword"] == ["Acme closing valve"]

    mixed = _run(session, monkeypatch, query="valve closing", organization_id=organization_id)
    assert [item.name for item in mixed[1:] if item.match == "keyword"] == ["Acme closing valve"]
    similar = [item for item in mixed[1:] if item.match == "similar"]
    assert {item.name for item in similar} == {"Gate valve", "Globe valve 100%"}
    assert all(item.source == "public" for item in similar)
