"""Hannah's concept photo search on a real PostgreSQL (WP-H3).

Commons and the vision model are faked, so this proves the write rules:

- a run stores every image it looked at, proposes the best two, and moves the
  target to `has_proposals`; a dry run writes nothing;
- a second run never checks a seen image again, and an approved photo keeps
  its slot so only one proposal remains;
- the seeder queues one card per due target, never a second open card, and
  never a target waiting on a steward.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_catalog_browse_search_postgresql import search_database  # noqa: E402,F401  (fixture)
from test_hannah_targets_postgresql import FakeModel, concepts, fake_wikidata  # noqa: E402,F401  (fixtures)

from symgov_backend.hannah_photos import curate_concept, target_view_from_row  # noqa: E402
from symgov_backend.hannah_targets import build_targets  # noqa: E402
from symgov_backend.models.schema import HannahConceptTarget  # noqa: E402

pytest.importorskip("psycopg")

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
VALVE = "SGC-90000001"


def _page(title):
    return {
        "title": f"File:{title}",
        "imageinfo": [
            {
                "url": f"https://upload.example/{title}",
                "thumburl": f"https://thumb.example/{title}",
                "descriptionurl": f"https://commons.example/{title}",
                "width": 1200, "height": 900, "mime": "image/jpeg",
                "extmetadata": {"Artist": {"value": "Ann"}, "LicenseShortName": {"value": "CC BY 4.0"}},
            }
        ],
    }


FILES = ["Gate valve photo 1.jpg", "Gate valve photo 2.jpg", "Gate valve photo 3.jpg", "Gate valve render.jpg"]


def fake_commons(url, params):
    if params.get("generator") == "search":
        return {"query": {"pages": {str(index): _page(title) for index, title in enumerate(FILES)}}}
    return fake_wikidata(url, params) if "wikidata" in url else {}


class FakeVision:
    def __init__(self):
        self.calls = 0

    def __call__(self, messages):
        self.calls += 1
        caption = json.loads(messages[1]["content"][0]["text"])["image_caption"]
        if "render" in caption:
            return json.dumps({"is_photograph": False, "shows_concept": "yes", "quality": 4, "confidence": 0.9})
        confidence = {"1": 0.9, "2": 0.8, "3": 0.7}[caption.split("photo ")[1][0]]
        return json.dumps(
            {"is_photograph": True, "shows_concept": "yes", "is_diagram_or_symbol": False, "quality": 5, "confidence": confidence}
        )


def _target(session):
    concept_id = session.execute(text("SELECT id FROM semantic_concepts WHERE concept_code = :code"), {"code": VALVE}).scalar()
    return session.query(HannahConceptTarget).filter_by(semantic_concept_id=concept_id).one()


def _run(engine, vision, *, apply=True):
    with sessionmaker(bind=engine)() as session:
        return curate_concept(
            session, target_view_from_row(session, _target(session)), apply=apply, http_get_json=fake_commons,
            fetch_bytes=lambda url: (url.encode(), "image/jpeg"), complete_vision=vision,
            vision_model="fake/vision", max_vision_calls=50, pause=lambda: None, now=NOW,
        )


def _photos(engine):
    with engine.connect() as connection:
        return {
            row.title: row
            for row in connection.execute(text("SELECT title, status, rank, score, author, license_label FROM hannah_concept_photos"))
        }


@pytest.fixture(scope="module")
def targets(concepts):
    engine, ids = concepts
    with sessionmaker(bind=engine)() as session:
        build_targets(session, apply=True, complete=FakeModel(), http_get_json=fake_wikidata, pause=lambda: None, now=NOW)
    yield engine, ids
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM agent_queue_items"))
        connection.execute(text("DELETE FROM hannah_concept_photos"))


def test_a_run_stores_every_image_and_proposes_the_best_two(targets):
    engine, _ids = targets

    dry = _run(engine, FakeVision(), apply=False)
    assert dry["proposed"] == 2 and _photos(engine) == {}

    vision = FakeVision()
    report = _run(engine, vision)
    assert report["applied"] and vision.calls == 4
    photos = _photos(engine)
    assert [photos[f"Gate valve photo {n}.jpg"].status for n in (1, 2, 3)] == ["proposed", "proposed", "candidate"]
    assert photos["Gate valve render.jpg"].status == "filtered"
    assert (photos["Gate valve photo 1.jpg"].author, photos["Gate valve photo 1.jpg"].license_label) == ("Ann", "CC BY 4.0")
    with sessionmaker(bind=engine)() as session:
        target = _target(session)
        assert (target.status, target.attempt_count, target.next_eligible_at) == ("has_proposals", 1, None)

    # A steward approves the best one (WP-H4 will do this through a route).
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE hannah_concept_photos SET status = 'approved', object_key = 'hannah/concept-photos/x.jpg', "
                "decided_at = :now WHERE title = 'Gate valve photo 1.jpg'"
            ),
            {"now": NOW},
        )
    again = FakeVision()
    _run(engine, again)
    assert again.calls == 0
    photos = _photos(engine)
    assert [photos[f"Gate valve photo {n}.jpg"].status for n in (1, 2, 3)] == ["approved", "proposed", "candidate"]


def _runner():
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_hannah_concept_curation.py"
    spec = importlib.util.spec_from_file_location("hannah_concept_runner_pg", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_seeder_queues_due_targets_once(targets, tmp_path):
    engine, _ids = targets
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO agent_definitions (id, slug, display_name, role, model, status, queue_family, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'hannah', 'Hannah', 'curation', 'none', 'active', 'curation', :now, :now) "
                "ON CONFLICT (slug) DO NOTHING"
            ),
            {"now": NOW},
        )
        connection.execute(text("UPDATE hannah_concept_targets SET status = 'pending' WHERE status <> 'excluded'"))
        connection.execute(
            text(
                "UPDATE hannah_concept_targets SET status = 'has_proposals' FROM semantic_concepts c "
                "WHERE c.id = hannah_concept_targets.semantic_concept_id AND c.concept_code = :code"
            ),
            {"code": VALVE},
        )
    runner = _runner()
    factory = sessionmaker(bind=engine)

    first = runner.seed_hannah_concept_queue_cards(db_env_file=None, runtime_root=tmp_path, session_factory=factory)
    second = runner.seed_hannah_concept_queue_cards(db_env_file=None, runtime_root=tmp_path, session_factory=factory)

    # The pump is due; the valve waits on a steward; the tag bubble is excluded.
    assert [card["concept_code"] for card in first["created"]] == ["SGC-90000002"]
    assert second["createdCount"] == 0
    card = json.loads((tmp_path / "agent_queue_items" / f"{first['created'][0]['queue_item_id']}.json").read_text())
    assert card["source_type"] == "concept_photo_curation"
    assert card["payload_json"]["display_name"] == "SGC-90000002 Pump, centrifugal type"
