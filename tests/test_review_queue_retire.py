from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from symgov_backend import review_queue_retire as cli
from symgov_backend.models import AuditEvent, ReviewCase, ReviewSplitItem


# The model tables carry PostgreSQL-only server defaults, so SQLite gets the
# minimal columns the CLI reads and writes instead of metadata.create_all.
SQLITE_DDL = (
    "CREATE TABLE review_cases (id CHAR(32) PRIMARY KEY, source_entity_type TEXT NOT NULL,"
    " source_entity_id CHAR(32) NOT NULL, current_stage TEXT NOT NULL, owner_id CHAR(32),"
    " escalation_level TEXT NOT NULL, opened_at DATETIME NOT NULL, closed_at DATETIME)",
    "CREATE TABLE review_split_items (id CHAR(32) PRIMARY KEY, review_case_id CHAR(32) NOT NULL,"
    " child_key TEXT NOT NULL, proposed_symbol_id TEXT NOT NULL, proposed_symbol_name TEXT NOT NULL,"
    " file_name TEXT NOT NULL, parent_file_name TEXT NOT NULL, name_source TEXT,"
    " attachment_object_key TEXT, status TEXT NOT NULL, latest_action TEXT, latest_note TEXT,"
    " latest_details TEXT, latest_decision_id CHAR(32), latest_action_id CHAR(32),"
    " downstream_agent_slug TEXT, downstream_queue_item_id TEXT, payload_json JSON NOT NULL,"
    " created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, processed_at DATETIME)",
    "CREATE TABLE audit_events (id CHAR(32) PRIMARY KEY, entity_type TEXT NOT NULL,"
    " entity_id CHAR(32) NOT NULL, action TEXT NOT NULL, actor_id CHAR(32),"
    " payload_json JSON NOT NULL, created_at DATETIME NOT NULL)",
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _case(source_type, stage="classification_review", closed=False):
    return ReviewCase(
        id=uuid.uuid4(),
        source_entity_type=source_type,
        source_entity_id=uuid.uuid4(),
        current_stage=stage,
        escalation_level="none",
        opened_at=NOW,
        closed_at=NOW if closed else None,
    )


def _child(case, status):
    return ReviewSplitItem(
        id=uuid.uuid4(),
        review_case_id=case.id,
        child_key=str(uuid.uuid4()),
        proposed_symbol_id="X-1",
        proposed_symbol_name="X",
        file_name="x.png",
        parent_file_name="sheet.png",
        status=status,
        payload_json={},
        created_at=NOW,
        updated_at=NOW,
    )


def _database(monkeypatch, tmp_path):
    path = tmp_path / "retire.db"
    url = f"sqlite+pysqlite:///{path}"
    engine = create_engine(url, poolclass=StaticPool, connect_args={"check_same_thread": False})
    with engine.begin() as connection:
        for statement in SQLITE_DDL:
            connection.execute(text(statement))
    monkeypatch.setenv("SYMGOV_DATABASE_URL", url)
    # The CLI builds its own engine; share ours so the in-file db is the same.
    monkeypatch.setattr(cli, "_engine", lambda: create_engine(url, hide_parameters=True))
    return engine


def _seed(engine):
    validation = _case("validation_report")
    provenance = _case("provenance_assessment", "provenance_rights_review")
    already_closed = _case("validation_report", closed=True)
    promotion = _case("promotion_request", "promotion_review")
    ids = (validation.id, provenance.id, already_closed.id, promotion.id)
    with Session(engine) as session:
        session.add_all([validation, provenance, already_closed, promotion])
        session.flush()
        session.add_all(
            [
                _child(validation, "awaiting_decision"),
                _child(validation, "returned_for_review"),
                _child(validation, "queued_rupert"),
                _child(promotion, "awaiting_decision"),
            ]
        )
        session.commit()
    return ids


def test_dry_run_reports_and_writes_nothing(monkeypatch, tmp_path, capsys):
    engine = _database(monkeypatch, tmp_path)
    _seed(engine)

    assert cli.main([]) == 0
    summary = json.loads(capsys.readouterr().out)

    assert summary["mode"] == "dry-run"
    assert summary["cases"] == 2
    assert summary["split_children"] == 2
    assert summary["untouched_other_sources"] == {"promotion_request": 1}
    with Session(engine) as session:
        assert session.scalar(select(ReviewCase).where(ReviewCase.closed_at.is_(None)).limit(1)) is not None
        assert len(session.scalars(select(ReviewCase).where(ReviewCase.closed_at.is_(None))).all()) == 3
        assert session.scalars(select(AuditEvent)).all() == []


def test_apply_closes_queue_cases_only_and_audits_each(monkeypatch, tmp_path, capsys):
    engine = _database(monkeypatch, tmp_path)
    validation_id, provenance_id, closed_id, promotion_id = _seed(engine)
    actor = uuid.uuid4()

    assert cli.main(["--apply", "--actor-id", str(actor), "--reason", "historical cleanup"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["mode"] == "apply"
    assert summary["by_source_and_stage"] == {
        "validation_report/classification_review": 1,
        "provenance_assessment/provenance_rights_review": 1,
    }

    with Session(engine) as session:
        retired = {c.id: c for c in session.scalars(select(ReviewCase)).all()}
        assert retired[validation_id].closed_at is not None
        assert retired[validation_id].current_stage == cli.RETIRED_STAGE
        assert retired[provenance_id].current_stage == cli.RETIRED_STAGE
        assert retired[closed_id].current_stage == "classification_review"
        assert retired[promotion_id].closed_at is None
        assert retired[promotion_id].current_stage == "promotion_review"

        statuses = sorted(
            (c.review_case_id == promotion_id, c.status) for c in session.scalars(select(ReviewSplitItem)).all()
        )
        assert statuses == [
            (False, "deleted"),
            (False, "deleted"),
            (False, "queued_rupert"),
            (True, "awaiting_decision"),
        ]

        events = session.scalars(select(AuditEvent)).all()
        assert {e.entity_id for e in events} == {validation_id, provenance_id}
        assert all(e.actor_id == actor and e.action == cli.AUDIT_ACTION for e in events)
        by_case = {e.entity_id: e.payload_json for e in events}
        assert by_case[validation_id]["previous_stage"] == "classification_review"
        assert by_case[validation_id]["reason"] == "historical cleanup"


def test_second_apply_finds_nothing(monkeypatch, tmp_path, capsys):
    engine = _database(monkeypatch, tmp_path)
    _seed(engine)
    args = ["--apply", "--actor-id", str(uuid.uuid4()), "--reason", "r"]
    assert cli.main(args) == 0
    capsys.readouterr()
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["cases"] == 0


def test_apply_requires_actor_and_reason(monkeypatch, tmp_path):
    _database(monkeypatch, tmp_path)
    try:
        cli.main(["--apply"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("--apply without actor and reason must be refused")
