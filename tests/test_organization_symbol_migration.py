from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import CheckConstraint

import symgov_backend.models as models


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260829_0033_organization_symbol_visibility.py"
)


def _load_migration():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION.name}"
    spec = importlib.util.spec_from_file_location("organization_symbol_visibility_0033", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0033_revision_and_review_models_exist():
    migration = _load_migration()

    assert migration.revision == "20260829_0033"
    assert migration.down_revision == "20260826_0032"
    assert getattr(models, "OrganizationSymbolReviewSubmission", None) is not None
    assert getattr(models, "OrganizationSymbolReviewDecision", None) is not None


def _compact(value: object) -> str:
    return " ".join(str(value).split()).lower()


def test_governed_symbol_visibility_metadata_preserves_legacy_public_defaults():
    table = models.GovernedSymbol.__table__

    assert table.c.owner_organization_id.nullable is True
    assert table.c.owner_organization_id.foreign_keys.pop().target_fullname == "organizations.id"
    assert table.c.visibility.nullable is False
    assert _compact(table.c.visibility.server_default.arg) == "'public'"
    assert table.c.organization_wide.nullable is False
    assert _compact(table.c.organization_wide.server_default.arg) == "false"
    checks = {
        constraint.name: _compact(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert checks["ck_governed_symbols_visibility"] == (
        "visibility in ('organization_private', 'public')"
    )
    assert checks["ck_governed_symbols_organization_wide_scope"] == (
        "not organization_wide or owner_organization_id is not null"
    )
    tenant_index = next(
        index
        for index in table.indexes
        if index.name == "ix_governed_symbols_owner_visibility_organization_wide"
    )
    assert [column.name for column in tenant_index.columns] == [
        "owner_organization_id",
        "visibility",
        "organization_wide",
    ]


def test_review_model_metadata_is_exact_revision_bound_and_append_preserving():
    submission = models.OrganizationSymbolReviewSubmission.__table__
    assert list(submission.columns.keys()) == [
        "id",
        "organization_id",
        "governed_symbol_id",
        "symbol_revision_id",
        "submitted_by_user_id",
        "submitted_at",
        "rationale",
        "status",
        "closed_at",
    ]
    assert {foreign_key.target_fullname for foreign_key in submission.foreign_keys} == {
        "organizations.id",
        "governed_symbols.id",
        "symbol_revisions.id",
        "users.id",
    }
    submission_checks = {
        constraint.name: _compact(constraint.sqltext)
        for constraint in submission.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert submission_checks == {
        "ck_organization_symbol_review_submissions_status": (
            "(status = 'active' and closed_at is null) or "
            "(status = 'closed' and closed_at is not null)"
        ),
        "ck_organization_symbol_review_submissions_rationale": (
            "rationale is null or (btrim(rationale) <> '' and char_length(rationale) <= 2000)"
        ),
    }
    active = next(
        index
        for index in submission.indexes
        if index.name == "uq_organization_symbol_review_submissions_active_revision"
    )
    assert active.unique is True
    assert [column.name for column in active.columns] == ["symbol_revision_id"]
    assert _compact(active.dialect_options["postgresql"]["where"]) == "status = 'active'"

    decision = models.OrganizationSymbolReviewDecision.__table__
    assert list(decision.columns.keys()) == [
        "id",
        "submission_id",
        "organization_id",
        "governed_symbol_id",
        "symbol_revision_id",
        "decided_by_user_id",
        "decision",
        "rationale",
        "decided_at",
    ]
    decision_checks = {
        constraint.name: _compact(constraint.sqltext)
        for constraint in decision.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert decision_checks == {
        "ck_organization_symbol_review_decisions_decision": (
            "decision in ('approved', 'rejected', 'changes_requested')"
        ),
        "ck_organization_symbol_review_decisions_rationale": (
            "rationale is null or (btrim(rationale) <> '' and char_length(rationale) <= 2000)"
        ),
    }


def test_0033_migration_declares_visibility_review_projection_and_guards():
    migration = MIGRATION.read_text(encoding="utf-8")
    compact = _compact(migration)
    required = (
        'op.add_column( "governed_symbols", sa.column("owner_organization_id"',
        'op.add_column( "governed_symbols", sa.column( "visibility"',
        'op.add_column( "governed_symbols", sa.column( "organization_wide"',
        'op.create_table( "organization_symbol_review_submissions"',
        'op.create_table( "organization_symbol_review_decisions"',
        "create constraint trigger trg_organization_symbol_review_submission_binding",
        "create constraint trigger trg_organization_symbol_review_decision_binding",
        "create constraint trigger trg_governed_symbols_organization_review_binding",
        "create constraint trigger trg_symbol_revisions_organization_review_binding",
        "create function serialize_organization_symbol_review_binding() "
        "returns trigger language plpgsql set search_path = pg_catalog, public as $$",
        "create function validate_organization_symbol_review_submission_binding() "
        "returns trigger language plpgsql set search_path = pg_catalog, public, pg_temp as $$",
        "create function validate_organization_symbol_review_decision_binding() "
        "returns trigger language plpgsql set search_path = pg_catalog, public, pg_temp as $$",
        "create function validate_organization_symbol_review_parent_binding() "
        "returns trigger language plpgsql set search_path = pg_catalog, public, pg_temp as $$",
        "create function validate_governed_symbol_organization_wide_eligibility() "
        "returns trigger language plpgsql set search_path = pg_catalog, public, pg_temp as $$",
        "current_submission public.organization_symbol_review_submissions%rowtype",
        "current_symbol public.governed_symbols%rowtype",
        "symgov:stage5:organization-review:governed-symbol:",
        "order by lock_key",
        "pg_catalog.pg_advisory_xact_lock(binding_lock_key)",
        "create trigger trg_organization_symbol_review_submission_serialization",
        "before insert on organization_symbol_review_submissions",
        "create trigger trg_governed_symbols_organization_review_serialization",
        "before update of owner_organization_id on governed_symbols",
        "create trigger trg_symbol_revisions_organization_review_serialization",
        "before update of symbol_id on symbol_revisions",
        "create constraint trigger trg_governed_symbols_organization_wide_eligibility",
        "join public.governed_symbols current_symbol on current_symbol.id = submission.governed_symbol_id",
        "join public.symbol_revisions current_revision on current_revision.id = submission.symbol_revision_id",
        "before update or delete on organization_symbol_review_submissions",
        "before update or delete on organization_symbol_review_decisions",
        "create trigger trg_organization_symbol_review_submissions_immutable_truncate",
        "before truncate on organization_symbol_review_submissions",
        "create trigger trg_organization_symbol_review_decisions_immutable_truncate",
        "before truncate on organization_symbol_review_decisions",
        "for each statement execute function protect_organization_symbol_review_submission_history()",
        "for each statement execute function protect_organization_symbol_review_decision_history()",
        "create view active_public_symbol_projections as",
        "gs.visibility = 'public'",
        "sr.lifecycle_state = 'published'",
        "pack.audience = 'public'",
        "pack.status = 'published'",
        "grant select on active_public_symbol_projections to symgov_app",
        "revoke delete, truncate on organization_symbol_review_submissions",
        "revoke update, delete, truncate on organization_symbol_review_decisions",
        "lock table organization_symbol_review_decisions in access exclusive mode",
        "lock table organization_symbol_review_submissions in access exclusive mode",
        "lock table governed_symbols in access exclusive mode",
        "cannot downgrade organization symbol visibility while stage 5 data exists",
        "drop trigger if exists trg_organization_symbol_review_submissions_immutable_truncate",
        "drop trigger if exists trg_organization_symbol_review_decisions_immutable_truncate",
        "drop function if exists serialize_organization_symbol_review_binding()",
    )
    missing = [fragment for fragment in required if _compact(fragment) not in compact]
    assert not missing, f"missing migration contracts: {missing}"
