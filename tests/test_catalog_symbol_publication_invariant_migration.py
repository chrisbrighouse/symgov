from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260826_0031_catalog_symbol_publication_invariant.py"
)
APPROVAL_TARGET_MIGRATION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260826_0032_publication_approval_targets.py"
)


def _compact(value: str) -> str:
    return " ".join(value.lower().split())


def test_0031_is_linear_after_live_0030_head_and_preflights_existing_publications() -> None:
    assert MIGRATION.exists()
    source = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'revision(?:\s*:\s*str)?\s*=\s*"20260826_0031"', source)
    assert re.search(r'down_revision(?:\s*:\s*[^=]+)?\s*=\s*"20260822_0030"', source)
    compact = _compact(source)
    assert "catalog publication invariant preflight failed" in compact
    assert "sr.lifecycle_state = 'published'" in compact
    assert "gs.catalog_symbol_id is null" in compact
    assert "csi.role = 'canonical'" in compact
    assert "csi.governed_symbol_id = gs.id" in compact


def test_0031_defers_publication_completeness_across_every_relevant_table() -> None:
    source = _compact(MIGRATION.read_text(encoding="utf-8"))
    assert "create function validate_catalog_symbol_publication_invariant()" in source
    for table in (
        "symbol_revisions",
        "published_pages",
        "pack_entries",
        "governed_symbols",
        "catalog_symbol_identifiers",
    ):
        assert re.search(
            rf"create constraint trigger \w+ after insert or update on {table} .*?"
            r"deferrable initially deferred .*?"
            r"execute function validate_catalog_symbol_publication_invariant\(\)",
            source,
        ), table
    assert "published_pages" in source
    assert "pack_entries" in source
    assert "matching canonical catalog identifier" in source


def test_0031_downgrade_preserves_identifiers_and_removes_only_its_helpers() -> None:
    source = _compact(MIGRATION.read_text(encoding="utf-8"))
    downgrade = source.partition("def downgrade() -> none:")[2]
    assert downgrade
    assert "lock table symbol_revisions" in downgrade
    assert "catalog publication invariant downgrade refused" in downgrade
    for table in (
        "symbol_revisions",
        "published_pages",
        "pack_entries",
        "governed_symbols",
        "catalog_symbol_identifiers",
    ):
        assert f" on {table}" in downgrade
    assert "drop function if exists validate_catalog_symbol_publication_invariant()" in downgrade
    assert "drop table" not in downgrade
    assert "drop column" not in downgrade
    assert "delete from catalog_symbol_identifiers" not in downgrade


def test_0032_adds_linear_immutable_publication_approval_targets() -> None:
    assert APPROVAL_TARGET_MIGRATION.exists()
    source = _compact(APPROVAL_TARGET_MIGRATION.read_text(encoding="utf-8"))
    assert 'revision: str = "20260826_0032"' in source
    assert 'down_revision: union[str, none] = "20260826_0031"' in source
    assert "create_table( \"publication_approval_targets\"" in source
    for column in (
        "review_decision_id",
        "review_case_id",
        "revision_targets_json",
        "content_sha256",
        "created_at",
    ):
        assert f'\"{column}\"' in source
    assert "unique=true" in source
    assert "jsonb_array_length(revision_targets_json) > 0" in source
    assert "publication approval targets are immutable" in source
    assert "before update or delete on publication_approval_targets" in source


def test_approval_target_orm_metadata_matches_migration_contract() -> None:
    from symgov_backend import models

    model = getattr(models, "PublicationApprovalTarget", None)
    assert model is not None
    assert model.__tablename__ == "publication_approval_targets"
    assert set(model.__table__.columns.keys()) == {
        "id",
        "review_decision_id",
        "review_case_id",
        "revision_targets_json",
        "content_sha256",
        "created_at",
    }
