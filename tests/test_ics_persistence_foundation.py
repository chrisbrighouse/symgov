"""Contract tests for durable, non-destructive ICS persistence."""
from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint

from symgov_backend.classification_schemes import (
    classification_import_id,
    classification_node_seed_id,
    classification_scheme_seed_id,
    classification_crosswalk_id,
    normalize_classification_node_code,
    normalize_ics_node_code,
)
from symgov_backend.models import ICSDomainCrosswalk, ICSTaxonomyImport

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "backend/alembic/versions/20260915_0058_relax_node_code_grammar.py"
REVIEW_MIGRATION = (
    ROOT / "backend/alembic/versions/20260916_0059_ics_crosswalk_review_disposition.py"
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _migration_module():
    return _load(MIGRATION, "ics_persistence_migration")


def test_migration_chains_from_current_head_and_defines_explicit_records():
    migration = _migration_module()
    source = MIGRATION.read_text(encoding="utf-8")

    assert migration.revision == "20260915_0058"
    assert migration.down_revision == "20260911_0057"
    assert "ics_taxonomy_imports" in source
    assert "ics_domain_crosswalks" in source
    assert "provenance" not in ICSTaxonomyImport.__table__.columns
    assert "crosswalk" not in ICSTaxonomyImport.__table__.columns


def test_import_record_carries_queryable_immutable_source_metadata():
    columns = ICSTaxonomyImport.__table__.columns
    expected = {
        "id", "scheme_id", "dataset", "edition", "publication_year",
        "source_update_year", "source_url", "page_url", "browse_url",
        "license_url", "license_code", "attribution", "clarification",
        "limitation", "retrieved_at", "last_modified", "content_sha256",
        "source_bytes", "created_by_user_id", "created_at",
    }
    assert set(columns.keys()) == expected
    assert all(not columns[name].nullable for name in expected - {"last_modified", "created_by_user_id"})
    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(column.name for column in constraint.columns) == ("scheme_id", "content_sha256")
        for constraint in ICSTaxonomyImport.__table__.constraints
    )


def test_crosswalk_is_explicit_and_links_both_governed_nodes():
    columns = ICSDomainCrosswalk.__table__.columns
    assert set(columns.keys()) == {
        "id", "import_id", "source_scheme_id", "source_node_id",
        "target_scheme_id", "target_node_id", "relation", "review_status",
        "reason", "created_at", "reviewed_by_user_id", "reviewed_at",
        "review_note",
    }
    check_names = {
        constraint.name
        for constraint in ICSDomainCrosswalk.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {
        "ck_ics_domain_crosswalks_relation",
        "ck_ics_domain_crosswalks_review_status",
        "ck_ics_domain_crosswalks_reason",
    } <= check_names


def test_crosswalk_disposition_is_attributable_in_storage():
    """A review decision names its reviewer and its moment, or it is refused.

    The attribution is a check constraint rather than only a service rule,
    because before 20260916_0059 the *only* way to record a disposition was a
    raw `UPDATE ... SET review_status='approved'`, which named nobody.
    """
    columns = ICSDomainCrosswalk.__table__.columns
    assert all(
        columns[name].nullable
        for name in ("reviewed_by_user_id", "reviewed_at", "review_note")
    )
    reviewer = columns["reviewed_by_user_id"]
    assert {key.column.table.name for key in reviewer.foreign_keys} == {"users"}
    assert {key.ondelete for key in reviewer.foreign_keys} == {"SET NULL"}

    check_names = {
        constraint.name
        for constraint in ICSDomainCrosswalk.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {
        "ck_ics_domain_crosswalks_disposition_attributed",
        "ck_ics_domain_crosswalks_review_note",
    } <= check_names


def test_review_migration_chains_from_the_persistence_migration():
    review = _load(REVIEW_MIGRATION, "ics_review_migration")
    source = REVIEW_MIGRATION.read_text(encoding="utf-8")

    assert review.revision == "20260916_0059"
    assert review.down_revision == "20260915_0058"
    # The downgrade must refuse to silently discard recorded attribution.
    assert "Cannot discard recorded crosswalk review attribution" in source
    for column in ("reviewed_by_user_id", "reviewed_at", "review_note"):
        assert column in source


def test_ics_grammar_is_opt_in_and_preserves_leading_zeroes():
    assert normalize_ics_node_code("01") == "01"
    assert normalize_ics_node_code("01.080") == "01.080"
    assert normalize_ics_node_code("13.220.20") == "13.220.20"
    with pytest.raises(ValueError):
        normalize_classification_node_code("13.220")
    for invalid in ("1", "A.B", "29..020", "01.080.001"):
        with pytest.raises(ValueError):
            normalize_ics_node_code(invalid)


def test_snapshot_and_crosswalk_identifiers_are_deterministic():
    scheme_id = classification_scheme_seed_id("ISO-ICS-7")
    digest = "a" * 64
    import_id = classification_import_id(scheme_id, digest)
    source_node_id = classification_node_seed_id("ENGINEERING-DISCIPLINE", "ELECTRICAL")
    target_node_id = classification_node_seed_id("ISO-ICS-7", "29")

    assert import_id == classification_import_id(scheme_id, digest)
    assert classification_crosswalk_id(import_id, source_node_id, target_node_id) == classification_crosswalk_id(
        import_id, source_node_id, target_node_id
    )
    assert import_id != classification_import_id(scheme_id, "b" * 64)


def test_import_model_accepts_distinct_snapshots_without_overwriting_identity():
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    scheme_id = classification_scheme_seed_id("ISO-ICS-7")
    common = dict(
        scheme_id=scheme_id,
        dataset="iso_ics",
        edition=7,
        publication_year=2015,
        source_update_year=2025,
        source_url="https://example.test/ICS.csv",
        page_url="https://example.test/open-data",
        browse_url="https://example.test/browse",
        license_url="https://example.test/license",
        license_code="ODC-By 1.0",
        attribution="attribution",
        clarification="clarification",
        limitation="limitation",
        retrieved_at=now,
        last_modified=None,
        source_bytes=b"snapshot",
        created_by_user_id=None,
        created_at=now,
    )
    first = ICSTaxonomyImport(id=classification_import_id(scheme_id, "a" * 64), content_sha256="a" * 64, **common)
    second = ICSTaxonomyImport(id=classification_import_id(scheme_id, "b" * 64), content_sha256="b" * 64, **common)
    assert first.id != second.id
    assert first.content_sha256 == "a" * 64
    assert second.content_sha256 == "b" * 64
