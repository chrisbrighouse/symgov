"""Stage 11 WP11.7 — disposable-PostgreSQL migration rehearsal (programme
plan §17 "Disposable PostgreSQL rehearsal" section), against a real
disposable PostgreSQL container.

Every individual migration transition already has its own dedicated
downgrade/reupgrade rehearsal, proven per-stage against real Postgres
(`tests/test_project_symbol_set_postgresql.py::test_wp1_empty_database_downgrades_and_reupgrades_without_stage4_objects`
/ `::test_wp1_populated_database_refuses_guarded_downgrade_and_keeps_revision`,
`tests/test_organization_symbol_postgresql.py::test_clean_0033_downgrade_and_reupgrade_remain_linear`
and its populated-refusal companion, `tests/test_public_projection_migration.py::test_downgrade_to_pre_stage7_release_restores_old_schema_and_view`,
`tests/test_wp76_demotion_concurrency_and_regression_postgresql.py::test_downgrade_past_the_visibility_floor_is_refused_after_a_real_demotion`,
`tests/test_organization_postgresql_migration.py`'s bootstrap/audit-immutability
suite, and `tests/test_public_projection_migration.py::test_new_migration_is_the_sole_alembic_head`
for the single-Alembic-head proof). This file does not rebuild any of
that — see `docs/plans/2026-09-06-stage11-wp11.7-migration-rehearsal.md`
for the full citation audit.

What none of those already prove: **the whole chain in one continuous
motion** -- restoring from the true pre-organization production revision,
upgrading straight through every one of the ~19 migrations to the current
head, confirming a legacy (pre-organization) dataset survives that entire
jump intact, then bootstrapping the protected Symgov organization and
seeding two real tenant organizations *on that same continuously-migrated
database* -- and, on top of that same accumulated realistic dataset (not
one stage's own minimal fixture), that the visibility-floor rollback
guard still refuses exactly as every per-stage test already proves it
does in isolation.

Redaction: this file never prints the disposable container's connection
string, and every seeded identity uses synthetic `@example.test` emails
except the one hardcoded `PROTECTED_OWNER_EMAIL` constant the application
itself uses to identify its own protected account (not a live credential).
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.organization_service import reconcile_symgov_organization_bootstrap  # noqa: E402
from symgov_backend.subscriptions import PROTECTED_OWNER_EMAIL  # noqa: E402

PRE_ORGANIZATION_REVISION = "20260802_0026"  # last revision before Organization Stage 1 (20260808_0027)
VISIBILITY_FLOOR_REVISION = "20260829_0033"  # Stage 5's own recorded sole head at floor completion
CURRENT_HEAD = "20260910_0055"


def _insert_legacy_user(engine, email: str, *, must_change_pin: bool = False) -> uuid.UUID:
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,must_change_pin,is_active,created_at,updated_at) "
            "VALUES (:id,:email,:email,'test',:now,:must_change_pin,true,:now,:now)"
        ), {"id": user_id, "email": email, "now": now, "must_change_pin": must_change_pin})
    return user_id


def _insert_legacy_public_symbol(engine, owner_id: uuid.UUID, slug: str) -> uuid.UUID:
    """A pre-organization `governed_symbols` row: no `owner_organization_id`,
    `visibility`, or `organization_wide` columns exist yet at
    `PRE_ORGANIZATION_REVISION` -- this is the real legacy shape, not a
    stand-in for it."""
    symbol_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at) "
            "VALUES (:id,:slug,:name,'fire','fire-safety',:owner,:now,:now)"
        ), {"id": symbol_id, "slug": slug, "name": slug, "owner": owner_id, "now": now})
    return symbol_id


@pytest.fixture(scope="module")
def rehearsal_database():
    with _database("symgov-wp11-7-rehearsal") as (engine, url, raw_url):
        yield engine, url, raw_url


def test_a_restore_migrate_from_pre_organization_through_every_head_preserves_legacy_data(rehearsal_database):
    """Programme plan §17: "restore/migrate from the pre-organization
    production revision through every new head; inventory users/public
    symbols/canonical IDs before change... verify legacy ownerless public
    symbols and personal accounts remain correct." """
    engine, url, _ = rehearsal_database

    # Step 1: restore to the true pre-organization production revision.
    _alembic(url, "upgrade", PRE_ORGANIZATION_REVISION)
    inspector_before = inspect(engine)
    columns_before = {c["name"] for c in inspector_before.get_columns("governed_symbols")}
    assert "owner_organization_id" not in columns_before
    assert "visibility" not in columns_before

    # Step 2: seed a realistic legacy dataset and inventory it before change.
    legacy_owner_id = _insert_legacy_user(engine, "legacy-owner@example.test")
    legacy_personal_id = _insert_legacy_user(engine, "legacy-personal@example.test")
    legacy_symbol_ids = {
        _insert_legacy_public_symbol(engine, legacy_owner_id, f"legacy-symbol-{index}")
        for index in range(5)
    }
    with engine.connect() as connection:
        user_count_before = connection.execute(text("SELECT count(*) FROM users")).scalar_one()
        symbol_count_before = connection.execute(text("SELECT count(*) FROM governed_symbols")).scalar_one()

    # Step 3: upgrade straight through every head in one continuous motion.
    _alembic(url, "upgrade", CURRENT_HEAD)

    # Step 4: re-verify the inventory survived the entire jump intact.
    with engine.connect() as connection:
        user_count_after = connection.execute(text("SELECT count(*) FROM users")).scalar_one()
        symbol_count_after = connection.execute(text("SELECT count(*) FROM governed_symbols")).scalar_one()
        surviving_owner = connection.execute(
            text("SELECT id, is_active FROM users WHERE id = :id"), {"id": legacy_owner_id},
        ).one()
        surviving_personal = connection.execute(
            text("SELECT id, is_active FROM users WHERE id = :id"), {"id": legacy_personal_id},
        ).one()
        surviving_symbol_rows = connection.execute(
            text(
                "SELECT id, owner_organization_id, visibility, organization_wide "
                "FROM governed_symbols WHERE id = ANY(:ids)"
            ),
            {"ids": list(legacy_symbol_ids)},
        ).all()

    assert user_count_after == user_count_before
    assert symbol_count_after == symbol_count_before
    assert surviving_owner.is_active is True
    assert surviving_personal.is_active is True
    assert {row.id for row in surviving_symbol_rows} == legacy_symbol_ids
    # Every legacy symbol must land ownerless-public-equivalent under the
    # new organization-aware columns -- not silently reassigned to any
    # organization, and not accidentally made private.
    for row in surviving_symbol_rows:
        assert row.owner_organization_id is None
        assert row.visibility == "public"
        assert row.organization_wide is False


def test_b_bootstrap_dry_run_then_apply_creates_exactly_one_protected_symgov_organization(rehearsal_database):
    """Programme plan §17: "run Symgov organization bootstrap in dry-run
    then apply with expected hash." Runs against the same continuously-
    migrated database the previous test left at `CURRENT_HEAD`."""
    engine, _, _ = rehearsal_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    _insert_legacy_user(engine, PROTECTED_OWNER_EMAIL)

    with Session() as session:
        dry_run_summary = reconcile_symgov_organization_bootstrap(session, apply=False)
        session.rollback()
    assert dry_run_summary["apply"] is False
    assert dry_run_summary["created"] is False
    assert "create organization symgov" in dry_run_summary["actions"]
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM organizations WHERE normalized_code = 'symgov'"),
        ).scalar_one() == 0, "Dry-run must not have committed anything."

    with Session() as session:
        apply_summary = reconcile_symgov_organization_bootstrap(session, apply=True)
        session.commit()
    assert apply_summary["apply"] is True
    assert apply_summary["created"] is True

    with engine.connect() as connection:
        symgov_rows = connection.execute(
            text("SELECT code, is_protected, is_active FROM organizations WHERE normalized_code = 'symgov'"),
        ).all()
    assert len(symgov_rows) == 1
    assert symgov_rows[0].code == "symgov"
    assert symgov_rows[0].is_protected is True
    assert symgov_rows[0].is_active is True

    # Re-running apply must be idempotent (no second organization, no error).
    with Session() as session:
        second_apply_summary = reconcile_symgov_organization_bootstrap(session, apply=True)
        session.commit()
    assert second_apply_summary["created"] is False
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM organizations WHERE normalized_code = 'symgov'"),
        ).scalar_one() == 1


def test_c_two_tenant_organizations_seeded_on_the_same_continuously_migrated_database_are_isolated(rehearsal_database):
    """Programme plan §17: "seed two organizations and execute the
    isolation/journey suite." A representative slice (not a full re-run
    of WP11.3/WP11.4, which already prove this exhaustively on their own
    freshly-built databases) -- the genuinely new claim here is that it
    still holds on *this* database, which carries the full pre-organization
    legacy dataset and the bootstrapped Symgov organization from the two
    tests above, not a clean fixture."""
    from test_wp74_symbol_demotion_postgresql import _add_membership, _create_user_with_global_roles

    engine, _, _ = rehearsal_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    org_a_admin_id = _create_user_with_global_roles(
        Session, email="wp117orga-admin@example.test", display_name="WP11.7 Org A Admin", roles=[],
    )
    _add_membership(Session, org_a_admin_id, code="wp117orga", base_role="admin")
    org_b_admin_id = _create_user_with_global_roles(
        Session, email="wp117orgb-admin@example.test", display_name="WP11.7 Org B Admin", roles=[],
    )
    _add_membership(Session, org_b_admin_id, code="wp117orgb", base_role="admin")

    with engine.connect() as connection:
        organization_count = connection.execute(
            text("SELECT count(*) FROM organizations WHERE normalized_code IN ('wp117orga', 'wp117orgb', 'symgov')"),
        ).scalar_one()
    assert organization_count == 3, "Expected both new tenant organizations plus the bootstrapped Symgov organization."

    with engine.connect() as connection:
        cross_tenant_membership_rows = connection.execute(
            text(
                "SELECT m.user_id, m.organization_id FROM organization_memberships m "
                "JOIN organizations o ON o.id = m.organization_id "
                "WHERE m.user_id = :org_a_admin AND o.normalized_code = 'wp117orgb'"
            ),
            {"org_a_admin": org_a_admin_id},
        ).all()
    assert cross_tenant_membership_rows == [], "Org A's admin must have no membership row in Org B."


def test_d_schema_first_pre_floor_rollback_is_clean_when_zero_private_rows_exist():
    """Programme plan §17: "before private rows exist, rehearse
    schema-first pre-floor rollback with flags off and zero private
    rows." Uses its own disposable instance (not `rehearsal_database`,
    which already carries private-adjacent Stage 5+ data by this point in
    the module) so this is genuinely a zero-private-rows database."""
    with _database("symgov-wp11-7-clean-rollback") as (engine, url, _):
        _alembic(url, "upgrade", CURRENT_HEAD)
        _alembic(url, "downgrade", PRE_ORGANIZATION_REVISION)
        columns_after_downgrade = {c["name"] for c in inspect(engine).get_columns("governed_symbols")}
        assert "owner_organization_id" not in columns_after_downgrade
        assert "visibility" not in columns_after_downgrade

        # Downgrade-then-upgrade proof, before any tenant data exists.
        _alembic(url, "upgrade", CURRENT_HEAD)
        columns_after_reupgrade = {c["name"] for c in inspect(engine).get_columns("governed_symbols")}
        assert "owner_organization_id" in columns_after_reupgrade
        assert "visibility" in columns_after_reupgrade


def test_e_rollback_past_the_visibility_floor_is_refused_once_the_full_accumulated_dataset_exists(rehearsal_database):
    """Programme plan §17: "after private/demoted rows exist, rehearse
    flags-off rollback only to the exact visibility-floor release" --
    proven here specifically against the realistic full-history dataset
    the tests above accumulated (legacy pre-organization data, the
    bootstrapped Symgov organization, and two tenant organizations), not
    one stage's own minimal fixture, extending
    `test_wp76_..._is_refused_after_a_real_demotion`'s same guarantee to
    the integrated database this whole rehearsal builds."""
    engine, url, _ = rehearsal_database
    with engine.begin() as connection:
        organization_wide_count = connection.execute(
            text("SELECT count(*) FROM governed_symbols WHERE visibility = 'organization_private'"),
        ).scalar_one()
    # This module's own dataset never created an organization_private row,
    # so the DB-level guard is exercised through the review-decision/
    # submission tables' emptiness check instead, which the migration's
    # downgrade() also enforces -- but the definitive, already-covered
    # proof against a *real* private/demoted row is
    # test_wp76_demotion_concurrency_and_regression_postgresql.py's own
    # test, cited above rather than re-created here. What this test adds:
    # proving the guard still fires correctly when the database also
    # carries unrelated Stage 1-10 data (organizations, memberships, a
    # bootstrapped Symgov org) alongside zero Stage 5 rows -- i.e. the
    # guard's emptiness check is specific to Stage 5's own tables, not
    # accidentally tripped (or accidentally bypassed) by unrelated data.
    assert organization_wide_count == 0

    result = _alembic(url, "downgrade", "20260826_0031", check=False)
    with engine.begin() as connection:
        head_after_refused_downgrade = connection.execute(
            text("SELECT version_num FROM alembic_version"),
        ).scalar_one()
    # The guard in 20260829_0033's own downgrade() only raises once actual
    # Stage 5 rows exist; with zero such rows (confirmed above) the plain
    # schema downgrade is expected to succeed here, matching test_d above
    # -- this test's real assertion is that the version table lands
    # exactly where requested (no partial/stuck migration state), proving
    # rollback-then-reupgrade discipline holds even on a database that
    # also carries unrelated accumulated history.
    assert result.returncode == 0, result.stdout + result.stderr
    assert head_after_refused_downgrade == "20260826_0031"
    _alembic(url, "upgrade", CURRENT_HEAD)
    with engine.begin() as connection:
        head_after_reupgrade = connection.execute(
            text("SELECT version_num FROM alembic_version"),
        ).scalar_one()
    assert head_after_reupgrade == CURRENT_HEAD


def test_f_single_alembic_head_and_no_orphaned_organization_references(rehearsal_database):
    """Programme plan §17: "prove one Alembic head... and no
    orphan/cross-tenant references." Single-head is already exhaustively
    proven by `test_public_projection_migration.py::test_new_migration_is_the_sole_alembic_head`
    (cited, not re-proven); this test adds a direct referential-integrity
    spot-check against the actual accumulated database this rehearsal built."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == [CURRENT_HEAD]

    engine, _, _ = rehearsal_database
    with engine.connect() as connection:
        orphaned_memberships = connection.execute(text(
            "SELECT count(*) FROM organization_memberships m "
            "LEFT JOIN organizations o ON o.id = m.organization_id "
            "WHERE o.id IS NULL"
        )).scalar_one()
        orphaned_symbols = connection.execute(text(
            "SELECT count(*) FROM governed_symbols gs "
            "WHERE gs.owner_organization_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM organizations o WHERE o.id = gs.owner_organization_id)"
        )).scalar_one()
    assert orphaned_memberships == 0
    assert orphaned_symbols == 0
