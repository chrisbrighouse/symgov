from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from symgov_backend.organization_service import (
    add_protected_organization_member,
    assign_platform_admin,
    create_organization_with_initial_admin,
    deactivate_membership,
    get_platform_admin_detail,
    list_memberships_for_login_choice,
    list_organizations,
    list_platform_admins,
    reactivate_organization,
    reactivate_membership,
    finalize_organization_icon_upload,
    grant_member_capability,
    remove_organization_icon,
    revoke_platform_admin,
    replace_membership_base_role,
    suspend_organization,
    update_organization,
)
from symgov_backend.models import (
    Organization,
    OrganizationMembership,
    OrganizationRoleAssignment,
    PlatformRoleAssignment,
    OrganizationMemberCapability,
    ProductUsageEvent,
    User,
    UserRole,
    UserSession,
)


@pytest.fixture(autouse=True)
def _stub_emit_audit():
    """AuditEvent uses JSONB (PostgreSQL-only); the sqlite fixtures here don't create
    that table. Stub _emit_audit by default, matching the API-level test convention."""
    with patch("symgov_backend.organization_service._emit_audit"):
        yield


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
        UserSession.__table__,
        OrganizationMemberCapability.__table__,
        ProductUsageEvent.__table__,
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


def _seed_user(session, *, email: str, display_name: str | None = None) -> User:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    user = User(
        id=uuid.uuid4(),
        email=email,
        display_name=display_name or email,
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


def _seed_platform_admin_actor(session, *, email: str = "platform@example.test") -> User:
    actor = _seed_user(session, email=email)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    organization = Organization(
        id=uuid.uuid4(),
        code="symgov",
        normalized_code="symgov",
        display_name="Symgov",
        name_key="symgov",
        is_active=True,
        is_protected=True,
        fallback_icon_svg="<svg/>",
        created_at=now,
        updated_at=now,
    )
    membership = OrganizationMembership(
        id=uuid.uuid4(),
        organization_id=organization.id,
        user_id=actor.id,
        status="active",
        activated_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add_all(
        [
            organization,
            membership,
            OrganizationRoleAssignment(
                id=uuid.uuid4(),
                membership_id=membership.id,
                base_role="admin",
                is_active=True,
                assigned_at=now,
            ),
            PlatformRoleAssignment(
                id=uuid.uuid4(),
                user_id=actor.id,
                role="platform_admin",
                is_active=True,
                assigned_at=now,
            ),
        ]
    )
    session.flush()
    return actor


def test_create_organization_with_initial_admin_is_atomic_and_normalized():
    Session = _session_factory()
    with Session() as session:
        actor = _seed_platform_admin_actor(session)
        admin = _seed_user(session, email="admin@example.test")
        created = create_organization_with_initial_admin(
            session,
            code="ACME-01",
            display_name="Acme Engineering",
            legal_name="Acme Engineering Limited",
            locale="en-GB",
            initial_admin_user_id=admin.id,
            actor_user_id=actor.id,
        )
        session.commit()

        org = session.get(Organization, created.organization.id)
        assert org is not None
        assert org.code == "ACME-01"
        assert org.normalized_code == "acme-01"
        assert org.name_key == "acme engineering"
        assert org.legal_name_key == "acme engineering limited"
        assert org.fallback_icon_svg is not None
        assert "svg" in org.fallback_icon_svg.lower()

        membership = session.query(OrganizationMembership).filter(OrganizationMembership.user_id == admin.id).one()
        assert membership.status == "active"

        active_admin_role = session.query(OrganizationRoleAssignment).filter(
            OrganizationRoleAssignment.membership_id == membership.id,
            OrganizationRoleAssignment.is_active.is_(True),
        ).one()
        assert active_admin_role.base_role == "admin"


def test_login_membership_selection_is_bounded_deterministic_and_supports_more_than_five_memberships():
    Session = _session_factory()
    with Session() as session:
        actor = _seed_platform_admin_actor(session)
        admin = _seed_user(session, email="selector@example.test")
        for index in range(7):
            result = create_organization_with_initial_admin(
                session,
                code=f"ORG-{index:02d}",
                display_name=f"Organization {index}",
                legal_name=f"Organization {index} Limited",
                locale="en-US",
                initial_admin_user_id=admin.id,
                actor_user_id=actor.id,
            )
        session.commit()

        first = list_memberships_for_login_choice(session, user_id=admin.id, limit=5)
        second = list_memberships_for_login_choice(session, user_id=admin.id, limit=5)

        assert len(first) == 5
        assert [item.organization_code for item in first] == [item.organization_code for item in second]
        assert [item.organization_code for item in first] == sorted(item.organization_code for item in first)


def test_replace_membership_base_role_blocks_last_active_admin():
    Session = _session_factory()
    with Session() as session:
        actor = _seed_platform_admin_actor(session)
        owner = _seed_user(session, email="owner@example.test")
        created = create_organization_with_initial_admin(
            session,
            code="LOCK-01",
            display_name="Lock Org",
            legal_name="Lock Org Ltd",
            locale="en-US",
            initial_admin_user_id=owner.id,
            actor_user_id=actor.id,
        )
        session.commit()

        try:
            replace_membership_base_role(
                session,
                membership_id=created.membership.id,
                new_base_role="user",
                actor_user_id=owner.id,
            )
        except ValueError as exc:
            assert "last active organization admin" in str(exc).lower()
        else:
            raise AssertionError("Expected last-admin protection to fail before write.")

        session.expire_all()
        active_roles = session.query(OrganizationRoleAssignment).filter(
            OrganizationRoleAssignment.membership_id == created.membership.id,
            OrganizationRoleAssignment.is_active.is_(True),
        ).all()
        assert [role.base_role for role in active_roles] == ["admin"]


def test_last_active_admin_guard_ignores_inactive_and_deleted_admin_users():
    Session = _session_factory()
    with Session() as session:
        actor = _seed_platform_admin_actor(session)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        for index, eligibility in enumerate(("inactive", "deleted"), start=1):
            owner = _seed_user(session, email=f"eligible-owner-{index}@example.test")
            ineligible_admin = _seed_user(session, email=f"ineligible-admin-{index}@example.test")
            if eligibility == "inactive":
                ineligible_admin.is_active = False
            else:
                ineligible_admin.deleted_at = now
            created = create_organization_with_initial_admin(
                session,
                code=f"ELIGIBILITY-{index:02d}",
                display_name=f"Eligibility {index}",
                initial_admin_user_id=owner.id,
                actor_user_id=actor.id,
            )
            ineligible_membership = OrganizationMembership(
                id=uuid.uuid4(),
                organization_id=created.organization.id,
                user_id=ineligible_admin.id,
                status="active",
                activated_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add_all(
                [
                    ineligible_membership,
                    OrganizationRoleAssignment(
                        id=uuid.uuid4(),
                        membership_id=ineligible_membership.id,
                        base_role="admin",
                        is_active=True,
                        assigned_at=now,
                    ),
                ]
            )
            session.flush()

            try:
                replace_membership_base_role(
                    session,
                    membership_id=created.membership.id,
                    new_base_role="user",
                    actor_user_id=owner.id,
                )
            except ValueError as exc:
                assert "last active organization admin" in str(exc).lower()
            else:
                raise AssertionError(f"Expected {eligibility} admin user to be excluded from the active-admin count.")


def test_create_fails_before_write_for_ineligible_actor_or_inactive_initial_admin():
    Session = _session_factory()
    with Session() as session:
        ordinary_actor = _seed_user(session, email="ordinary@example.test")
        inactive_admin = _seed_user(session, email="inactive@example.test")
        inactive_admin.is_active = False
        session.flush()

        cases = (
            (ordinary_actor.id, ordinary_actor.id, "platform administrator"),
            (
                _seed_platform_admin_actor(session, email="eligible@example.test").id,
                inactive_admin.id,
                "initial administrator must be an active",
            ),
        )
        for index, (actor_id, admin_id, expected) in enumerate(cases):
            try:
                create_organization_with_initial_admin(
                    session,
                    code=f"FAIL-{index + 1:02d}",
                    display_name="Must Not Exist",
                    initial_admin_user_id=admin_id,
                    actor_user_id=actor_id,
                )
            except ValueError as exc:
                assert expected in str(exc).lower()
            else:
                raise AssertionError("Expected creation eligibility validation to fail.")
        assert session.query(Organization).filter(Organization.normalized_code.like("fail-%")).count() == 0


def test_login_choices_filter_suspended_entitlement_and_validate_bound():
    Session = _session_factory()
    with Session() as session:
        actor = _seed_platform_admin_actor(session)
        member = _seed_user(session, email="member@example.test")
        active = create_organization_with_initial_admin(
            session,
            code="ACTIVE-01",
            display_name="Active Org",
            initial_admin_user_id=member.id,
            actor_user_id=actor.id,
        )
        suspended = create_organization_with_initial_admin(
            session,
            code="SUSPEND-01",
            display_name="Suspended Org",
            initial_admin_user_id=member.id,
            actor_user_id=actor.id,
        )
        suspended.organization.entitlement_status = "suspended"
        session.commit()

        choices = list_memberships_for_login_choice(session, user_id=member.id, limit=100)
        assert [choice.organization_id for choice in choices] == [active.organization.id]
        for invalid_limit in (0, 101):
            try:
                list_memberships_for_login_choice(session, user_id=member.id, limit=invalid_limit)
            except ValueError as exc:
                assert "limit" in str(exc).lower()
            else:
                raise AssertionError("Expected bounded membership limit validation.")


def test_normalized_name_duplicates_warn_but_distinct_codes_remain_allowed():
    Session = _session_factory()
    with Session() as session:
        actor = _seed_platform_admin_actor(session)
        admin = _seed_user(session, email="duplicate-name-admin@example.test")
        first = create_organization_with_initial_admin(
            session,
            code="ENTITY-ONE",
            display_name="Shared   Entity",
            legal_name="Shared Legal Name",
            initial_admin_user_id=admin.id,
            actor_user_id=actor.id,
        )
        second = create_organization_with_initial_admin(
            session,
            code="ENTITY-TWO",
            display_name=" shared entity ",
            legal_name="SHARED LEGAL NAME",
            initial_admin_user_id=admin.id,
            actor_user_id=actor.id,
        )

        assert first.duplicate_warnings == ()
        assert second.organization.normalized_code == "entity-two"
        assert len(second.duplicate_warnings) == 1
        assert second.duplicate_warnings[0].organization_id == first.organization.id
        assert second.duplicate_warnings[0].organization_code == "ENTITY-ONE"
        assert second.duplicate_warnings[0].matched_fields == ("display_name", "legal_name")
        assert session.query(Organization).count() == 3  # protected Symgov plus both entities


def test_platform_admin_assignment_requires_symgov_admin_and_last_revoke_is_blocked():
    Session = _session_factory()
    with Session() as session:
        actor = _seed_platform_admin_actor(session)
        candidate = _seed_user(session, email="candidate@example.test")

        try:
            assign_platform_admin(session, user_id=candidate.id, actor_user_id=actor.id)
        except ValueError as exc:
            assert "symgov organization admin" in str(exc).lower()
        else:
            raise AssertionError("Expected platform-admin eligibility validation.")

        try:
            revoke_platform_admin(session, user_id=actor.id, actor_user_id=actor.id)
        except ValueError as exc:
            assert "last eligible platform administrator" in str(exc).lower()
        else:
            raise AssertionError("Expected last eligible platform-admin protection.")


def test_symgov_admin_with_active_platform_role_cannot_be_demoted_or_deactivated():
    Session = _session_factory()
    with Session() as session:
        actor = _seed_platform_admin_actor(session)
        symgov_membership = session.query(OrganizationMembership).filter(
            OrganizationMembership.user_id == actor.id
        ).one()

        operations = (
            lambda: replace_membership_base_role(
                session,
                membership_id=symgov_membership.id,
                new_base_role="user",
                actor_user_id=actor.id,
            ),
            lambda: deactivate_membership(
                session,
                membership_id=symgov_membership.id,
                actor_user_id=actor.id,
            ),
        )
        for operation in operations:
            try:
                operation()
            except ValueError as exc:
                assert "protected symgov organization" in str(exc).lower()
            else:
                raise AssertionError("Expected active Platform Admin eligibility protection.")


def _seed_commercial_org(session, *, code: str, entitlement_status: str = "active") -> Organization:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    org = Organization(
        id=uuid.uuid4(),
        code=code,
        normalized_code=code.lower(),
        display_name=f"{code} Inc",
        name_key=f"{code.lower()} inc",
        entitlement_status=entitlement_status,
        is_active=True,
        is_protected=False,
        fallback_icon_svg="<svg/>",
        created_at=now,
        updated_at=now,
    )
    session.add(org)
    session.flush()
    return org


def test_list_organizations_paginates_and_orders_by_normalized_code():
    Session = _session_factory()
    with Session() as session:
        actor = _seed_platform_admin_actor(session)
        _seed_commercial_org(session, code="ZEBRA")
        _seed_commercial_org(session, code="ACME")
        session.commit()

        page1, total = list_organizations(session, actor_user_id=actor.id, page=1, page_size=2)
        assert total == 3
        assert [o.code for o in page1] == ["ACME", "symgov"]

        page2, total = list_organizations(session, actor_user_id=actor.id, page=2, page_size=2)
        assert total == 3
        assert [o.code for o in page2] == ["ZEBRA"]


def test_list_organizations_requires_effective_platform_admin():
    Session = _session_factory()
    with Session() as session:
        _seed_platform_admin_actor(session)
        non_admin = _seed_user(session, email="not-admin@example.test")
        session.commit()

        try:
            list_organizations(session, actor_user_id=non_admin.id)
        except ValueError as exc:
            assert "platform administrator" in str(exc).lower()
        else:
            raise AssertionError("Expected effective Platform Admin requirement.")


def test_suspend_organization_sets_status_revokes_sessions_and_is_idempotent():
    Session = _session_factory()
    with Session() as session:
        actor = _seed_platform_admin_actor(session)
        member = _seed_user(session, email="member@example.test")
        org = _seed_commercial_org(session, code="ACME")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        session.add(
            UserSession(
                id=uuid.uuid4(),
                auth_user_id=member.id,
                token_hash="hash-1",
                created_at=now,
                expires_at=now,
                session_mode="organization",
                active_organization_id=org.id,
            )
        )
        session.commit()

        result = suspend_organization(session, org.id, actor_user_id=actor.id)
        assert result.entitlement_status == "suspended"

        remaining_active = (
            session.query(UserSession)
            .filter(UserSession.active_organization_id == org.id, UserSession.revoked_at.is_(None))
            .count()
        )
        assert remaining_active == 0

        # Idempotent: suspending an already-suspended organization is a no-op.
        again = suspend_organization(session, org.id, actor_user_id=actor.id)
        assert again.entitlement_status == "suspended"


def test_suspend_protected_symgov_organization_is_rejected():
    Session = _session_factory()
    with Session() as session:
        actor = _seed_platform_admin_actor(session)
        symgov_org = session.query(Organization).filter(Organization.normalized_code == "symgov").one()

        try:
            suspend_organization(session, symgov_org.id, actor_user_id=actor.id)
        except ValueError as exc:
            assert "protected" in str(exc).lower()
        else:
            raise AssertionError("Expected protected-organization suspension rejection.")


def test_suspend_unknown_organization_raises():
    Session = _session_factory()
    with Session() as session:
        actor = _seed_platform_admin_actor(session)

        try:
            suspend_organization(session, uuid.uuid4(), actor_user_id=actor.id)
        except ValueError as exc:
            assert "not found" in str(exc).lower()
        else:
            raise AssertionError("Expected not-found error for unknown organization.")


def test_reactivate_organization_restores_active_status_and_is_idempotent():
    Session = _session_factory()
    with Session() as session:
        actor = _seed_platform_admin_actor(session)
        org = _seed_commercial_org(session, code="ACME", entitlement_status="suspended")
        session.commit()

        result = reactivate_organization(session, org.id, actor_user_id=actor.id)
        assert result.entitlement_status == "active"

        again = reactivate_organization(session, org.id, actor_user_id=actor.id)
        assert again.entitlement_status == "active"


def _seed_symgov_admin_candidate(session, symgov_org_id, *, email):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    user = _seed_user(session, email=email)
    membership = OrganizationMembership(
        id=uuid.uuid4(),
        organization_id=symgov_org_id,
        user_id=user.id,
        status="active",
        activated_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(membership)
    session.flush()
    session.add(
        OrganizationRoleAssignment(
            id=uuid.uuid4(),
            membership_id=membership.id,
            base_role="admin",
            is_active=True,
            assigned_at=now,
        )
    )
    session.flush()
    return user


def test_get_platform_admin_detail_finds_the_most_recently_granted_admin_directly():
    """Regression: grant_admin previously re-fetched via list_platform_admins(page=1,
    page_size=200) ordered by assigned_at ascending and linear-scanned for the user,
    which would miss the just-granted admin once an org has >=200 active admins.
    get_platform_admin_detail must find the exact row without relying on page position."""
    Session = _session_factory()
    with Session() as session:
        actor = _seed_platform_admin_actor(session)
        symgov_org_id = session.query(Organization).filter(
            Organization.normalized_code == "symgov"
        ).one().id

        candidates = [
            _seed_symgov_admin_candidate(session, symgov_org_id, email=f"candidate-{i}@example.test")
            for i in range(3)
        ]
        for candidate in candidates:
            assign_platform_admin(session, user_id=candidate.id, actor_user_id=actor.id)
        session.commit()

        most_recent = candidates[-1]

        # Simulate the old bug's premise: the most recently granted admin is not on
        # a small first page ordered by assigned_at ascending.
        first_page, _total = list_platform_admins(session, page=1, page_size=1)
        assert most_recent.id not in {a.user_id for a in first_page}

        detail = get_platform_admin_detail(session, most_recent.id)
        assert detail is not None
        assert detail.user_id == most_recent.id
        assert detail.user_email == most_recent.email


def test_suspend_and_reactivate_require_effective_platform_admin():
    Session = _session_factory()
    with Session() as session:
        _seed_platform_admin_actor(session)
        non_admin = _seed_user(session, email="not-admin@example.test")
        org = _seed_commercial_org(session, code="ACME")
        session.commit()

        for operation in (
            lambda: suspend_organization(session, org.id, actor_user_id=non_admin.id),
            lambda: reactivate_organization(session, org.id, actor_user_id=non_admin.id),
        ):
            try:
                operation()
            except ValueError as exc:
                assert "platform administrator" in str(exc).lower()
            else:
                raise AssertionError("Expected effective Platform Admin requirement.")


def test_ordinary_mutations_revalidate_live_organization_admin_authority():
    Session = _session_factory()
    with Session() as session:
        platform_actor = _seed_platform_admin_actor(session)
        owner = _seed_user(session, email="owner-live-authority@example.test")
        outsider = _seed_user(session, email="outsider-live-authority@example.test")
        target = _seed_user(session, email="target-live-authority@example.test")
        created = create_organization_with_initial_admin(
            session,
            code="LIVE-AUTH",
            display_name="Live Authority",
            initial_admin_user_id=owner.id,
            actor_user_id=platform_actor.id,
        )
        target_membership = OrganizationMembership(
            id=uuid.uuid4(), organization_id=created.organization.id, user_id=target.id,
            status="active", activated_at=created.membership.activated_at,
            created_at=created.membership.created_at, updated_at=created.membership.updated_at,
        )
        session.add(target_membership)
        session.commit()

        operations = (
            lambda: update_organization(
                session, created.organization.id, actor_user_id=outsider.id,
                display_name="Unauthorized rename",
            ),
            lambda: grant_member_capability(
                session, target_membership.id, capability="contributor",
                actor_user_id=outsider.id, organization_id=created.organization.id,
            ),
            lambda: finalize_organization_icon_upload(
                session, created.organization.id, actor_user_id=outsider.id,
                storage_key="organizations/live-auth/icon.png", content_type="image/png",
            ),
        )
        for operation in operations:
            with pytest.raises(ValueError, match="active administrator"):
                operation()
            session.rollback()


def test_reactivation_preserves_append_only_lifecycle_timestamps():
    Session = _session_factory()
    with Session() as session:
        actor = _seed_platform_admin_actor(session)
        target = _seed_user(session, email="reactivation-target@example.test")
        org = _seed_commercial_org(session, code="REACTIVATE")
        activated_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        deactivated_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
        membership = OrganizationMembership(
            id=uuid.uuid4(), organization_id=org.id, user_id=target.id,
            status="inactive", activated_at=activated_at, deactivated_at=deactivated_at,
            created_at=activated_at, updated_at=deactivated_at,
        )
        session.add(membership)
        session.flush()

        reactivate_membership(
            session, membership_id=membership.id, actor_user_id=actor.id,
            reason="Approved return to the organization",
        )

        assert membership.status == "active"
        assert membership.activated_at == activated_at
        assert membership.deactivated_at == deactivated_at


def test_protected_membership_audit_receives_bounded_reason():
    Session = _session_factory()
    with Session() as session:
        actor = _seed_platform_admin_actor(session)
        target = _seed_user(session, email="protected-target@example.test")
        symgov = session.query(Organization).filter_by(normalized_code="symgov").one()
        with patch("symgov_backend.organization_service._emit_audit") as emit:
            add_protected_organization_member(
                session, symgov.id, user_id=target.id, base_role="user",
                actor_user_id=actor.id, reason="Approved by platform operations",
            )

        membership_audit = next(
            call for call in emit.call_args_list
            if call.kwargs.get("action") == "membership.added"
        )
        assert membership_audit.kwargs["payload"]["reason"] == "Approved by platform operations"
