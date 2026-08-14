from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from symgov_backend.management import manage_symgov_organization, parse_args
from symgov_backend.organization_service import reconcile_symgov_organization_bootstrap
from symgov_backend.models import (
    Organization,
    OrganizationMembership,
    OrganizationRoleAssignment,
    PlatformRoleAssignment,
    User,
    UserRole,
)
from symgov_backend.settings import SymgovAPISettings


def _session_factory():
    engine = create_engine("sqlite:///:memory:")

    # Filter out PostgreSQL-specific constraints that SQLite doesn't understand
    from sqlalchemy import CheckConstraint
    for table in (
        User.__table__,
        UserRole.__table__,
        Organization.__table__,
        OrganizationMembership.__table__,
        OrganizationRoleAssignment.__table__,
        PlatformRoleAssignment.__table__,
    ):
        original_constraints = table.constraints
        try:
            table.constraints = {
                c for c in table.constraints
                if not (isinstance(c, CheckConstraint) and "~" in str(c.sqltext))
            }
            table.create(engine)
        finally:
            table.constraints = original_constraints
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

def _seed_protected_owner(session) -> User:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    user = User(
        id=uuid.uuid4(),
        email="chris.brighouse@hotmail.co.uk",
        display_name="Chris Brighouse",
        pin_hash="pbkdf2_sha256$260000$c2FsdA==$ZGlnZXN0",
        pin_set_at=now,
        must_change_pin=False,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    session.flush()
    return user


def test_stage1_flags_default_off_and_pilot_codes_are_normalized(monkeypatch):
    monkeypatch.delenv("SYMGOV_ORGANIZATIONS_ENABLED", raising=False)
    monkeypatch.delenv("SYMGOV_ORGANIZATION_ADMIN_ENABLED", raising=False)
    monkeypatch.delenv("SYMGOV_SYMBOL_SETS_ENABLED", raising=False)
    monkeypatch.delenv("SYMGOV_ORGANIZATION_SYMBOLS_ENABLED", raising=False)
    monkeypatch.delenv("SYMGOV_ORGANIZATION_AGENTS_ENABLED", raising=False)
    monkeypatch.setenv("SYMGOV_ORGANIZATION_PILOT_CODES", " ACME-01, acme-01, BETA-2 ")

    settings = SymgovAPISettings()

    assert settings.organizations_enabled is False
    assert settings.organization_admin_enabled is False
    assert settings.symbol_sets_enabled is False
    assert settings.organization_symbols_enabled is False
    assert settings.organization_agents_enabled is False
    assert settings.organization_pilot_codes == ("acme-01", "beta-2")


def test_invalid_organization_pilot_code_fails_closed(monkeypatch):
    monkeypatch.setenv("SYMGOV_ORGANIZATION_PILOT_CODES", "valid-01, bad code")

    try:
        SymgovAPISettings()
    except ValueError as exc:
        assert "pilot" in str(exc).lower()
    else:
        raise AssertionError("Expected invalid organization pilot code to fail closed.")


def test_symgov_bootstrap_audit_mode_is_read_only_and_reports_actions():
    Session = _session_factory()
    with Session() as session:
        _seed_protected_owner(session)
        summary = reconcile_symgov_organization_bootstrap(session, apply=False)
        session.commit()

        assert summary["apply"] is False
        assert summary["created"] is False
        assert summary["changed"] is False
        assert any("create organization symgov" in action for action in summary["actions"])
        assert all("@" not in action for action in summary["actions"])
        assert session.query(Organization).count() == 0


def test_symgov_bootstrap_apply_is_idempotent_and_protects_required_assignments():
    Session = _session_factory()
    with Session() as session:
        owner = _seed_protected_owner(session)
        first = reconcile_symgov_organization_bootstrap(session, apply=True)
        second = reconcile_symgov_organization_bootstrap(session, apply=True)
        session.commit()

        assert first["apply"] is True
        assert second["apply"] is True
        assert session.query(Organization).count() == 1

        org = session.query(Organization).filter(Organization.normalized_code == "symgov").one()
        assert org.code == "symgov"
        assert org.is_protected is True

        membership = session.query(OrganizationMembership).filter(
            OrganizationMembership.organization_id == org.id,
            OrganizationMembership.user_id == owner.id,
            OrganizationMembership.status == "active",
        ).one()

        active_org_admin = session.query(OrganizationRoleAssignment).filter(
            OrganizationRoleAssignment.membership_id == membership.id,
            OrganizationRoleAssignment.is_active.is_(True),
        ).one()
        assert active_org_admin.base_role == "admin"

        active_platform = session.query(PlatformRoleAssignment).filter(
            PlatformRoleAssignment.user_id == owner.id,
            PlatformRoleAssignment.role == "platform_admin",
            PlatformRoleAssignment.is_active.is_(True),
        ).one()
        assert active_platform is not None
        assert second["created"] is False
        assert second["changed"] is False
        assert second["actions"] == []


def test_symgov_bootstrap_audit_reports_inactive_suspended_organization_without_mutation():
    Session = _session_factory()
    with Session() as session:
        _seed_protected_owner(session)
        reconcile_symgov_organization_bootstrap(session, apply=True)
        organization = session.query(Organization).filter(
            Organization.normalized_code == "symgov"
        ).one()
        organization.is_active = False
        organization.entitlement_status = "suspended"
        session.commit()

    with Session() as session:
        summary = manage_symgov_organization(session, apply=False)

    assert summary["changed"] is False
    assert summary["actions"] == [
        "reactivate protected Symgov organization",
        "restore protected Symgov organization entitlement",
    ]
    with Session() as session:
        organization = session.query(Organization).filter(
            Organization.normalized_code == "symgov"
        ).one()
        assert organization.is_active is False
        assert organization.entitlement_status == "suspended"


def test_symgov_bootstrap_apply_restores_active_entitlement_idempotently():
    Session = _session_factory()
    with Session() as session:
        _seed_protected_owner(session)
        reconcile_symgov_organization_bootstrap(session, apply=True)
        organization = session.query(Organization).filter(
            Organization.normalized_code == "symgov"
        ).one()
        organization.is_active = False
        organization.entitlement_status = "suspended"
        session.commit()

    with Session() as session:
        repaired = manage_symgov_organization(session, apply=True)
    with Session() as session:
        repeated = manage_symgov_organization(session, apply=True)
        organization = session.query(Organization).filter(
            Organization.normalized_code == "symgov"
        ).one()

    assert repaired["changed"] is True
    assert repaired["actions"] == [
        "reactivate protected Symgov organization",
        "restore protected Symgov organization entitlement",
    ]
    assert organization.is_active is True
    assert organization.entitlement_status == "active"
    assert repeated["changed"] is False
    assert repeated["actions"] == []


def test_symgov_bootstrap_apply_fails_closed_for_inactive_owner_without_writes():
    Session = _session_factory()
    with Session() as session:
        owner = _seed_protected_owner(session)
        owner.is_active = False
        session.flush()

        try:
            reconcile_symgov_organization_bootstrap(session, apply=True)
        except ValueError as exc:
            assert "active protected owner" in str(exc).lower()
        else:
            raise AssertionError("Expected inactive protected owner to block bootstrap apply.")

        assert session.query(Organization).count() == 0


def test_management_command_defaults_to_audit_and_requires_explicit_apply():
    audit_args = parse_args(["bootstrap-symgov-organization"])
    apply_args = parse_args(["bootstrap-symgov-organization", "--apply"])

    assert audit_args.apply is False
    assert apply_args.apply is True


def test_management_audit_is_read_only_apply_commits_and_output_is_bounded():
    Session = _session_factory()
    with Session() as session:
        _seed_protected_owner(session)
        session.commit()

    with Session() as session:
        audit = manage_symgov_organization(session, apply=False)
    assert audit["apply"] is False
    assert audit["created"] is False
    assert audit["changed"] is False
    assert 0 < len(audit["actions"]) <= 4
    assert all("@" not in action and len(action) <= 80 for action in audit["actions"])
    with Session() as session:
        assert session.query(Organization).count() == 0

    with Session() as session:
        applied = manage_symgov_organization(session, apply=True)
    assert applied["apply"] is True
    assert applied["created"] is True
    with Session() as session:
        assert session.query(Organization).count() == 1

    with Session() as session:
        repeated = manage_symgov_organization(session, apply=True)
    assert repeated["created"] is False
    assert repeated["changed"] is False
    assert repeated["actions"] == []
