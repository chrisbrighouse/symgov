from symgov_backend.models import (
    AuthOrganizationSelectionChallenge,
    Base,
    Organization,
    OrganizationMemberCapability,
    OrganizationMembership,
    OrganizationRoleAssignment,
    PlatformRoleAssignment,
    UserSession,
)


def _constraint_names(table):
    return {constraint.name for constraint in table.constraints if constraint.name}


def test_organization_models_exported_and_named():
    assert Organization.__tablename__ == "organizations"
    assert OrganizationMembership.__tablename__ == "organization_memberships"
    assert OrganizationRoleAssignment.__tablename__ == "organization_role_assignments"
    assert OrganizationMemberCapability.__tablename__ == "organization_member_capabilities"
    assert PlatformRoleAssignment.__tablename__ == "platform_role_assignments"


def test_organization_models_constraints_cover_stage1_invariants():
    organization_constraint_names = _constraint_names(Organization.__table__)
    assert "ck_organizations_code_format" in organization_constraint_names
    assert "ck_organizations_normalized_code_format" in organization_constraint_names
    assert "ck_organizations_status" in organization_constraint_names
    assert "ck_organizations_reserved_identity" in organization_constraint_names

    memberships_constraint_names = _constraint_names(OrganizationMembership.__table__)
    assert "ck_organization_memberships_status" in memberships_constraint_names

    role_constraint_names = _constraint_names(OrganizationRoleAssignment.__table__)
    assert "ck_organization_role_assignments_base_role" in role_constraint_names
    assert "ck_organization_role_assignments_active_revoked" in role_constraint_names

    capability_constraint_names = _constraint_names(OrganizationMemberCapability.__table__)
    assert "ck_organization_member_capabilities_capability" in capability_constraint_names
    assert "ck_organization_member_capabilities_active_revoked" in capability_constraint_names

    platform_constraint_names = _constraint_names(PlatformRoleAssignment.__table__)
    assert "ck_platform_role_assignments_role" in platform_constraint_names
    assert "ck_platform_role_assignments_active_revoked" in platform_constraint_names

    challenge_constraint_names = _constraint_names(AuthOrganizationSelectionChallenge.__table__)
    assert "ck_auth_organization_selection_challenges_token_hash" in challenge_constraint_names
    assert "ck_auth_organization_selection_challenges_eligible_hash" in challenge_constraint_names
    assert "ck_auth_organization_selection_challenges_expiry" in challenge_constraint_names


def test_user_session_stage1_columns_present_with_safe_default_personal_mode():
    columns = UserSession.__table__.columns
    assert "session_mode" in columns
    assert "active_organization_id" in columns
    assert "recent_step_up_at" in columns
    assert columns.session_mode.server_default is not None

    user_session_constraints = _constraint_names(UserSession.__table__)
    assert "ck_user_sessions_mode" in user_session_constraints
    assert "ck_user_sessions_mode_active_org" in user_session_constraints


def test_base_metadata_contains_new_stage1_tables():
    table_names = set(Base.metadata.tables)
    assert "organizations" in table_names
    assert "organization_memberships" in table_names
    assert "organization_role_assignments" in table_names
    assert "organization_member_capabilities" in table_names
    assert "platform_role_assignments" in table_names
    assert "auth_organization_selection_challenges" in table_names


def test_membership_user_foreign_key_preserves_history_on_user_delete():
    foreign_keys = tuple(OrganizationMembership.__table__.columns.user_id.foreign_keys)

    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == "users.id"
    assert foreign_keys[0].ondelete == "RESTRICT"
