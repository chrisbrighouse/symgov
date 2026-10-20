"""Hannah's calibration and automatic approval on a real PostgreSQL (WP-H6).

Commons, the vision model and storage are faked:

- calibration labels only a steward's approvals and rejections, reuses the
  stored score of the model that checked a photo, calls any other model
  afresh, and writes nothing to the photo rows;
- automatic approval takes Hannah's proposals at or above the threshold in
  rank order, stores each as `hannah` with no user, leaves the rest for a
  steward, and keeps a proposal it cannot fetch.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_catalog_browse_search_postgresql import search_database  # noqa: E402,F401  (fixture)
from test_hannah_photos_postgresql import NOW, FakeVision, _photos, _run, _target, targets  # noqa: E402,F401  (fixtures)
from test_hannah_targets_postgresql import concepts  # noqa: E402,F401  (fixture)
from test_wp74_symbol_demotion_postgresql import _client, _create_user_with_global_roles  # noqa: E402

from symgov_backend.hannah_calibration import calibrate  # noqa: E402
from symgov_backend.hannah_review import auto_approve  # noqa: E402

pytest.importorskip("psycopg")


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (90, 120, 150)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _reset(engine):
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM hannah_concept_photos"))
        connection.execute(text("UPDATE hannah_concept_targets SET status = 'pending', attempt_count = 0, next_eligible_at = NULL"))


class OtherVision:
    """A second model that is less sure of photo 1 and calls photo 2 a drawing."""

    def __init__(self):
        self.calls = 0

    def __call__(self, messages):
        self.calls += 1
        caption = json.loads(messages[1]["content"][0]["text"])["image_caption"]
        if "photo 2" in caption:
            return json.dumps({"is_photograph": False, "shows_concept": "yes", "quality": 4, "confidence": 0.9})
        return json.dumps(
            {"is_photograph": True, "shows_concept": "yes", "is_diagram_or_symbol": False, "quality": 5, "confidence": 0.6}
        )


def test_calibration_uses_steward_labels_and_writes_nothing(targets):
    engine, _ids = targets
    _reset(engine)
    _run(engine, FakeVision())
    _client_unused, Session = _client(engine)
    steward = _create_user_with_global_roles(Session, email="hannah-calibrator@example.test", display_name="Steward", roles=["admin"])
    with engine.begin() as connection:
        for title, status in (("Gate valve photo 1.jpg", "approved"), ("Gate valve photo 2.jpg", "rejected")):
            connection.execute(
                text(
                    "UPDATE hannah_concept_photos SET status = :status, decided_by_user_id = :user, decided_at = :now, "
                    "object_key = CASE WHEN :status = 'approved' THEN 'hannah/concept-photos/x.jpg' END WHERE title = :title"
                ),
                {"status": status, "user": steward, "now": NOW, "title": title},
            )
    before = _photos(engine)

    other = OtherVision()
    fetched = []
    with sessionmaker(bind=engine)() as session:
        report = calibrate(
            session,
            ["fake/vision", "other/vision"],
            completer_for=lambda model: FakeVision() if model == "fake/vision" else other,
            fetch_bytes=lambda url: (fetched.append(url), (b"x", "image/jpeg"))[1],
        )

    assert report["labels"] == {"approved": 1, "rejected": 1, "removed_not_used": 0}
    stored = report["models"]["fake/vision"]
    assert sorted((photo["title"], photo["from"], photo["score"]) for photo in stored["photos"]) == [
        ("Gate valve photo 1.jpg", "stored", 0.9), ("Gate valve photo 2.jpg", "stored", 0.8),
    ]
    assert stored["recommended"]["threshold"] == 0.85 and stored["recommended"]["enough_evidence"] is False

    fresh = report["models"]["other/vision"]
    assert other.calls == 2 and report["vision_calls"] == 2
    assert sorted(fetched) == ["https://upload.example/Gate valve photo 1.jpg", "https://upload.example/Gate valve photo 2.jpg"]
    by_title = {photo["title"]: photo for photo in fresh["photos"]}
    assert by_title["Gate valve photo 1.jpg"]["score"] == 0.6
    assert by_title["Gate valve photo 2.jpg"]["score"] is None
    assert by_title["Gate valve photo 2.jpg"]["hard_reject"] == ["vision:not_a_photograph"]
    assert fresh["recommended"]["threshold"] == 0.35

    assert _photos(engine) == before


def test_automatic_approval_takes_proposals_at_the_threshold_as_hannah(targets):
    engine, _ids = targets
    _reset(engine)
    _run(engine, FakeVision())
    stored = {}
    with sessionmaker(bind=engine)() as session:
        concept_id = _target(session).semantic_concept_id
        result = auto_approve(
            session, concept_id, min_score=0.85, fetch_bytes=lambda url: (_jpeg(), "image/jpeg"),
            store_object=lambda key, payload, content_type: stored.update({key: content_type}), now=NOW,
        )
    assert len(result["approved"]) == 1 and result["failed"] == []
    photos = _photos(engine)
    assert [photos[f"Gate valve photo {n}.jpg"].status for n in (1, 2, 3)] == ["approved", "proposed", "candidate"]
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT decided_by_agent, decided_by_user_id, object_key FROM hannah_concept_photos WHERE status = 'approved'")
        ).one()
        audit = connection.execute(
            text("SELECT actor_id, payload_json FROM audit_events WHERE action = 'hannah_photo_approved' ORDER BY created_at DESC LIMIT 1")
        ).one()
        target_status = connection.execute(
            text("SELECT t.status FROM hannah_concept_targets t WHERE t.semantic_concept_id = :id"), {"id": concept_id}
        ).scalar()
    assert (row.decided_by_agent, row.decided_by_user_id) == ("hannah", None)
    assert list(stored) == [row.object_key]
    assert audit.actor_id is None and audit.payload_json["agent"] == "hannah" and audit.payload_json["score"] == 0.9
    # The second proposal is below the threshold, so a steward still has work.
    assert target_status == "has_proposals"


def test_automatic_approval_keeps_a_proposal_it_cannot_fetch(targets):
    engine, _ids = targets
    _reset(engine)
    _run(engine, FakeVision())

    def broken(url):
        raise OSError("offline")

    with sessionmaker(bind=engine)() as session:
        concept_id = _target(session).semantic_concept_id
        result = auto_approve(
            session, concept_id, min_score=0.5, fetch_bytes=broken,
            store_object=lambda key, payload, content_type: None, now=NOW,
        )
    assert result["approved"] == [] and len(result["failed"]) == 2
    photos = _photos(engine)
    assert [photos[f"Gate valve photo {n}.jpg"].status for n in (1, 2)] == ["proposed", "proposed"]


def test_the_runner_auto_approves_after_a_search_when_the_threshold_is_set(targets, monkeypatch, tmp_path):
    from symgov_backend import hannah_runner
    from test_hannah_photos_postgresql import fake_commons

    engine, _ids = targets
    _reset(engine)
    stored = {}
    monkeypatch.setenv("SYMGOV_HANNAH_AUTO_APPROVE_MIN_SCORE", "0.75")
    monkeypatch.setattr(hannah_runner, "create_session_factory", lambda **kwargs: sessionmaker(bind=engine))
    monkeypatch.setattr(hannah_runner, "resolve_model_for_feature", lambda feature: "fake/vision")
    monkeypatch.setattr(hannah_runner, "default_http_get_json", fake_commons)
    monkeypatch.setattr(hannah_runner, "make_vision_completer", lambda model, **kwargs: FakeVision())
    monkeypatch.setattr(
        hannah_runner, "default_fetch_bytes", lambda url: (_jpeg() if "upload.example" in url else url.encode(), "image/jpeg")
    )
    monkeypatch.setattr(
        hannah_runner, "upload_object_bytes",
        lambda *, object_key, payload, content_type, env_file: stored.update({object_key: content_type}),
    )
    monkeypatch.setattr(hannah_runner.RuntimePersistenceBridge, "__init__", lambda self, env_file=None: None)
    monkeypatch.setattr(hannah_runner.RuntimePersistenceBridge, "persist_agent_execution", lambda self, **kwargs: {"ok": True})
    monkeypatch.setattr("symgov_backend.hannah_photos.time.sleep", lambda seconds: None)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO agent_definitions (id, slug, display_name, role, model, status, queue_family, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'hannah', 'Hannah', 'curation', 'none', 'active', 'curation', now(), now()) "
                "ON CONFLICT (slug) DO NOTHING"
            )
        )
    with sessionmaker(bind=engine)() as session:
        row = session.execute(
            text(hannah_runner.CARD_QUERY + " WHERE c.concept_code = 'SGC-90000001'")
        ).mappings().one()
        card = hannah_runner.create_concept_card(
            session, hannah_id=hannah_runner.hannah_agent_id(session), row=row, runtime_root=tmp_path,
            created_at=NOW.isoformat(),
        )
        session.commit()
    card = Path(card["path"])

    result = hannah_runner.process_queue_item(card, tmp_path, persist_db=True)

    assert result["queue_item_status"] == "success"
    photos = _photos(engine)
    assert [photos[f"Gate valve photo {n}.jpg"].status for n in (1, 2, 3)] == ["approved", "approved", "candidate"]
    assert len(stored) == 2
    report = json.loads(Path(result["curation_report_path"]).read_text())["payload_json"]
    assert report["auto_approve_min_score"] == 0.75 and len(report["auto_approved"]["approved"]) == 2
    assert (report["target_status"], report["proposed"]) == ("has_photos", 0)


def test_automatic_approval_keeps_a_proposal_it_cannot_store(targets):
    engine, _ids = targets
    _reset(engine)
    _run(engine, FakeVision())

    def full(key, payload, content_type):
        raise OSError("bucket unavailable")

    with sessionmaker(bind=engine)() as session:
        concept_id = _target(session).semantic_concept_id
        result = auto_approve(
            session, concept_id, min_score=0.5, fetch_bytes=lambda url: (_jpeg(), "image/jpeg"), store_object=full, now=NOW,
        )
    assert result["approved"] == [] and [item["error"] for item in result["failed"]] == ["The photo could not be stored: OSError."] * 2
    assert _photos(engine)["Gate valve photo 1.jpg"].status == "proposed"
