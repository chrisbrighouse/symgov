"""Hannah's concept targets on a real PostgreSQL (WP-H2).

The model and Wikidata are faked, so this proves the scope query and the
write rules rather than answer quality:

- only active concepts with a verified primary assignment on a public Catalog
  symbol are in scope: a private symbol's concept and a proposed assignment's
  concept are not;
- a dry run writes nothing;
- `physical_equipment` and the abstract kinds are decided by `concept_kind`,
  `other` by the model; an abstract concept is excluded and never linked;
- a second run asks nothing again, and a steward's decision survives
  `reclassify`.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_catalog_browse_search_postgresql import search_database  # noqa: E402,F401  (fixture)

from symgov_backend.hannah_targets import CLASSIFY_SYSTEM_PROMPT, PICK_SYSTEM_PROMPT, build_targets  # noqa: E402
from symgov_backend.models.schema import HannahConceptTarget  # noqa: E402

pytest.importorskip("psycopg")

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)


def _concept(connection, *, code, kind, name, alias, owner_id):
    concept_id, revision_id = uuid.uuid4(), uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO semantic_concepts (id,concept_code,concept_kind,status,created_by_user_id,created_at,updated_at) "
            "VALUES (:id,:code,:kind,'active',:owner,:now,:now)"
        ),
        {"id": concept_id, "code": code, "kind": kind, "owner": owner_id, "now": NOW},
    )
    connection.execute(
        text(
            "INSERT INTO semantic_concept_revisions (id,concept_id,revision_label,lifecycle_state,preferred_name,definition,"
            "aliases_json,author_id,created_at) VALUES (:id,:concept,'1','published',:name,:definition,CAST(:aliases AS jsonb),:owner,:now)"
        ),
        {
            "id": revision_id, "concept": concept_id, "name": name, "owner": owner_id, "now": NOW,
            "definition": f"{alias}: a test concept.", "aliases": json.dumps([alias]),
        },
    )
    connection.execute(
        text("UPDATE semantic_concepts SET current_revision_id=:revision WHERE id=:id"), {"revision": revision_id, "id": concept_id}
    )
    return concept_id


def _assign(connection, *, revision_id, concept_id, status="verified"):
    connection.execute(
        text(
            "INSERT INTO symbol_semantic_assignments (id,symbol_revision_id,semantic_concept_id,assignment_role,status,method,"
            "created_at,updated_at,reviewed_at) VALUES (:id,:revision,:concept,'primary',:status,'manual',:now,:now,"
            "CASE WHEN :status IN ('proposed','retired') THEN NULL ELSE CAST(:now AS timestamptz) END)"
        ),
        {"id": uuid.uuid4(), "revision": revision_id, "concept": concept_id, "status": status, "now": NOW},
    )


@pytest.fixture(scope="module")
def concepts(search_database):
    engine, seeded = search_database
    with engine.begin() as connection:
        owner_id = connection.execute(text("SELECT owner_id FROM governed_symbols LIMIT 1")).scalar()
        ids = {
            "valve": _concept(connection, code="SGC-90000001", kind="physical_equipment", name="GateValve", alias="Gate valve", owner_id=owner_id),
            "pump": _concept(connection, code="SGC-90000002", kind="other", name="PumpCentrifugalType", alias="Pump, centrifugal type", owner_id=owner_id),
            "tag": _concept(connection, code="SGC-90000003", kind="annotation", name="TagBubble", alias="Tag bubble", owner_id=owner_id),
            "private": _concept(connection, code="SGC-90000004", kind="physical_equipment", name="FireHydrant", alias="Fire hydrant", owner_id=owner_id),
            "proposed": _concept(connection, code="SGC-90000005", kind="physical_equipment", name="SmokeDetector", alias="Smoke detector", owner_id=owner_id),
        }
        _assign(connection, revision_id=seeded["Gate valve"]["revision_id"], concept_id=ids["valve"])
        _assign(connection, revision_id=seeded["Globe valve 100%"]["revision_id"], concept_id=ids["valve"])
        _assign(connection, revision_id=seeded["Centrifugal pump"]["revision_id"], concept_id=ids["pump"])
        _assign(connection, revision_id=seeded["Tag bubble"]["revision_id"], concept_id=ids["tag"])
        _assign(connection, revision_id=seeded["Smoke detector"]["revision_id"], concept_id=ids["proposed"], status="proposed")

        # A draft revision that no public pack carries: not in the Catalog.
        symbol_id, revision_id = uuid.uuid4(), uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at) "
                "VALUES (:id,'private-hydrant','Fire hydrant','fire','civil',:owner,:now,:now)"
            ),
            {"id": symbol_id, "owner": owner_id, "now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO symbol_revisions (id,symbol_id,revision_label,lifecycle_state,payload_json,author_id,created_at) "
                "VALUES (:id,:symbol,'A','draft','{}'::jsonb,:owner,:now)"
            ),
            {"id": revision_id, "symbol": symbol_id, "owner": owner_id, "now": NOW},
        )
        _assign(connection, revision_id=revision_id, concept_id=ids["private"])
    yield engine, ids
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM hannah_concept_targets"))


class FakeModel:
    def __init__(self):
        self.classify_calls = 0
        self.pick_calls = 0

    def __call__(self, messages):
        system = messages[0]["content"]
        payload = json.loads(messages[1]["content"])
        if system == CLASSIFY_SYSTEM_PROMPT:
            self.classify_calls += 1
            return json.dumps(
                {
                    "concepts": [
                        {"code": item["code"], "photographable": "yes", "search_terms": [item["name"].lower()]}
                        for item in payload["concepts"]
                    ]
                }
            )
        assert system == PICK_SYSTEM_PROMPT
        self.pick_calls += 1
        return json.dumps({"qid": payload["candidates"][0]["qid"], "confidence": 0.8})


def fake_wikidata(url, params):
    if params["action"] == "wbsearchentities":
        return {"search": [{"id": "Q1", "label": params["search"], "description": "a thing"}]}
    return {
        "entities": {
            params["ids"]: {
                "claims": {"P373": [{"mainsnak": {"datavalue": {"value": "Test category"}}}]},
                "sitelinks": {"enwiki": {"title": "Test page"}},
            }
        }
    }


def _targets(engine):
    with engine.connect() as connection:
        return {
            row.concept_code: row
            for row in connection.execute(
                text(
                    "SELECT c.concept_code, t.photographable, t.photographable_basis, t.status, t.search_terms_json, "
                    "t.wikidata_qid, t.commons_category FROM hannah_concept_targets t "
                    "JOIN semantic_concepts c ON c.id = t.semantic_concept_id"
                )
            )
        }


def _build(engine, model, **kwargs):
    with sessionmaker(bind=engine)() as session:
        return build_targets(session, complete=model, http_get_json=fake_wikidata, pause=lambda: None, now=NOW, **kwargs)


def test_scope_dry_run_apply_and_rerun(concepts):
    engine, _ids = concepts
    model = FakeModel()

    dry = _build(engine, model, apply=False)
    assert dry["applied"] is False
    assert dry["in_scope"] == 3
    assert {row["concept_code"] for row in dry["concepts"]} == {"SGC-90000001", "SGC-90000002", "SGC-90000003"}
    assert _targets(engine) == {}

    applied = _build(engine, model, apply=True)
    assert applied["applied"] is True
    targets = _targets(engine)
    assert set(targets) == {"SGC-90000001", "SGC-90000002", "SGC-90000003"}
    valve, pump, tag = targets["SGC-90000001"], targets["SGC-90000002"], targets["SGC-90000003"]
    assert (valve.photographable, valve.photographable_basis, valve.status) == ("yes", "concept_kind", "pending")
    assert valve.search_terms_json == ["gate valve"]
    assert (valve.wikidata_qid, valve.commons_category) == ("Q1", "Test category")
    assert (pump.photographable, pump.photographable_basis) == ("yes", "llm")
    assert (tag.photographable, tag.photographable_basis, tag.status) == ("no", "concept_kind", "excluded")
    assert tag.wikidata_qid is None

    calls = (model.classify_calls, model.pick_calls)
    again = _build(engine, model, apply=True)
    assert again["worked"] == 0
    assert (model.classify_calls, model.pick_calls) == calls


def test_a_steward_decision_survives_reclassify(concepts):
    engine, ids = concepts
    model = FakeModel()
    _build(engine, model, apply=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE hannah_concept_targets SET photographable='no', photographable_basis='steward', "
                "search_terms_json='[\"steward term\"]'::jsonb, status='excluded' WHERE semantic_concept_id=:id"
            ),
            {"id": ids["pump"]},
        )

    _build(engine, model, apply=True, reclassify=True)

    pump = _targets(engine)["SGC-90000002"]
    assert (pump.photographable, pump.photographable_basis, pump.status) == ("no", "steward", "excluded")
    assert pump.search_terms_json == ["steward term"]
    with sessionmaker(bind=engine)() as session:
        assert session.query(HannahConceptTarget).count() == 3
