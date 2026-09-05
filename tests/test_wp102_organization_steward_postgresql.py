"""Stage 10 WP10.2 regression: `organization_steward.run_organization_steward`
against a real disposable PostgreSQL container.

Per the Stage 10 plan
(`docs/plans/2026-09-05-symbol-set-management-stage10-implementation-plan.md`,
WP10.2/§4 Q4/Q6/Q7 and this package's own design round):
- Entirely deterministic -- no LLM call, no mocking of one either, since
  none exists in this code path.
- Five in-scope finding types: `reviewer_coverage_gap`,
  `review_backlog_stale` (14-day threshold), `project_health_issue`,
  `symbol_set_health_issue`, `unresolved_reference`. `icon_generation_missing`
  is deliberately not in the v1 vocabulary at all (schema-level, proven in
  WP10.1's own tests) so it cannot be exercised here.
- Idempotent: re-running with the same still-true condition touches the
  existing finding's `last_seen_at` rather than creating a duplicate row
  (the fingerprint-based upsert, `agent_finding_support.upsert_active_finding`).
- Tenant-scoped: running the Steward for one organization must never create
  or touch a finding whose `agent_config_id` belongs to another organization's
  configuration, proven against real Postgres, not by reasoning alone.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402
from test_wp74_symbol_demotion_postgresql import _add_membership, _create_user_with_global_roles  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.models import (  # noqa: E402
    AgentConfiguration,
    AgentFinding,
    GovernedSymbol,
    OrganizationMemberCapability,
    OrganizationMembership,
    OrganizationSymbolReviewSubmission,
    Project,
    ProjectSymbolSet,
    SymbolRevision,
    SymbolSet,
    SymbolSetItem,
)
from symgov_backend.organization_steward import run_organization_steward  # noqa: E402

NEW_MIGRATION_HEAD = "20260905_0044"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def wp102_database():
    with _database("symgov-wp102") as (engine, url, raw_url):
        _alembic(url, "upgrade", NEW_MIGRATION_HEAD)
        yield engine, url, raw_url


@pytest.fixture()
def wp102_session(wp102_database):
    engine, _, _ = wp102_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return Session


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _seed_org(Session, *, capabilities=()):
    user_id = _create_user_with_global_roles(Session, email=f"wp102-{uuid.uuid4().hex[:8]}@example.test", display_name=f"WP10.2 User {uuid.uuid4().hex[:8]}", roles=[])
    organization_id = _add_membership(Session, user_id, code=f"wp102{uuid.uuid4().hex[:6]}", base_role="admin", capabilities=capabilities)
    return user_id, organization_id


def _seed_config(Session, organization_id):
    now = _now()
    config = AgentConfiguration(
        logical_agent_name="organization_steward", scope_type="organization", scope_id=organization_id,
        enabled=True, created_at=now, updated_at=now,
    )
    with Session() as session:
        session.add(config)
        session.commit()
        session.refresh(config)
        return config


def _create_governed_symbol(session, *, owner_id, organization_id):
    now = _now()
    symbol_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    symbol = GovernedSymbol(
        id=symbol_id, slug=f"wp102-symbol-{uuid.uuid4().hex[:8]}", canonical_name="WP10.2 Test Symbol",
        category="fire", discipline="fire-safety", owner_id=owner_id, owner_organization_id=organization_id,
        visibility="organization_private", organization_wide=False, current_revision_id=None,
        created_at=now, updated_at=now,
    )
    session.add(symbol)
    session.flush()
    session.add(SymbolRevision(id=revision_id, symbol_id=symbol_id, revision_label="1", lifecycle_state="approved", payload_json={}, author_id=owner_id, created_at=now))
    session.flush()
    symbol.current_revision_id = revision_id
    session.flush()
    return symbol_id, revision_id


def _create_review_submission(session, *, organization_id, owner_id, submitted_at, status="active"):
    symbol_id, revision_id = _create_governed_symbol(session, owner_id=owner_id, organization_id=organization_id)
    submission = OrganizationSymbolReviewSubmission(
        id=uuid.uuid4(), organization_id=organization_id, governed_symbol_id=symbol_id, symbol_revision_id=revision_id,
        submitted_by_user_id=owner_id, status=status, submitted_at=submitted_at,
        closed_at=submitted_at if status == "closed" else None,
    )
    session.add(submission)
    session.flush()
    return submission


def _create_project(session, *, organization_id, owner_id):
    now = _now()
    project = Project(
        id=uuid.uuid4(), organization_id=organization_id, code=f"P{uuid.uuid4().hex[:6].upper()}",
        normalized_code=None, name="WP10.2 Project", status="active", created_by_user_id=owner_id,
        created_at=now, updated_at=now,
    )
    project.normalized_code = project.code.lower()
    session.add(project)
    session.flush()
    return project


def _create_symbol_set(session, *, organization_id, owner_id):
    now = _now()
    symbol_set = SymbolSet(
        id=uuid.uuid4(), owner_organization_id=organization_id, code=f"S{uuid.uuid4().hex[:6].upper()}",
        normalized_code=None, name="WP10.2 Symbol Set", status="active", created_by_user_id=owner_id,
        created_at=now, updated_at=now,
    )
    symbol_set.normalized_code = symbol_set.code.lower()
    session.add(symbol_set)
    session.flush()
    return symbol_set


def _attach_project_symbol_set(session, *, project_id, symbol_set_id, owner_id, status="active"):
    now = _now()
    row = ProjectSymbolSet(
        id=uuid.uuid4(), project_id=project_id, symbol_set_id=symbol_set_id, status=status,
        is_default=(status == "active"), created_by_user_id=owner_id, created_at=now, updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def _add_set_item(session, *, symbol_set_id, governed_symbol_id, availability_status="active", availability_reason=None):
    now = _now()
    item = SymbolSetItem(
        id=uuid.uuid4(), symbol_set_id=symbol_set_id, governed_symbol_id=governed_symbol_id, sort_order=0,
        availability_status=availability_status, availability_reason=availability_reason,
        created_at=now, updated_at=now,
    )
    session.add(item)
    session.flush()
    return item


def test_healthy_organization_produces_no_findings(wp102_session):
    owner_id, organization_id = _seed_org(wp102_session, capabilities=("symbol_reviewer",))
    config = _seed_config(wp102_session, organization_id)
    now = _now()
    with wp102_session() as session:
        _create_review_submission(session, organization_id=organization_id, owner_id=owner_id, submitted_at=now, status="active")
        project = _create_project(session, organization_id=organization_id, owner_id=owner_id)
        symbol_set = _create_symbol_set(session, organization_id=organization_id, owner_id=owner_id)
        _attach_project_symbol_set(session, project_id=project.id, symbol_set_id=symbol_set.id, owner_id=owner_id)
        symbol_id, _ = _create_governed_symbol(session, owner_id=owner_id, organization_id=organization_id)
        _add_set_item(session, symbol_set_id=symbol_set.id, governed_symbol_id=symbol_id, availability_status="active")
        session.commit()

        config = session.get(AgentConfiguration, config.id)
        touched = run_organization_steward(session, config, now=now)
        session.commit()

    assert touched == []


def test_reviewer_coverage_gap_detected(wp102_session):
    owner_id, organization_id = _seed_org(wp102_session)  # no symbol_reviewer capability
    config = _seed_config(wp102_session, organization_id)
    now = _now()
    with wp102_session() as session:
        _create_review_submission(session, organization_id=organization_id, owner_id=owner_id, submitted_at=now, status="active")
        session.commit()

        config = session.get(AgentConfiguration, config.id)
        touched = run_organization_steward(session, config, now=now)
        session.commit()

    finding_types = {f.finding_type for f in touched}
    assert "reviewer_coverage_gap" in finding_types
    gap = next(f for f in touched if f.finding_type == "reviewer_coverage_gap")
    assert gap.entity_type == "organization"
    assert gap.entity_id == organization_id
    assert gap.severity == "high"
    assert gap.status == "open"


def test_review_backlog_stale_uses_fourteen_day_threshold(wp102_session):
    owner_id, organization_id = _seed_org(wp102_session, capabilities=("symbol_reviewer",))
    config = _seed_config(wp102_session, organization_id)
    now = _now()
    with wp102_session() as session:
        stale = _create_review_submission(session, organization_id=organization_id, owner_id=owner_id, submitted_at=now - timedelta(days=15), status="active")
        fresh = _create_review_submission(session, organization_id=organization_id, owner_id=owner_id, submitted_at=now - timedelta(days=5), status="active")
        session.commit()

        config = session.get(AgentConfiguration, config.id)
        touched = run_organization_steward(session, config, now=now)
        session.commit()

    backlog_findings = [f for f in touched if f.finding_type == "review_backlog_stale"]
    assert len(backlog_findings) == 1
    assert backlog_findings[0].entity_id == stale.id
    assert backlog_findings[0].entity_id != fresh.id


def test_project_health_issue_detected_when_no_active_symbol_set(wp102_session):
    owner_id, organization_id = _seed_org(wp102_session, capabilities=("symbol_reviewer",))
    config = _seed_config(wp102_session, organization_id)
    now = _now()
    with wp102_session() as session:
        project = _create_project(session, organization_id=organization_id, owner_id=owner_id)
        session.commit()

        config = session.get(AgentConfiguration, config.id)
        touched = run_organization_steward(session, config, now=now)
        session.commit()

    health_findings = [f for f in touched if f.finding_type == "project_health_issue"]
    assert len(health_findings) == 1
    assert health_findings[0].entity_id == project.id


def test_project_with_active_symbol_set_is_healthy(wp102_session):
    owner_id, organization_id = _seed_org(wp102_session, capabilities=("symbol_reviewer",))
    config = _seed_config(wp102_session, organization_id)
    now = _now()
    with wp102_session() as session:
        project = _create_project(session, organization_id=organization_id, owner_id=owner_id)
        symbol_set = _create_symbol_set(session, organization_id=organization_id, owner_id=owner_id)
        _attach_project_symbol_set(session, project_id=project.id, symbol_set_id=symbol_set.id, owner_id=owner_id)
        session.commit()

        config = session.get(AgentConfiguration, config.id)
        touched = run_organization_steward(session, config, now=now)
        session.commit()

    assert not any(f.finding_type == "project_health_issue" for f in touched)


def test_symbol_set_health_issue_detected_when_no_available_items(wp102_session):
    owner_id, organization_id = _seed_org(wp102_session, capabilities=("symbol_reviewer",))
    config = _seed_config(wp102_session, organization_id)
    now = _now()
    with wp102_session() as session:
        symbol_set = _create_symbol_set(session, organization_id=organization_id, owner_id=owner_id)
        session.commit()

        config = session.get(AgentConfiguration, config.id)
        touched = run_organization_steward(session, config, now=now)
        session.commit()

    health_findings = [f for f in touched if f.finding_type == "symbol_set_health_issue"]
    assert len(health_findings) == 1
    assert health_findings[0].entity_id == symbol_set.id


def test_unresolved_reference_detected(wp102_session):
    owner_id, organization_id = _seed_org(wp102_session, capabilities=("symbol_reviewer",))
    config = _seed_config(wp102_session, organization_id)
    now = _now()
    with wp102_session() as session:
        symbol_set = _create_symbol_set(session, organization_id=organization_id, owner_id=owner_id)
        symbol_id, _ = _create_governed_symbol(session, owner_id=owner_id, organization_id=organization_id)
        item = _add_set_item(session, symbol_set_id=symbol_set.id, governed_symbol_id=symbol_id, availability_status="unavailable", availability_reason="Symbol was demoted.")
        session.commit()

        config = session.get(AgentConfiguration, config.id)
        touched = run_organization_steward(session, config, now=now)
        session.commit()

    reference_findings = [f for f in touched if f.finding_type == "unresolved_reference"]
    assert len(reference_findings) == 1
    assert reference_findings[0].entity_id == item.id
    assert reference_findings[0].evidence_json["availability_reason"] == "Symbol was demoted."


def test_rerun_is_idempotent_and_touches_last_seen_at(wp102_session):
    owner_id, organization_id = _seed_org(wp102_session)  # reviewer coverage gap persists across both runs
    config = _seed_config(wp102_session, organization_id)
    first_run_time = _now()
    with wp102_session() as session:
        _create_review_submission(session, organization_id=organization_id, owner_id=owner_id, submitted_at=first_run_time, status="active")
        session.commit()

        config = session.get(AgentConfiguration, config.id)
        first_touched = run_organization_steward(session, config, now=first_run_time)
        session.commit()
        first_finding_id = first_touched[0].id

    second_run_time = first_run_time + timedelta(hours=1)
    with wp102_session() as session:
        config = session.get(AgentConfiguration, config.id)
        second_touched = run_organization_steward(session, config, now=second_run_time)
        session.commit()

    assert len(second_touched) == 1
    assert second_touched[0].id == first_finding_id
    assert second_touched[0].last_seen_at == second_run_time
    assert second_touched[0].first_seen_at == first_run_time

    with wp102_session() as session:
        count = session.query(AgentFinding).filter(AgentFinding.agent_config_id == config.id).count()
    assert count == 1


def test_findings_are_scoped_to_the_running_organization_not_leaked_across_tenants(wp102_session):
    owner_a, organization_a = _seed_org(wp102_session)  # no reviewer -> coverage gap
    owner_b, organization_b = _seed_org(wp102_session, capabilities=("symbol_reviewer",))  # healthy
    config_a = _seed_config(wp102_session, organization_a)
    config_b = _seed_config(wp102_session, organization_b)
    now = _now()
    with wp102_session() as session:
        _create_review_submission(session, organization_id=organization_a, owner_id=owner_a, submitted_at=now, status="active")
        _create_review_submission(session, organization_id=organization_b, owner_id=owner_b, submitted_at=now, status="active")
        session.commit()

        config_a = session.get(AgentConfiguration, config_a.id)
        touched_a = run_organization_steward(session, config_a, now=now)
        config_b = session.get(AgentConfiguration, config_b.id)
        touched_b = run_organization_steward(session, config_b, now=now)
        session.commit()

    assert any(f.finding_type == "reviewer_coverage_gap" for f in touched_a)
    assert touched_b == []

    with wp102_session() as session:
        org_b_findings = session.query(AgentFinding).filter(AgentFinding.agent_config_id == config_b.id).count()
    assert org_b_findings == 0


def test_platform_scoped_config_is_rejected(wp102_session):
    now = _now()
    config = AgentConfiguration(logical_agent_name="organization_steward", scope_type="platform", scope_id=None, enabled=True, created_at=now, updated_at=now)
    with wp102_session() as session:
        with pytest.raises(ValueError):
            run_organization_steward(session, config, now=now)
