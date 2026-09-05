"""Stage 10 WP10.3 regression: `platform_governance.run_platform_governance`
against a real disposable PostgreSQL container.

Per the Stage 10 plan
(`docs/plans/2026-09-05-symbol-set-management-stage10-implementation-plan.md`,
WP10.3/§4 Q4/Q6/Q7):
- Entirely deterministic, reads only existing `Organization`/
  `PlatformRoleAssignment` data -- no new report-generation entity.
- `platform_admin_continuity_risk` fires whenever fewer than two active
  Platform Administrators exist (`severity='high'` at exactly one; the
  `enforce_platform_admin_eligibility` trigger already prevents zero from
  ever occurring in practice, so that branch is not exercised here).
- `duplicate_organization_suspected` fires for every active organization
  sharing an existing active organization's `name_key`, keeping the
  earliest-created organization in each colliding group unflagged.
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
from test_wp74_symbol_demotion_postgresql import _create_user_with_global_roles, _make_platform_admin  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.models import AgentConfiguration, AgentFinding, Organization, OrganizationMembership, OrganizationRoleAssignment, PlatformRoleAssignment  # noqa: E402
from symgov_backend.platform_governance import run_platform_governance  # noqa: E402

NEW_MIGRATION_HEAD = "20260905_0044"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture()
def wp103_database():
    # Function-scoped (not module-scoped like WP10.1/10.2): platform-scoped
    # findings and platform-admin counts are process-wide facts, not
    # per-organization ones, so sharing one database across many test
    # functions here would make each test's continuity-risk count depend on
    # every earlier test in the module -- a fresh container per test avoids
    # that coupling entirely.
    with _database("symgov-wp103") as (engine, url, raw_url):
        _alembic(url, "upgrade", NEW_MIGRATION_HEAD)
        yield engine, url, raw_url


@pytest.fixture()
def wp103_session(wp103_database):
    engine, _, _ = wp103_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return Session


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _add_admin_membership(session, *, organization_id, user_id, now):
    """`enforce_active_organization_admin_minimum` requires every active
    organization to retain at least one active Organization Administrator
    at every commit -- must be added in the same transaction as the
    organization row itself."""
    membership = OrganizationMembership(
        id=uuid.uuid4(), organization_id=organization_id, user_id=user_id, status="active",
        activated_at=now, created_at=now, updated_at=now,
    )
    session.add(membership)
    session.flush()
    session.add(OrganizationRoleAssignment(id=uuid.uuid4(), membership_id=membership.id, base_role="admin", is_active=True, assigned_at=now))


def _seed_platform_config(Session):
    now = _now()
    config = AgentConfiguration(logical_agent_name="platform_governance", scope_type="platform", scope_id=None, enabled=True, created_at=now, updated_at=now)
    with Session() as session:
        session.add(config)
        session.commit()
        session.refresh(config)
        return config


def test_single_platform_admin_is_flagged_as_continuity_risk(wp103_session):
    user_id = _create_user_with_global_roles(wp103_session, email=f"wp103-{uuid.uuid4().hex[:8]}@example.test", display_name="WP10.3 Admin", roles=[])
    _make_platform_admin(wp103_session, user_id)  # exactly one active admin
    config = _seed_platform_config(wp103_session)
    now = _now()

    with wp103_session() as session:
        config = session.get(AgentConfiguration, config.id)
        touched = run_platform_governance(session, config, now=now)
        session.commit()

    continuity_findings = [f for f in touched if f.finding_type == "platform_admin_continuity_risk"]
    assert len(continuity_findings) == 1
    assert continuity_findings[0].severity == "high"
    assert continuity_findings[0].evidence_json["active_platform_admin_count"] == 1


def test_two_platform_admins_is_healthy(wp103_session):
    user_a = _create_user_with_global_roles(wp103_session, email=f"wp103-{uuid.uuid4().hex[:8]}@example.test", display_name="WP10.3 Admin A", roles=[])
    _make_platform_admin(wp103_session, user_a)
    user_b = _create_user_with_global_roles(wp103_session, email=f"wp103-{uuid.uuid4().hex[:8]}@example.test", display_name="WP10.3 Admin B", roles=[])
    _make_platform_admin(wp103_session, user_b)  # also gives user_b symgov-org admin membership, required by enforce_platform_admin_eligibility
    now = _now()

    config = _seed_platform_config(wp103_session)
    with wp103_session() as session:
        config = session.get(AgentConfiguration, config.id)
        touched = run_platform_governance(session, config, now=now)
        session.commit()

    assert not any(f.finding_type == "platform_admin_continuity_risk" for f in touched)


def test_duplicate_organization_name_key_is_flagged(wp103_session):
    user_id = _create_user_with_global_roles(wp103_session, email=f"wp103-{uuid.uuid4().hex[:8]}@example.test", display_name="WP10.3 Admin", roles=[])
    _make_platform_admin(wp103_session, user_id)  # provides a second org (symgov) and its admin, not itself a duplicate target

    now = _now()
    earlier = now - timedelta(days=10)
    with wp103_session() as session:
        first = Organization(
            id=uuid.uuid4(), code="ACME", normalized_code="acme", display_name="Acme Engineering",
            name_key="acme-engineering", entitlement_status="active", is_active=True, is_protected=False,
            fallback_icon_svg="<svg/>", created_at=earlier, updated_at=earlier,
        )
        duplicate = Organization(
            id=uuid.uuid4(), code="ACME2", normalized_code="acme2", display_name="Acme Engineering",
            name_key="acme-engineering", entitlement_status="active", is_active=True, is_protected=False,
            fallback_icon_svg="<svg/>", created_at=now, updated_at=now,
        )
        session.add_all([first, duplicate])
        session.flush()
        _add_admin_membership(session, organization_id=first.id, user_id=user_id, now=earlier)
        _add_admin_membership(session, organization_id=duplicate.id, user_id=user_id, now=now)
        session.commit()
        first_id, duplicate_id = first.id, duplicate.id

    config = _seed_platform_config(wp103_session)
    with wp103_session() as session:
        config = session.get(AgentConfiguration, config.id)
        touched = run_platform_governance(session, config, now=now)
        session.commit()

    duplicate_findings = [f for f in touched if f.finding_type == "duplicate_organization_suspected"]
    assert len(duplicate_findings) == 1
    assert duplicate_findings[0].entity_id == duplicate_id
    assert duplicate_findings[0].entity_id != first_id
    assert duplicate_findings[0].evidence_json["colliding_organization_id"] == str(first_id)


def test_no_duplicate_organizations_is_healthy(wp103_session):
    user_id = _create_user_with_global_roles(wp103_session, email=f"wp103-{uuid.uuid4().hex[:8]}@example.test", display_name="WP10.3 Admin", roles=[])
    _make_platform_admin(wp103_session, user_id)

    now = _now()
    with wp103_session() as session:
        unique_org = Organization(
            id=uuid.uuid4(), code="UNIQUEORG", normalized_code="uniqueorg", display_name="Unique Org",
            name_key="unique-org", entitlement_status="active", is_active=True, is_protected=False,
            fallback_icon_svg="<svg/>", created_at=now, updated_at=now,
        )
        session.add(unique_org)
        session.flush()
        _add_admin_membership(session, organization_id=unique_org.id, user_id=user_id, now=now)
        session.commit()

    config = _seed_platform_config(wp103_session)
    with wp103_session() as session:
        config = session.get(AgentConfiguration, config.id)
        touched = run_platform_governance(session, config, now=now)
        session.commit()

    assert not any(f.finding_type == "duplicate_organization_suspected" for f in touched)


def test_organization_scoped_config_is_rejected(wp103_session):
    now = _now()
    config = AgentConfiguration(logical_agent_name="platform_governance", scope_type="organization", scope_id=uuid.uuid4(), enabled=True, created_at=now, updated_at=now)
    with wp103_session() as session:
        with pytest.raises(ValueError):
            run_platform_governance(session, config, now=now)
