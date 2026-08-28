from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import threading
from types import SimpleNamespace
import time
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from symgov_backend import publication_handoff, runtime
from symgov_backend.auth import AuthenticatedUser
from symgov_backend.publication_authority import lock_review_case_decision_authority
from symgov_backend.routes import workspace as workspace_routes
from symgov_backend.schemas import WorkspaceRightsReviewDecisionRequest, WorkspaceSplitReviewProcessRequest
from symgov_backend.models import (
    AgentDefinition,
    AgentOutputArtifact,
    AgentQueueItem,
    AgentRun,
    Attachment,
    AuditEvent,
    CatalogSymbolIdentifier,
    GovernedSymbol,
    HumanReviewDecision,
    PackEntry,
    PublicationJob,
    PublicationPack,
    PublishedPage,
    ReviewCase,
    ReviewCaseAction,
    ReviewSplitItem,
    SymbolRevision,
    User,
    ValidationReport,
)
from symgov_backend.published_catalog import published_symbol_display_id


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "backend" / "symgov_backend" / "runtime.py"
BACKEND = ROOT / "backend"


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _alembic(url: str, *args: str) -> None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(BACKEND),
        "SYMGOV_DATABASE_URL": url,
        "SYMGOV_MIGRATION_DATABASE_URL": url,
    }
    subprocess.run(
        ["alembic", *args],
        cwd=BACKEND,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.fixture(scope="module")
def publication_database():
    psycopg = pytest.importorskip("psycopg")
    if shutil.which("docker") is None or _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required for the disposable publication PostgreSQL fixture")

    name = f"symgov-publication-{uuid.uuid4().hex[:12]}"
    password = "disposable-publication-password"
    _docker(
        "run",
        "--rm",
        "--detach",
        "--name",
        name,
        "--env",
        f"POSTGRES_PASSWORD={password}",
        "--env",
        "POSTGRES_DB=symgov_publication",
        "--publish",
        "127.0.0.1::5432",
        "postgres:16-alpine",
    )
    engine = None
    try:
        port = int(_docker("port", name, "5432/tcp").stdout.strip().rsplit(":", 1)[1])
        raw_url = f"postgresql://postgres:{password}@127.0.0.1:{port}/symgov_publication"
        url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
        deadline = time.monotonic() + 60
        while True:
            try:
                with psycopg.connect(raw_url, connect_timeout=2) as connection:
                    connection.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)

        with psycopg.connect(raw_url, autocommit=True) as connection:
            connection.execute("CREATE ROLE symgov_app")
        _alembic(url, "upgrade", "head")
        engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE symgov_test_publication_order (
                        event text NOT NULL,
                        symbol_id uuid NOT NULL,
                        catalog_symbol_id text NOT NULL
                    );
                    CREATE TABLE symgov_test_publication_failure (armed boolean NOT NULL);

                    CREATE OR REPLACE FUNCTION symgov_test_assert_publication_identity()
                    RETURNS trigger LANGUAGE plpgsql AS $$
                    DECLARE
                        target_symbol_id uuid;
                        target_catalog_symbol_id text;
                    BEGIN
                        IF TG_TABLE_NAME = 'published_pages' THEN
                            SELECT symbol_id INTO target_symbol_id
                            FROM symbol_revisions WHERE id = NEW.current_symbol_revision_id;
                        ELSIF TG_TABLE_NAME = 'pack_entries' THEN
                            SELECT symbol_id INTO target_symbol_id
                            FROM symbol_revisions WHERE id = NEW.symbol_revision_id;
                        ELSE
                            target_symbol_id := NEW.symbol_id;
                        END IF;

                        SELECT catalog_symbol_id INTO target_catalog_symbol_id
                        FROM governed_symbols WHERE id = target_symbol_id;
                        IF target_catalog_symbol_id IS NULL OR NOT EXISTS (
                            SELECT 1 FROM catalog_symbol_identifiers
                            WHERE identifier = target_catalog_symbol_id
                              AND role = 'canonical'
                              AND governed_symbol_id = target_symbol_id
                        ) THEN
                            RAISE EXCEPTION 'publication write preceded canonical identity allocation';
                        END IF;
                        INSERT INTO symgov_test_publication_order
                            (event, symbol_id, catalog_symbol_id)
                        VALUES (TG_TABLE_NAME, target_symbol_id, target_catalog_symbol_id);
                        RETURN NEW;
                    END;
                    $$;

                    CREATE TRIGGER symgov_test_page_identity_order
                    BEFORE INSERT ON published_pages
                    FOR EACH ROW EXECUTE FUNCTION symgov_test_assert_publication_identity();
                    CREATE TRIGGER symgov_test_pack_entry_identity_order
                    BEFORE INSERT ON pack_entries
                    FOR EACH ROW EXECUTE FUNCTION symgov_test_assert_publication_identity();
                    CREATE TRIGGER symgov_test_lifecycle_identity_order
                    BEFORE INSERT OR UPDATE OF lifecycle_state ON symbol_revisions
                    FOR EACH ROW WHEN (NEW.lifecycle_state = 'published')
                    EXECUTE FUNCTION symgov_test_assert_publication_identity();

                    CREATE OR REPLACE FUNCTION refresh_published_symbol_views()
                    RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
                    BEGIN
                        IF EXISTS (SELECT 1 FROM symgov_test_publication_failure WHERE armed) THEN
                            RAISE EXCEPTION 'synthetic post-publication failure';
                        END IF;
                        REFRESH MATERIALIZED VIEW published_symbol_views;
                    END;
                    $$;
                    """
                )
            )
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        _docker("rm", "--force", name, check=False)


@pytest.fixture(scope="module")
def publication_context(publication_database):
    Session = sessionmaker(
        bind=publication_database,
        autoflush=False,
        expire_on_commit=False,
    )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    owner_id = uuid.uuid4()
    with Session.begin() as session:
        session.add_all(
            [
                User(
                    id=owner_id,
                    email="publication-owner@example.test",
                    display_name="Publication Owner",
                    pin_hash="test",
                    pin_set_at=now,
                    must_change_pin=False,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                ),
                AgentDefinition(
                    id=uuid.uuid4(),
                    slug="rupert",
                    display_name="Rupert",
                    role="publication",
                    model="test",
                    status="active",
                    queue_family="publication",
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
    bridge = runtime.RuntimePersistenceBridge.__new__(runtime.RuntimePersistenceBridge)
    bridge.session_factory = Session
    return SimpleNamespace(Session=Session, bridge=bridge, owner_id=owner_id, now=now)


def _seed_symbol(context, *, slug: str, revision_label: str = "A"):
    symbol_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    with context.Session.begin() as session:
        session.add(
            GovernedSymbol(
                id=symbol_id,
                catalog_symbol_id=None,
                slug=slug,
                canonical_name=slug.replace("-", " ").title(),
                category="test",
                discipline="test",
                owner_id=context.owner_id,
                current_revision_id=None,
                created_at=context.now,
                updated_at=context.now,
            )
        )
        session.add(
            SymbolRevision(
                id=revision_id,
                symbol_id=symbol_id,
                revision_label=revision_label,
                lifecycle_state="approved",
                payload_json={},
                author_id=context.owner_id,
                created_at=context.now,
            )
        )
    return symbol_id, revision_id


def _publication_handoff(context, revision_ids, *, pack_code: str):
    review_case_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    with context.Session.begin() as session:
        session.add(
            ReviewCase(
                id=review_case_id,
                source_entity_type="symbol_revision",
                source_entity_id=revision_ids[0],
                current_stage="publication_review",
                owner_id=context.owner_id,
                escalation_level="medium",
                opened_at=context.now,
                closed_at=context.now,
            )
        )
        session.flush()
        session.add(
            HumanReviewDecision(
                id=decision_id,
                review_case_id=review_case_id,
                decision_code="approve",
                decision_summary="Approved for publication.",
                decision_note=None,
                decided_by=context.owner_id,
                decider_name="Publication Owner",
                decider_role="reviewer",
                from_stage="publication_review",
                to_stage="published",
                decision_payload_json={},
                created_at=context.now,
                superseded_at=None,
            )
        )
        for revision_id in revision_ids:
            revision = session.get(SymbolRevision, revision_id)
            revision.payload_json = {
                **(revision.payload_json or {}),
                "review_decision_id": str(decision_id),
            }
        session.flush()
        approval_target = runtime.ensure_publication_approval_target(
            session,
            review_decision=session.get(HumanReviewDecision, decision_id),
            revisions=[session.get(SymbolRevision, revision_id) for revision_id in revision_ids],
            created_at=context.now,
        )

    now = context.now.isoformat().replace("+00:00", "Z")
    queue_id, run_id, artifact_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    actor = {
        "id": str(context.owner_id),
        "display_name": "Publication Owner",
        "effective_role": "reviewer",
    }
    queue_item = {
        "id": str(queue_id),
        "agent_id": "rupert",
        "source_type": "review_decision",
        "source_id": str(decision_id),
        "status": "completed",
        "priority": "medium",
        "payload_json": {
            "review_decision_id": str(decision_id),
            "review_case_id": str(review_case_id),
            "symbol_revision_ids": [str(item) for item in revision_ids],
            "human_decision": "approve",
            "human_approved": True,
            "approval_actor": actor,
            "approval_target_id": str(approval_target.id),
            "approval_content_sha256": approval_target.content_sha256,
        },
        "created_at": now,
        "started_at": now,
        "completed_at": now,
    }
    run_record = {
        "id": str(run_id),
        "model": "test",
        "prompt_version": "test",
        "tool_trace_json": [],
        "result_status": "completed",
        "started_at": now,
        "completed_at": now,
    }
    artifact_record = {
        "id": str(artifact_id),
        "artifact_type": "publication",
        "schema_version": "1",
        "created_at": now,
        "payload_json": {
            "decision": "stage",
            "staged_symbol_revisions": [str(item) for item in revision_ids],
            "approval_target_id": str(approval_target.id),
            "approval_content_sha256": approval_target.content_sha256,
            "release_target": "standards-current",
            "publication_pack": {
                "pack_code": pack_code,
                "title": f"Pack {pack_code}",
                "effective_date": context.now.date().isoformat(),
            },
        },
    }
    return queue_item, run_record, artifact_record, {"id": str(uuid.uuid4())}


def _publish(context, revision_ids, *, pack_code: str):
    records = _publication_handoff(context, revision_ids, pack_code=pack_code)
    return context.bridge.persist_publication_execution(*records)


def test_superseded_publication_approval_replay_has_zero_durable_side_effects(
    publication_context,
) -> None:
    symbol_id, revision_id = _seed_symbol(
        publication_context,
        slug="superseded-approval-replay",
    )
    records = _publication_handoff(
        publication_context,
        [revision_id],
        pack_code="superseded-approval-pack",
    )
    decision_id = uuid.UUID(records[0]["payload_json"]["review_decision_id"])
    counted_tables = (
        "agent_queue_items",
        "agent_runs",
        "agent_output_artifacts",
        "publication_packs",
        "publication_jobs",
        "published_pages",
        "pack_entries",
        "catalog_symbol_identifiers",
        "audit_events",
    )
    with publication_context.Session.begin() as session:
        session.get(HumanReviewDecision, decision_id).superseded_at = publication_context.now
        before = {
            table: session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in counted_tables
        }

    with pytest.raises(RuntimeError, match="superseded"):
        publication_context.bridge.persist_publication_execution(*records)

    with publication_context.Session() as session:
        after = {
            table: session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in counted_tables
        }
        assert after == before
        assert session.get(GovernedSymbol, symbol_id).catalog_symbol_id is None
        assert session.get(SymbolRevision, revision_id).lifecycle_state == "approved"


def test_execute_publication_handoff_rejects_superseded_approval_before_any_side_effect(
    publication_context,
    monkeypatch,
) -> None:
    symbol_id, revision_id = _seed_symbol(
        publication_context,
        slug="superseded-earliest-handoff",
    )
    review_case_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    action_id = uuid.uuid4()
    with publication_context.Session.begin() as session:
        session.add(
            ReviewCase(
                id=review_case_id,
                source_entity_type="symbol_revision",
                source_entity_id=revision_id,
                current_stage="ready_for_publication_handoff",
                owner_id=publication_context.owner_id,
                escalation_level="medium",
                opened_at=publication_context.now,
                closed_at=None,
            )
        )
        session.flush()
        session.add(
            HumanReviewDecision(
                id=decision_id,
                review_case_id=review_case_id,
                decision_code="approve",
                decision_summary="Approval was later superseded.",
                decision_note=None,
                decided_by=publication_context.owner_id,
                decider_name="Publication Owner",
                decider_role="reviewer",
                from_stage="publication_review",
                to_stage="ready_for_publication_handoff",
                decision_payload_json={"review_case_id": str(review_case_id)},
                created_at=publication_context.now,
                superseded_at=publication_context.now,
            )
        )
        session.flush()
        session.add(
            ReviewCaseAction(
                id=action_id,
                review_case_id=review_case_id,
                decision_id=decision_id,
                action_code="prepare_publication_handoff",
                action_status="pending",
                target_agent_slug="rupert",
                target_stage="publication_staging",
                action_payload_json={"decision_code": "approve"},
                created_by_type="human",
                created_by_id=publication_context.owner_id,
                created_at=publication_context.now,
            )
        )

    durable_tables = (
        "governed_symbols",
        "symbol_revisions",
        "review_cases",
        "human_review_decisions",
        "review_case_actions",
        "review_split_items",
        "publication_approval_targets",
        "agent_queue_items",
        "agent_runs",
        "agent_output_artifacts",
        "publication_packs",
        "publication_jobs",
        "published_pages",
        "pack_entries",
        "catalog_symbol_identifiers",
        "audit_events",
    )
    with publication_context.Session() as session:
        before = {
            table: session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in durable_tables
        }

    external_calls = []
    monkeypatch.setattr(
        publication_handoff,
        "write_rupert_queue_item",
        lambda *_args, **_kwargs: external_calls.append("queue-file"),
    )
    monkeypatch.setattr(
        publication_handoff,
        "run_rupert",
        lambda *_args, **_kwargs: external_calls.append("rupert"),
    )

    with publication_context.Session() as session:
        result = publication_handoff.execute_publication_handoff(
            session,
            review_case_id=review_case_id,
            decision_id=decision_id,
        )

    assert result["status"] == "failed"
    assert "superseded" in result["detail"].lower()
    assert external_calls == []
    with publication_context.Session() as session:
        after = {
            table: session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in durable_tables
        }
        assert after == before
        action = session.get(ReviewCaseAction, action_id)
        assert action.action_status == "pending"
        assert action.started_at is None
        assert action.completed_at is None
        assert action.action_payload_json == {"decision_code": "approve"}
        assert session.get(ReviewCase, review_case_id).current_stage == "ready_for_publication_handoff"
        assert session.get(GovernedSymbol, symbol_id).catalog_symbol_id is None
        assert session.get(SymbolRevision, revision_id).lifecycle_state == "approved"


def test_publication_handoff_serializes_concurrent_decision_supersession_through_last_effect(
    publication_context,
    monkeypatch,
) -> None:
    _symbol_id, revision_id = _seed_symbol(
        publication_context,
        slug="concurrent-supersession-serialization",
    )
    review_case_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    with publication_context.Session.begin() as session:
        session.add(
            ReviewCase(
                id=review_case_id,
                source_entity_type="symbol_revision",
                source_entity_id=revision_id,
                current_stage="ready_for_publication_handoff",
                owner_id=publication_context.owner_id,
                escalation_level="medium",
                opened_at=publication_context.now,
                closed_at=None,
            )
        )
        session.flush()
        session.add(
            HumanReviewDecision(
                id=decision_id,
                review_case_id=review_case_id,
                decision_code="approve",
                decision_summary="Approved before the concurrent supersession probe.",
                decision_note=None,
                decided_by=publication_context.owner_id,
                decider_name="Publication Owner",
                decider_role="reviewer",
                from_stage="publication_review",
                to_stage="ready_for_publication_handoff",
                decision_payload_json={"review_case_id": str(review_case_id)},
                created_at=publication_context.now,
                superseded_at=None,
            )
        )
        revision = session.get(SymbolRevision, revision_id)
        revision.payload_json = {"review_decision_id": str(decision_id)}
        session.add(
            ReviewCaseAction(
                id=uuid.uuid4(),
                review_case_id=review_case_id,
                decision_id=decision_id,
                action_code="prepare_publication_handoff",
                action_status="pending",
                target_agent_slug="rupert",
                target_stage="publication_staging",
                action_payload_json={"decision_code": "approve"},
                created_by_type="human",
                created_by_id=publication_context.owner_id,
                created_at=publication_context.now,
            )
        )

    final_effect_entered = threading.Event()
    release_final_effect = threading.Event()
    supersession_started = threading.Event()
    supersession_finished = threading.Event()
    handoff_result = {}
    contender_pid = {}

    monkeypatch.setattr(publication_handoff, "write_rupert_queue_item", lambda *_args: Path("queue.json"))

    def paused_rupert(_queue_path):
        final_effect_entered.set()
        assert release_final_effect.wait(timeout=10)
        return {"status": "completed"}

    monkeypatch.setattr(publication_handoff, "run_rupert", paused_rupert)

    def run_handoff():
        with publication_context.Session() as session:
            handoff_result.update(
                publication_handoff.execute_publication_handoff(
                    session,
                    review_case_id=review_case_id,
                    decision_id=decision_id,
                )
            )

    def supersede_decision():
        with publication_context.Session() as session:
            contender_pid["value"] = session.execute(text("SELECT pg_backend_pid()" )).scalar_one()
            supersession_started.set()
            session.execute(
                text(
                    "UPDATE human_review_decisions "
                    "SET superseded_at = :superseded_at WHERE id = :decision_id"
                ),
                {"superseded_at": publication_context.now, "decision_id": decision_id},
            )
            session.commit()
        supersession_finished.set()

    handoff_thread = threading.Thread(target=run_handoff)
    handoff_thread.start()
    assert final_effect_entered.wait(timeout=10)

    supersession_thread = threading.Thread(target=supersede_decision)
    supersession_thread.start()
    assert supersession_started.wait(timeout=10)

    deadline = time.monotonic() + 10
    blockers = []
    with publication_context.Session() as observer:
        while time.monotonic() < deadline:
            blockers = observer.execute(
                text("SELECT pg_blocking_pids(:pid)"),
                {"pid": contender_pid["value"]},
            ).scalar_one()
            if blockers:
                break
            if supersession_finished.is_set():
                break
            time.sleep(0.05)

    try:
        assert blockers
        assert not supersession_finished.is_set()
    finally:
        release_final_effect.set()
        handoff_thread.join(timeout=10)
        supersession_thread.join(timeout=10)

    assert not handoff_thread.is_alive()
    assert not supersession_thread.is_alive()
    assert handoff_result["status"] == "completed"
    with publication_context.Session() as session:
        assert session.get(HumanReviewDecision, decision_id).superseded_at is not None


def test_runtime_publication_serializes_supersession_before_authority_validation(
    publication_context,
    monkeypatch,
) -> None:
    _symbol_id, revision_id = _seed_symbol(
        publication_context,
        slug="runtime-concurrent-supersession-serialization",
    )
    records = _publication_handoff(
        publication_context,
        [revision_id],
        pack_code="runtime-concurrent-supersession-pack",
    )
    decision_id = uuid.UUID(records[0]["payload_json"]["review_decision_id"])
    authority_validation_entered = threading.Event()
    release_authority_validation = threading.Event()
    supersession_started = threading.Event()
    supersession_finished = threading.Event()
    contender_pid = {}
    publication_result = {}
    publication_error = {}

    original_resolve_revisions = runtime.resolve_durable_publication_revisions

    def paused_resolve_revisions(*args, **kwargs):
        authority_validation_entered.set()
        assert release_authority_validation.wait(timeout=10)
        return original_resolve_revisions(*args, **kwargs)

    monkeypatch.setattr(runtime, "resolve_durable_publication_revisions", paused_resolve_revisions)

    def publish():
        try:
            publication_result.update(publication_context.bridge.persist_publication_execution(*records))
        except BaseException as exc:  # Surface worker-thread failures in the test assertion below.
            publication_error["value"] = exc

    def supersede_decision():
        with publication_context.Session() as session:
            contender_pid["value"] = session.execute(text("SELECT pg_backend_pid()" )).scalar_one()
            supersession_started.set()
            session.execute(
                text(
                    "UPDATE human_review_decisions "
                    "SET superseded_at = :superseded_at WHERE id = :decision_id"
                ),
                {"superseded_at": publication_context.now, "decision_id": decision_id},
            )
            session.commit()
        supersession_finished.set()

    publish_thread = threading.Thread(target=publish)
    publish_thread.start()
    assert authority_validation_entered.wait(timeout=10)

    supersession_thread = threading.Thread(target=supersede_decision)
    supersession_thread.start()
    assert supersession_started.wait(timeout=10)

    deadline = time.monotonic() + 10
    blockers = []
    with publication_context.Session() as observer:
        while time.monotonic() < deadline:
            blockers = observer.execute(
                text("SELECT pg_blocking_pids(:pid)"),
                {"pid": contender_pid["value"]},
            ).scalar_one()
            if blockers:
                break
            if supersession_finished.is_set():
                break
            time.sleep(0.05)

    try:
        assert blockers
        assert not supersession_finished.is_set()
    finally:
        release_authority_validation.set()
        publish_thread.join(timeout=10)
        supersession_thread.join(timeout=10)

    assert not publish_thread.is_alive()
    assert not supersession_thread.is_alive()
    if publication_error:
        raise publication_error["value"]
    assert publication_result["durable_kind"] == "publication"
    with publication_context.Session() as session:
        assert session.get(HumanReviewDecision, decision_id).superseded_at is not None


def test_publication_rejects_mutated_approved_revision_before_durable_writes(
    publication_context,
) -> None:
    symbol_id, revision_id = _seed_symbol(
        publication_context,
        slug="mutated-approved-revision",
    )
    records = _publication_handoff(
        publication_context,
        [revision_id],
        pack_code="mutated-approved-revision-pack",
    )
    with publication_context.Session.begin() as session:
        revision = session.get(SymbolRevision, revision_id)
        revision.payload_json = {**revision.payload_json, "description": "mutated after approval"}

    with pytest.raises(RuntimeError, match="content identity"):
        publication_context.bridge.persist_publication_execution(*records)

    with publication_context.Session() as session:
        assert session.get(GovernedSymbol, symbol_id).catalog_symbol_id is None
        assert session.get(SymbolRevision, revision_id).lifecycle_state == "approved"
        assert session.query(PublicationJob).filter_by(
            id=runtime.coerce_uuid(f"publication-job:{records[0]['id']}")
        ).one_or_none() is None


def test_publication_approval_target_creation_rejects_same_key_byte_substitution(
    publication_context,
    monkeypatch,
) -> None:
    approved_bytes = b"approved artifact bytes"
    substituted_bytes = b"substituted artifact"
    object_key = f"publication-targets/{uuid.uuid4()}.svg"
    _symbol_id, revision_id = _seed_symbol(
        publication_context,
        slug="target-creation-object-substitution",
    )
    review_case_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    with publication_context.Session.begin() as session:
        revision = session.get(SymbolRevision, revision_id)
        revision.payload_json = {"source_object_key": object_key}
        session.add(
            ReviewCase(
                id=review_case_id,
                source_entity_type="symbol_revision",
                source_entity_id=revision_id,
                current_stage="ready_for_publication_handoff",
                owner_id=publication_context.owner_id,
                escalation_level="medium",
                opened_at=publication_context.now,
                closed_at=None,
            )
        )
        session.flush()
        decision = HumanReviewDecision(
            id=decision_id,
            review_case_id=review_case_id,
            decision_code="approve",
            decision_summary="Approved content identity.",
            decision_note=None,
            decided_by=publication_context.owner_id,
            decider_name="Publication Owner",
            decider_role="reviewer",
            from_stage="review",
            to_stage="ready_for_publication_handoff",
            decision_payload_json={"review_case_id": str(review_case_id)},
            created_at=publication_context.now,
        )
        session.add(decision)
        session.add(
            Attachment(
                id=uuid.uuid4(),
                parent_type="symbol_revision",
                parent_id=revision_id,
                filename="approved.svg",
                object_key=object_key,
                content_type="image/svg+xml",
                size_bytes=len(approved_bytes),
                sha256=hashlib.sha256(approved_bytes).hexdigest(),
                created_at=publication_context.now,
            )
        )
        session.flush()
        monkeypatch.setattr(
            runtime,
            "download_object_bytes",
            lambda **_kwargs: {
                "payload": substituted_bytes,
                "content_type": "image/svg+xml",
                "size_bytes": len(substituted_bytes),
            },
        )

        with pytest.raises(RuntimeError, match="content identity"):
            runtime.ensure_publication_approval_target(
                session,
                review_decision=decision,
                revisions=[revision],
                created_at=publication_context.now,
            )

    with publication_context.Session() as session:
        assert session.query(runtime.PublicationApprovalTarget).filter_by(
            review_decision_id=decision_id
        ).one_or_none() is None


def test_publication_approval_target_rejects_attachment_owned_by_other_revision(
    publication_context,
    monkeypatch,
) -> None:
    target_object_key = "catalog/previews/foreign-owned.png"
    approved_bytes = b"\x89PNG\r\n\x1a\nrevision-owned"
    _, target_revision_id = _seed_symbol(publication_context, slug="target-owner")
    _, foreign_revision_id = _seed_symbol(publication_context, slug="foreign-owner")
    review_case_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    with publication_context.Session.begin() as session:
        revision = session.get(SymbolRevision, target_revision_id)
        revision.payload_json = {"source_object_key": target_object_key}
        review_case = ReviewCase(
            id=review_case_id,
            source_entity_type="symbol_revision",
            source_entity_id=target_revision_id,
            current_stage="ready_for_publication_handoff",
            owner_id=publication_context.owner_id,
            escalation_level="medium",
            opened_at=publication_context.now,
            closed_at=None,
        )
        decision = HumanReviewDecision(
            id=decision_id,
            review_case_id=review_case_id,
            decision_code="approve",
            decision_summary="Approved target revision.",
            decision_note=None,
            decided_by=publication_context.owner_id,
            decider_name="Publication Owner",
            decider_role="reviewer",
            from_stage="review",
            to_stage="ready_for_publication_handoff",
            decision_payload_json={"review_case_id": str(review_case_id)},
            created_at=publication_context.now,
        )
        session.add(review_case)
        session.flush()
        session.add(decision)
        session.add(
            Attachment(
                id=uuid.uuid4(),
                parent_type="symbol_revision",
                parent_id=foreign_revision_id,
                filename="foreign-owned.png",
                object_key=target_object_key,
                content_type="image/png",
                size_bytes=len(approved_bytes),
                sha256=hashlib.sha256(approved_bytes).hexdigest(),
                created_at=publication_context.now,
            )
        )

    with publication_context.Session() as session:
        monkeypatch.setattr(
            runtime,
            "download_object_bytes",
            lambda **_kwargs: {"payload": approved_bytes, "content_type": "image/png"},
        )

        with pytest.raises(RuntimeError, match="revision-owned attachment"):
            runtime.ensure_publication_approval_target(
                session,
                review_decision=session.get(HumanReviewDecision, decision_id),
                revisions=[session.get(SymbolRevision, target_revision_id)],
                created_at=publication_context.now,
            )

        assert session.query(runtime.PublicationApprovalTarget).filter_by(
            review_decision_id=decision_id
        ).one_or_none() is None


def test_duplicate_followup_commit_failure_emits_no_libby_runtime_file(
    monkeypatch,
    tmp_path,
) -> None:
    review_case = SimpleNamespace(id=uuid.uuid4(), current_stage="ready_for_publication_handoff", closed_at=None)
    decision = SimpleNamespace(id=uuid.uuid4(), decided_by=uuid.uuid4())
    action = SimpleNamespace(id=uuid.uuid4(), action_status="running", completed_at=None, action_payload_json={})
    libby = SimpleNamespace(id=uuid.uuid4())

    class Query:
        def filter_by(self, **_kwargs):
            return self

        def one_or_none(self):
            return libby

    class CommitFailingSession:
        def query(self, model):
            assert model is AgentDefinition
            return Query()

        def get(self, model, _item_id):
            assert model is AgentQueueItem
            return None

        def add(self, _item):
            return None

        def commit(self):
            raise RuntimeError("synthetic duplicate queue commit failure")

        def flush(self):
            return None

    monkeypatch.setattr(publication_handoff, "Path", lambda _value: tmp_path)
    monkeypatch.setattr(publication_handoff, "approval_actor_snapshot", lambda _decision: {})
    monkeypatch.setattr(
        publication_handoff,
        "mark_split_item_duplicate_pending_for_decision",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="synthetic duplicate queue commit failure"):
        publication_handoff.queue_libby_duplicate_followup(
            CommitFailingSession(),
            review_case=review_case,
            decision=decision,
            action=action,
            duplicates=[
                {
                    "candidate_revision_id": str(uuid.uuid4()),
                    "matched_symbol_slug": "published-symbol",
                    "hamming_distance": 0,
                    "distance_threshold": 4,
                    "pixel_difference": 0.0,
                    "pixel_difference_threshold": 0.08,
                }
            ],
        )

    assert list(tmp_path.rglob("*.json")) == []


def test_runtime_publication_rejects_same_key_byte_substitution_before_writes(
    publication_context,
    monkeypatch,
) -> None:
    approved_bytes = b"approved runtime artifact bytes"
    substituted_bytes = b"substituted runtime bytes"
    object_key = f"publication-runtime/{uuid.uuid4()}.svg"
    symbol_id, revision_id = _seed_symbol(
        publication_context,
        slug="runtime-object-substitution",
    )
    with publication_context.Session.begin() as session:
        revision = session.get(SymbolRevision, revision_id)
        revision.payload_json = {"source_object_key": object_key}
        session.add(
            Attachment(
                id=uuid.uuid4(),
                parent_type="symbol_revision",
                parent_id=revision_id,
                filename="approved.svg",
                object_key=object_key,
                content_type="image/svg+xml",
                size_bytes=len(approved_bytes),
                sha256=hashlib.sha256(approved_bytes).hexdigest(),
                created_at=publication_context.now,
            )
        )

    current_bytes = {"value": approved_bytes}
    monkeypatch.setattr(
        runtime,
        "download_object_bytes",
        lambda **_kwargs: {
            "payload": current_bytes["value"],
            "content_type": "image/svg+xml",
            "size_bytes": len(current_bytes["value"]),
        },
    )
    records = _publication_handoff(
        publication_context,
        [revision_id],
        pack_code="runtime-object-substitution-pack",
    )
    current_bytes["value"] = substituted_bytes

    with pytest.raises(RuntimeError, match="content identity"):
        publication_context.bridge.persist_publication_execution(*records)

    with publication_context.Session() as session:
        assert session.get(GovernedSymbol, symbol_id).catalog_symbol_id is None
        assert session.get(SymbolRevision, revision_id).lifecycle_state == "approved"
        assert session.query(PublicationJob).filter_by(
            id=runtime.coerce_uuid(f"publication-job:{records[0]['id']}")
        ).one_or_none() is None


def test_publication_approval_byte_identity_accepts_unchanged_current_object(
    publication_context,
    monkeypatch,
) -> None:
    approved_bytes = b"unchanged approved artifact bytes"
    object_key = f"publication-valid/{uuid.uuid4()}.svg"
    symbol_id, revision_id = _seed_symbol(
        publication_context,
        slug="valid-object-content-identity",
    )
    with publication_context.Session.begin() as session:
        revision = session.get(SymbolRevision, revision_id)
        revision.payload_json = {"source_object_key": object_key}
        session.add(
            Attachment(
                id=uuid.uuid4(),
                parent_type="symbol_revision",
                parent_id=revision_id,
                filename="approved.svg",
                object_key=object_key,
                content_type="image/svg+xml",
                size_bytes=len(approved_bytes),
                sha256=hashlib.sha256(approved_bytes).hexdigest(),
                created_at=publication_context.now,
            )
        )

    downloads = []

    def download(**kwargs):
        downloads.append(kwargs)
        return {
            "payload": approved_bytes,
            "content_type": "image/svg+xml",
            "size_bytes": len(approved_bytes),
        }

    monkeypatch.setattr(runtime, "download_object_bytes", download)
    result = _publish(
        publication_context,
        [revision_id],
        pack_code="valid-object-content-identity-pack",
    )

    assert len(downloads) == 2
    assert all(call["object_key"] == object_key for call in downloads)
    assert all(call["max_bytes"] == len(approved_bytes) for call in downloads)
    assert result["durable_kind"] == "publication"
    with publication_context.Session() as session:
        assert session.get(GovernedSymbol, symbol_id).catalog_symbol_id is not None
        assert session.get(SymbolRevision, revision_id).lifecycle_state == "published"


@pytest.mark.parametrize("boundary", ["queue", "artifact"])
def test_publication_rejects_approval_target_mismatch_at_each_runtime_boundary(
    publication_context,
    boundary,
) -> None:
    _symbol_id, revision_id = _seed_symbol(
        publication_context,
        slug=f"approval-target-{boundary}-mismatch",
    )
    records = list(
        _publication_handoff(
            publication_context,
            [revision_id],
            pack_code=f"approval-target-{boundary}-mismatch-pack",
        )
    )
    if boundary == "queue":
        records[0]["payload_json"]["approval_target_id"] = str(uuid.uuid4())
    else:
        records[2]["payload_json"]["approval_content_sha256"] = "0" * 64

    with pytest.raises(RuntimeError, match="approval target"):
        publication_context.bridge.persist_publication_execution(*records)


def test_publication_allocator_uses_governed_symbol_and_keeps_existing_identity(monkeypatch) -> None:
    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id=None)
    allocated_at = datetime(2026, 8, 26, tzinfo=timezone.utc)
    calls = []

    def fake_ensure(session, target_id, *, allocated_at, allocation_source):
        calls.append((session, target_id, allocated_at, allocation_source))
        symbol.catalog_symbol_id = "S-000123"
        return "S-000123"

    monkeypatch.setattr(runtime, "ensure_catalog_symbol_id", fake_ensure)
    session = object()

    assert runtime.allocate_catalog_identity_for_publication(
        session, symbol, allocated_at=allocated_at
    ) == "S-000123"
    assert calls == [(session, symbol_id, allocated_at, "global_sequence")]


def test_publication_allocator_fails_closed_if_allocator_does_not_bind_symbol(monkeypatch) -> None:
    symbol = SimpleNamespace(id=uuid.uuid4(), catalog_symbol_id=None)
    monkeypatch.setattr(runtime, "ensure_catalog_symbol_id", lambda *args, **kwargs: "S-000123")

    with pytest.raises(RuntimeError, match="did not bind"):
        runtime.allocate_catalog_identity_for_publication(
            object(), symbol, allocated_at=datetime.now(timezone.utc)
        )


def test_runtime_publication_transaction_persists_distinct_stable_catalog_identities(
    publication_context,
) -> None:
    first_symbol_id, first_revision_id = _seed_symbol(
        publication_context,
        slug="transaction-pump",
    )

    first_result = _publish(
        publication_context,
        [first_revision_id],
        pack_code="transaction-pack-a",
    )
    with publication_context.Session() as session:
        first_symbol = session.get(GovernedSymbol, first_symbol_id)
        first_identifier = first_symbol.catalog_symbol_id
        assert first_identifier is not None
        assert session.get(CatalogSymbolIdentifier, first_identifier).governed_symbol_id == first_symbol_id
        assert session.get(SymbolRevision, first_revision_id).lifecycle_state == "published"
        assert session.query(PublishedPage).filter_by(
            id=uuid.UUID(first_result["published_pages"][0]["id"])
        ).one().current_symbol_revision_id == first_revision_id
        assert session.query(PackEntry).filter_by(
            id=uuid.UUID(first_result["pack_entries"][0]["id"])
        ).one().symbol_revision_id == first_revision_id

    _publish(publication_context, [first_revision_id], pack_code="transaction-pack-a")
    with publication_context.Session() as session:
        assert session.get(GovernedSymbol, first_symbol_id).catalog_symbol_id == first_identifier

    with publication_context.Session.begin() as session:
        first_symbol = session.get(GovernedSymbol, first_symbol_id)
        first_symbol.slug = "renamed-transaction-pump"
        first_symbol.canonical_name = "Renamed transaction pump"
    _publish(publication_context, [first_revision_id], pack_code="transaction-pack-b")
    with publication_context.Session() as session:
        assert session.get(GovernedSymbol, first_symbol_id).catalog_symbol_id == first_identifier

    later_revision_id = uuid.uuid4()
    with publication_context.Session.begin() as session:
        session.add(
            SymbolRevision(
                id=later_revision_id,
                symbol_id=first_symbol_id,
                revision_label="B",
                lifecycle_state="approved",
                payload_json={},
                author_id=publication_context.owner_id,
                created_at=publication_context.now,
            )
        )
    _publish(publication_context, [later_revision_id], pack_code="transaction-pack-b")

    second_symbol_id, second_revision_id = _seed_symbol(
        publication_context,
        slug="transaction-valve",
    )
    _publish(publication_context, [second_revision_id], pack_code="transaction-pack-b")

    with publication_context.Session() as session:
        first_symbol = session.get(GovernedSymbol, first_symbol_id)
        second_symbol = session.get(GovernedSymbol, second_symbol_id)
        assert first_symbol.catalog_symbol_id == first_identifier
        assert second_symbol.catalog_symbol_id is not None
        assert second_symbol.catalog_symbol_id != first_identifier
        assert session.get(SymbolRevision, later_revision_id).lifecycle_state == "published"
        ordered_events = set(
            session.execute(
                text(
                    "SELECT event FROM symgov_test_publication_order "
                    "WHERE symbol_id = :symbol_id"
                ),
                {"symbol_id": first_symbol_id},
            ).scalars()
        )
        assert {"published_pages", "pack_entries", "symbol_revisions"} <= ordered_events


def test_runtime_publication_transaction_rolls_back_identity_and_all_later_writes(
    publication_context,
) -> None:
    symbol_id, revision_id = _seed_symbol(
        publication_context,
        slug="transaction-rollback",
    )
    records = _publication_handoff(
        publication_context,
        [revision_id],
        pack_code="transaction-rollback-pack",
    )
    queue_id = uuid.UUID(records[0]["id"])
    run_id = uuid.UUID(records[1]["id"])
    artifact_id = uuid.UUID(records[2]["id"])

    with publication_context.Session() as session:
        baseline = {
            "identifiers": session.query(CatalogSymbolIdentifier).count(),
            "packs": session.query(PublicationPack).count(),
            "pages": session.query(PublishedPage).count(),
            "entries": session.query(PackEntry).count(),
            "jobs": session.query(PublicationJob).count(),
            "audits": session.query(AuditEvent).count(),
        }
    with publication_context.Session.begin() as session:
        session.execute(text("INSERT INTO symgov_test_publication_failure (armed) VALUES (true)"))

    try:
        with pytest.raises(DBAPIError, match="synthetic post-publication failure"):
            publication_context.bridge.persist_publication_execution(*records)
    finally:
        with publication_context.Session.begin() as session:
            session.execute(text("DELETE FROM symgov_test_publication_failure"))

    with publication_context.Session() as session:
        symbol = session.get(GovernedSymbol, symbol_id)
        revision = session.get(SymbolRevision, revision_id)
        assert symbol.catalog_symbol_id is None
        assert symbol.current_revision_id is None
        assert revision.lifecycle_state == "approved"
        assert session.query(CatalogSymbolIdentifier).filter_by(
            governed_symbol_id=symbol_id
        ).count() == 0
        assert session.query(PublicationPack).filter_by(
            pack_code="transaction-rollback-pack"
        ).count() == 0
        assert session.get(AgentQueueItem, queue_id) is None
        assert session.get(AgentRun, run_id) is None
        assert session.get(AgentOutputArtifact, artifact_id) is None
        assert {
            "identifiers": session.query(CatalogSymbolIdentifier).count(),
            "packs": session.query(PublicationPack).count(),
            "pages": session.query(PublishedPage).count(),
            "entries": session.query(PackEntry).count(),
            "jobs": session.query(PublicationJob).count(),
            "audits": session.query(AuditEvent).count(),
        } == baseline


def test_runtime_allocates_before_published_page_or_pack_entry_creation() -> None:
    source = RUNTIME.read_text(encoding="utf-8")
    method = source[source.index("    def persist_publication_execution(") :]
    allocation = method.index("allocate_catalog_identity_for_publication(")
    page = method.index("PublishedPage(")
    entry = method.index("PackEntry(")
    lifecycle = method.index('revision.lifecycle_state = "published"')
    assert allocation < page < entry < lifecycle


def test_published_display_identity_is_canonical_and_missing_identity_fails_closed() -> None:
    row = SimpleNamespace(catalog_symbol_id="S-000123")
    assert published_symbol_display_id(row) == "S-000123"

    with pytest.raises(RuntimeError, match="canonical Catalog symbol ID"):
        published_symbol_display_id(SimpleNamespace(catalog_symbol_id=None))


def _reviewer_user(owner_id: uuid.UUID) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=str(owner_id),
        email="publication-owner@example.test",
        display_name="Publication Owner",
        roles=("reviewer",),
        must_change_pin=False,
    )


def test_mounted_rights_writer_waits_for_review_case_authority_lock(publication_context) -> None:
    review_case_id = uuid.uuid4()
    with publication_context.Session.begin() as session:
        session.add(
            ReviewCase(
                id=review_case_id,
                source_entity_type="rights_test",
                source_entity_id=uuid.uuid4(),
                current_stage="provenance_rights_review",
                owner_id=publication_context.owner_id,
                escalation_level="medium",
                opened_at=publication_context.now,
            )
        )

    started = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []

    def run_writer() -> None:
        try:
            with publication_context.Session() as session:
                started.set()
                workspace_routes.create_workspace_rights_review_decision(
                    str(review_case_id),
                    WorkspaceRightsReviewDecisionRequest(
                        decisionCode="clear_rights",
                        evidenceNote="Rights cleared by reviewer.",
                    ),
                    current_user=_reviewer_user(publication_context.owner_id),
                    session=session,
                )
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    with publication_context.Session() as blocker:
        lock_review_case_decision_authority(blocker, review_case_id)
        writer = threading.Thread(target=run_writer)
        writer.start()
        assert started.wait(timeout=10)
        assert not finished.wait(timeout=0.5)
        with publication_context.Session() as observer:
            assert observer.query(HumanReviewDecision).filter_by(review_case_id=review_case_id).count() == 0
        blocker.rollback()

    writer.join(timeout=10)
    assert not writer.is_alive()
    assert errors == []
    with publication_context.Session() as observer:
        assert observer.query(HumanReviewDecision).filter_by(review_case_id=review_case_id).count() == 1


def test_mounted_multi_child_split_uses_one_locked_transaction_and_one_commit(
    publication_context,
    monkeypatch,
) -> None:
    review_case_id = uuid.uuid4()
    report_id = uuid.uuid4()
    queue_id = uuid.uuid4()
    with publication_context.Session.begin() as session:
        rupert = session.query(AgentDefinition).filter_by(slug="rupert").one()
        session.add(
            AgentQueueItem(
                id=queue_id,
                agent_id=rupert.id,
                source_type="test",
                source_id=uuid.uuid4(),
                status="completed",
                priority="medium",
                payload_json={},
                created_at=publication_context.now,
            )
        )
        session.add(
            ValidationReport(
                id=report_id,
                queue_item_id=queue_id,
                source_type="test",
                source_id=uuid.uuid4(),
                validation_status="needs_review",
                defect_count=0,
                normalized_payload_json={
                    "file_name": "parent.png",
                    "derivative_manifest": {
                        "children": [
                            {"child_id": "child-1", "proposed_symbol_id": "SPLIT-1", "file_name": "one.png"},
                            {"child_id": "child-2", "proposed_symbol_id": "SPLIT-2", "file_name": "two.png"},
                        ]
                    },
                },
                report_json={},
                created_at=publication_context.now,
            )
        )
        session.add(
            ReviewCase(
                id=review_case_id,
                source_entity_type="validation_report",
                source_entity_id=report_id,
                current_stage="raster_split_review",
                owner_id=publication_context.owner_id,
                escalation_level="medium",
                opened_at=publication_context.now,
            )
        )

    handoff_commit_flags: list[bool] = []
    monkeypatch.setattr(
        workspace_routes,
        "execute_review_followup_handoff",
        lambda *_args, **kwargs: handoff_commit_flags.append(kwargs.get("commit_transaction", True)) or {"status": "completed"},
    )
    started = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []
    commit_count = 0

    def run_writer() -> None:
        nonlocal commit_count
        try:
            with publication_context.Session() as session:
                original_commit = session.commit

                def _request() -> None:
                    workspace_routes.process_workspace_split_review_decisions(
                        str(review_case_id),
                        WorkspaceSplitReviewProcessRequest(
                            caseComment="Process both children atomically.",
                            childDecisions=[
                                {"childId": "SPLIT-1", "action": "request_changes", "details": "Fix one."},
                                {"childId": "SPLIT-2", "action": "request_changes", "details": "Fix two."},
                            ],
                        ),
                        current_user=_reviewer_user(publication_context.owner_id),
                        session=session,
                    )

                def _record_commit() -> None:
                    nonlocal commit_count
                    commit_count += 1

                def counted_commit() -> None:
                    _record_commit()
                    original_commit()

                session.commit = counted_commit

                started.set()
                _request()
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    with publication_context.Session() as blocker:
        lock_review_case_decision_authority(blocker, review_case_id)
        writer = threading.Thread(target=run_writer)
        writer.start()
        assert started.wait(timeout=10)
        assert not finished.wait(timeout=0.5)
        with publication_context.Session() as observer:
            assert observer.query(ReviewSplitItem).filter_by(review_case_id=review_case_id).count() == 0
            assert observer.query(HumanReviewDecision).filter_by(review_case_id=review_case_id).count() == 0
        blocker.rollback()

    writer.join(timeout=10)
    assert not writer.is_alive()
    assert errors == []
    assert commit_count == 2
    assert handoff_commit_flags == [True, True]
    with publication_context.Session() as observer:
        assert observer.query(ReviewSplitItem).filter_by(review_case_id=review_case_id).count() == 2
        assert observer.query(HumanReviewDecision).filter_by(review_case_id=review_case_id).count() == 2


def test_mounted_split_partial_duplicate_savepoint_preserves_outer_authority_lock(
    publication_context,
    monkeypatch,
) -> None:
    review_case_id = uuid.uuid4()
    report_id = uuid.uuid4()
    queue_id = uuid.uuid4()
    split_item_id = runtime.coerce_uuid(f"review-split-item:{review_case_id}:SPLIT-RACE")
    with publication_context.Session.begin() as session:
        rupert = session.query(AgentDefinition).filter_by(slug="rupert").one()
        session.add(
            AgentQueueItem(
                id=queue_id,
                agent_id=rupert.id,
                source_type="test",
                source_id=uuid.uuid4(),
                status="completed",
                priority="medium",
                payload_json={},
                created_at=publication_context.now,
            )
        )
        session.add(
            ValidationReport(
                id=report_id,
                queue_item_id=queue_id,
                source_type="test",
                source_id=uuid.uuid4(),
                validation_status="needs_review",
                defect_count=0,
                normalized_payload_json={
                    "file_name": "parent.png",
                    "derivative_manifest": {
                        "children": [
                            {"child_id": "child-race", "proposed_symbol_id": "SPLIT-RACE", "file_name": "race.png"},
                            {"child_id": "child-clean", "proposed_symbol_id": "SPLIT-CLEAN", "file_name": "clean.png"},
                        ]
                    },
                },
                report_json={},
                created_at=publication_context.now,
            )
        )
        session.add(
            ReviewCase(
                id=review_case_id,
                source_entity_type="validation_report",
                source_entity_id=report_id,
                current_stage="raster_split_review",
                owner_id=publication_context.owner_id,
                escalation_level="medium",
                opened_at=publication_context.now,
            )
        )

    lookup_entered = threading.Event()
    duplicate_inserted = threading.Event()
    handoff_entered = threading.Event()
    release_handoff = threading.Event()
    lock_contender_started = threading.Event()
    lock_contender_acquired = threading.Event()
    writer_finished = threading.Event()
    errors: list[BaseException] = []
    commit_count = 0

    def inherited_handoff(*_args, **kwargs):
        handoff_entered.set()
        assert release_handoff.wait(timeout=10)
        return {"status": "completed"}

    monkeypatch.setattr(workspace_routes, "execute_review_followup_handoff", inherited_handoff)

    def insert_duplicate() -> None:
        try:
            assert lookup_entered.wait(timeout=10)
            with publication_context.Session.begin() as session:
                session.add(
                    ReviewSplitItem(
                        id=split_item_id,
                        review_case_id=review_case_id,
                        child_key="SPLIT-RACE",
                        proposed_symbol_id="SPLIT-RACE",
                        proposed_symbol_name="Race",
                        file_name="race.png",
                        parent_file_name="parent.png",
                        status="awaiting_decision",
                        payload_json={},
                        created_at=publication_context.now,
                        updated_at=publication_context.now,
                    )
                )
            duplicate_inserted.set()
        except BaseException as exc:
            errors.append(exc)
            duplicate_inserted.set()

    def run_writer() -> None:
        nonlocal commit_count
        try:
            with publication_context.Session() as session:
                original_get = session.get
                original_commit = session.commit

                def racing_get(model, key, *args, **kwargs):
                    result = original_get(model, key, *args, **kwargs)
                    if model is ReviewSplitItem and key == split_item_id and not lookup_entered.is_set():
                        assert result is None
                        lookup_entered.set()
                        assert duplicate_inserted.wait(timeout=10)
                    return result

                session.get = racing_get

                def record_commit() -> None:
                    nonlocal commit_count
                    commit_count += 1

                def counted_commit() -> None:
                    record_commit()
                    original_commit()

                session.commit = counted_commit

                workspace_routes.process_workspace_split_review_decisions(
                    str(review_case_id),
                    WorkspaceSplitReviewProcessRequest(
                        caseComment="Recover the duplicate inside a savepoint.",
                        childDecisions=[
                            {"childId": "SPLIT-RACE", "action": "request_changes", "details": "Fix race."},
                            {"childId": "SPLIT-CLEAN", "action": "request_changes", "details": "Fix clean."},
                        ],
                    ),
                    current_user=_reviewer_user(publication_context.owner_id),
                    session=session,
                )
        except BaseException as exc:
            errors.append(exc)
        finally:
            writer_finished.set()

    def contend_for_authority() -> None:
        try:
            with publication_context.Session() as session:
                lock_contender_started.set()
                lock_review_case_decision_authority(session, review_case_id)
                lock_contender_acquired.set()
                session.rollback()
        except BaseException as exc:
            errors.append(exc)

    duplicate_thread = threading.Thread(target=insert_duplicate)
    writer_thread = threading.Thread(target=run_writer)
    duplicate_thread.start()
    writer_thread.start()
    assert handoff_entered.wait(timeout=10)

    contender_thread = threading.Thread(target=contend_for_authority)
    contender_thread.start()
    assert lock_contender_started.wait(timeout=10)
    assert lock_contender_acquired.wait(timeout=0.5)

    release_handoff.set()
    writer_thread.join(timeout=10)
    duplicate_thread.join(timeout=10)
    contender_thread.join(timeout=10)
    assert not writer_thread.is_alive()
    assert not duplicate_thread.is_alive()
    assert not contender_thread.is_alive()
    assert errors == []
    assert commit_count == 2
    assert lock_contender_acquired.is_set()
    with publication_context.Session() as observer:
        assert observer.query(ReviewSplitItem).filter_by(review_case_id=review_case_id).count() == 2
        assert observer.query(HumanReviewDecision).filter_by(review_case_id=review_case_id).count() == 2
