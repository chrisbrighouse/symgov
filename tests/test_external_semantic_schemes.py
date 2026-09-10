"""Contract cover for SM-P0-03: external semantic schemes and concept mappings.

Scope note: this file is deliberately DB-free. It pins the migration/ORM
storage contract, the seeded scheme definitions, the controlled vocabularies
and the pure validators. Behaviour only a real PostgreSQL server can prove --
the NOT NULL scheme version, the partial unique indexes, check constraints
actually rejecting rows, the seed landing, and downgrade -- lives in
`test_external_semantic_schemes_postgresql.py`.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint

from symgov_backend import concept_external_references as mapping_service
from symgov_backend import external_semantic_schemes as scheme_service
from symgov_backend.concept_external_references import (
    BASES_INSUFFICIENT_FOR_EXACT,
    EXTERNAL_MAPPING_METHODS,
    EXTERNAL_MAPPING_STATUSES,
    EXTERNAL_MAPPING_TRANSITIONS,
    EXTERNAL_MAPPING_TYPES,
    EXTERNAL_MAPPING_VERIFICATION_BASES,
    normalize_external_identifier,
    normalize_external_label,
    normalize_external_mapping_confidence,
    normalize_external_mapping_evidence,
)
from symgov_backend.external_semantic_schemes import (
    CHECKSUM_ALGORITHMS,
    EXTERNAL_SCHEME_STATUS_TRANSITIONS,
    EXTERNAL_SCHEME_STATUSES,
    SEED_EXTERNAL_SEMANTIC_SCHEMES,
    normalize_external_scheme_code,
    normalize_external_scheme_uri,
    normalize_scheme_version_integrity,
)
from symgov_backend.models import (
    ConceptExternalReference,
    ExternalSemanticScheme,
    ExternalSemanticSchemeVersion,
)
from symgov_backend.symbol_semantic_assignments import SEMANTIC_ASSIGNMENT_METHODS

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "backend" / "alembic" / "versions" / "20260909_0049_external_semantic_schemes.py"
MODEL = ROOT / "backend" / "symgov_backend" / "models" / "schema.py"
EXPORTS = ROOT / "backend" / "symgov_backend" / "models" / "__init__.py"

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)


def _compact(source: str) -> str:
    return " ".join(source.split())


def _assert_fragments(source: str, *fragments: str) -> None:
    compact = _compact(source)
    for fragment in fragments:
        assert _compact(fragment) in compact, f"missing contract fragment: {fragment}"


def _class_source(source: str, name: str) -> str:
    match = re.search(rf"^class {name}\(Base\):.*?(?=^class |\Z)", source, re.MULTILINE | re.DOTALL)
    assert match, f"missing ORM model {name}"
    return match.group(0)


def _checks(model) -> dict[str, str]:
    return {
        constraint.name: _compact(str(constraint.sqltext)).lower()
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }


# --------------------------------------------------------------------------
# Migration storage contract
# --------------------------------------------------------------------------


def test_0049_chains_from_the_semantic_assignment_head():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION.name}"
    migration = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'revision(?:\s*:\s*str)?\s*=\s*"20260909_0049"', migration)
    assert re.search(r'down_revision(?:\s*:\s*[^=]+)?\s*=\s*"20260909_0048"', migration)


def test_0049_creates_scheme_and_version_storage():
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        'op.create_table( "external_semantic_schemes"',
        'sa.Column("scheme_code", sa.Text(), nullable=False)',
        'sa.Column("issuing_body", sa.Text(), nullable=False)',
        'sa.Column("base_uri", sa.Text(), nullable=True)',
        'op.create_index( "uq_external_semantic_schemes_scheme_code", "external_semantic_schemes", ["scheme_code"], unique=True,',
        'op.create_table( "external_semantic_scheme_versions"',
        'sa.Column("version_label", sa.Text(), nullable=False)',
        'sa.Column("release_date", sa.Date(), nullable=True)',
        'sa.Column("checksum", sa.Text(), nullable=True)',
        'sa.Column("etag", sa.Text(), nullable=True)',
        '"uq_external_semantic_scheme_versions_scheme_version_label", "external_semantic_scheme_versions", ["scheme_id", "version_label"], unique=True,',
    )


def test_0049_requires_a_scheme_version_on_every_mapping():
    """Specification section 16.2: no external mapping without a scheme
    version. NOT NULL is what makes the section 3.4 "timeless lookup list"
    failure structurally impossible rather than merely discouraged."""
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        'op.create_table( "concept_external_references"',
        'sa.Column( "scheme_version_id", postgresql.UUID(as_uuid=True), '
        'sa.ForeignKey("external_semantic_scheme_versions.id", ondelete="RESTRICT", '
        'name="fk_concept_external_references_scheme_version_id"), nullable=False,',
    )
    assert ConceptExternalReference.__table__.columns["scheme_version_id"].nullable is False


def test_0049_keeps_the_external_identifier_off_the_concept():
    """Specification section 7.5 and principle P-04: an external identifier is
    a mapping, never the SymGov concept's key. The upgrade must not add one to
    semantic_concepts."""
    migration = MIGRATION.read_text(encoding="utf-8")
    upgrade = migration[migration.index("def upgrade()"):migration.index("def downgrade()")]
    assert "semantic_concepts" in upgrade  # only as the mapping's foreign key target
    assert 'op.add_column("semantic_concepts"' not in _compact(upgrade)
    assert "external_identifier" not in {
        column.name for column in mapping_service.SemanticConcept.__table__.columns
    }


def test_0049_names_the_long_foreign_keys_explicitly():
    """Three convention-generated names would be 72, 68 and 82 characters,
    past PostgreSQL's 63-character identifier limit."""
    migration = MIGRATION.read_text(encoding="utf-8")
    for name in (
        "fk_external_semantic_scheme_versions_scheme_id",
        "fk_external_semantic_scheme_versions_created_by_user_id",
        "fk_concept_external_references_semantic_concept_id",
        "fk_concept_external_references_scheme_version_id",
    ):
        assert f'name="{name}"' in migration
        assert len(name) <= 63


def test_0049_check_constraint_names_are_bare():
    """A pre-prefixed name passed to sa.CheckConstraint inside op.create_table
    is silently double-prefixed and hash-truncated by SQLAlchemy. Every check
    constraint here passes a bare name and lets NAMING_CONVENTION prefix it."""
    migration = MIGRATION.read_text(encoding="utf-8")
    block = migration[migration.index("def upgrade()"):migration.index("def downgrade()")]
    for match in re.finditer(r'sa\.CheckConstraint\((.*?)\n        \)', block, re.DOTALL):
        name = re.search(r'name="([^"]+)"', match.group(1))
        assert name, f"unnamed check constraint: {match.group(1)[:60]}"
        assert not name.group(1).startswith("ck_"), (
            f"check constraint name must be bare, not pre-prefixed: {name.group(1)}"
        )


def test_0049_indexes_the_two_read_paths_the_specification_names():
    """Specification section 14.3."""
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        '"ix_concept_external_references_scheme_version_identifier", "concept_external_references", ["scheme_version_id", "external_identifier"],',
        '"ix_concept_external_references_concept_status", "concept_external_references", ["semantic_concept_id", "mapping_status"],',
    )


def test_0049_constrains_one_verified_exact_mapping_per_release():
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        '"uq_concept_external_references_verified_exact", "concept_external_references", '
        '["semantic_concept_id", "scheme_version_id"], unique=True, '
        "postgresql_where=sa.text(\"mapping_type = 'exact' and mapping_status = 'verified'\"),",
        '"uq_concept_external_references_active_mapping", "concept_external_references", '
        '["semantic_concept_id", "scheme_version_id", "external_identifier"], unique=True, '
        "postgresql_where=sa.text(\"mapping_status in ('proposed', 'verified')\"),",
    )


def test_0049_seeds_the_three_scheme_definitions_and_no_reference_data():
    """Specification section 15.1: seed scheme definitions only. A version row
    or a mapping row in the migration would be bulk import arriving early."""
    migration = MIGRATION.read_text(encoding="utf-8")
    upgrade = migration[migration.index("def upgrade()"):migration.index("def downgrade()")]
    assert upgrade.count("INSERT INTO") == 1
    assert "INSERT INTO external_semantic_schemes" in upgrade
    assert "INSERT INTO external_semantic_scheme_versions" not in upgrade
    assert "INSERT INTO concept_external_references" not in upgrade
    assert "ON CONFLICT DO NOTHING" in upgrade

    for definition in SEED_EXTERNAL_SEMANTIC_SCHEMES:
        assert f"'{definition['scheme_code']}'" in upgrade
        assert str(definition["id"]) in upgrade
        assert f"'{definition['title']}'" in upgrade
        assert f"'{definition['issuing_body']}'" in upgrade
        if definition["base_uri"] is None:
            continue
        assert f"'{definition['base_uri']}'" in upgrade


def test_seed_identifiers_are_reproducible_from_the_scheme_code():
    """uuid5 of a SymGov URN, so every environment agrees on the seed ids
    without a lookup by code."""
    for definition in SEED_EXTERNAL_SEMANTIC_SCHEMES:
        expected = uuid.uuid5(
            uuid.NAMESPACE_URL, f"urn:symgov:external-semantic-scheme:{definition['scheme_code']}"
        )
        assert definition["id"] == expected


def test_seeded_schemes_cover_the_three_the_delivery_plan_names():
    codes = {definition["scheme_code"] for definition in SEED_EXTERNAL_SEMANTIC_SCHEMES}
    assert codes == {"DEXPI-RDL", "ISO15926-RDL-PCA", "CFIHOS-RDL"}
    for definition in SEED_EXTERNAL_SEMANTIC_SCHEMES:
        assert normalize_external_scheme_code(definition["scheme_code"]) == definition["scheme_code"]
        assert normalize_external_scheme_uri(definition["base_uri"], "base URI") == definition["base_uri"]


def test_0049_is_purely_additive():
    migration = MIGRATION.read_text(encoding="utf-8")
    upgrade = migration[migration.index("def upgrade()"):migration.index("def downgrade()")]
    for forbidden in ("op.alter_column(", "op.drop_column(", "op.drop_table(", "op.add_column("):
        assert forbidden not in upgrade, f"upgrade must stay additive: {forbidden}"


def test_0049_downgrade_reverses_every_created_object():
    migration = MIGRATION.read_text(encoding="utf-8")
    downgrade = migration[migration.index("def downgrade()"):]
    for table in (
        "concept_external_references",
        "external_semantic_scheme_versions",
        "external_semantic_schemes",
    ):
        assert f'op.drop_table("{table}")' in downgrade
    for index_name in (
        "uq_concept_external_references_verified_exact",
        "uq_concept_external_references_active_mapping",
        "ix_concept_external_references_concept_status",
        "ix_concept_external_references_scheme_version_identifier",
        "ix_external_semantic_scheme_versions_scheme_status",
        "uq_external_semantic_scheme_versions_scheme_version_label",
        "ix_external_semantic_schemes_status_scheme_code",
        "uq_external_semantic_schemes_scheme_code",
    ):
        assert index_name in downgrade
    # Children before parents, or the RESTRICT foreign keys refuse the drop.
    assert downgrade.index('op.drop_table("concept_external_references")') < downgrade.index(
        'op.drop_table("external_semantic_scheme_versions")'
    ) < downgrade.index('op.drop_table("external_semantic_schemes")')


# --------------------------------------------------------------------------
# ORM mapping parity
# --------------------------------------------------------------------------


def test_scheme_orm_mappings_exist():
    model = MODEL.read_text(encoding="utf-8")
    _assert_fragments(
        _class_source(model, "ExternalSemanticScheme"),
        '__tablename__ = "external_semantic_schemes"',
        "scheme_code: Mapped[str] = mapped_column(Text, nullable=False)",
        "issuing_body: Mapped[str] = mapped_column(Text, nullable=False)",
        "base_uri: Mapped[str | None] = mapped_column(Text, nullable=True)",
    )
    _assert_fragments(
        _class_source(model, "ExternalSemanticSchemeVersion"),
        '__tablename__ = "external_semantic_scheme_versions"',
        "version_label: Mapped[str] = mapped_column(Text, nullable=False)",
        "release_date: Mapped[object | None] = mapped_column(Date, nullable=True)",
        "checksum_algorithm: Mapped[str | None] = mapped_column(Text, nullable=True)",
    )
    _assert_fragments(
        _class_source(model, "ConceptExternalReference"),
        '__tablename__ = "concept_external_references"',
        "external_identifier: Mapped[str] = mapped_column(Text, nullable=False)",
        "external_label: Mapped[str | None] = mapped_column(Text, nullable=True)",
        "mapping_type: Mapped[str] = mapped_column(Text, nullable=False)",
        "confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)",
    )


def test_scheme_orm_metadata_matches_migration_integrity():
    # `_checks` lower-cases the whole expression for comparison, so the stored
    # `[A-Z0-9]` character classes read as `[a-z0-9]` here. The constraint
    # itself is case-sensitive; `test_scheme_code_normalizes_to_upper_case`
    # and the PostgreSQL rehearsal cover that.
    assert _checks(ExternalSemanticScheme) == {
        "ck_external_semantic_schemes_scheme_code": (
            "scheme_code ~ '^[a-z0-9][a-z0-9.-]{0,62}[a-z0-9]$'"
        ),
        "ck_external_semantic_schemes_title": (
            "btrim(title) <> '' and char_length(title) <= 256"
        ),
        "ck_external_semantic_schemes_issuing_body": (
            "btrim(issuing_body) <> '' and char_length(issuing_body) <= 256"
        ),
        "ck_external_semantic_schemes_base_uri": (
            "base_uri is null or (base_uri ~ '^https?://' and char_length(base_uri) <= 1024)"
        ),
        "ck_external_semantic_schemes_status": (
            "status in ('active', 'deprecated', 'withdrawn')"
        ),
    }


def test_scheme_version_orm_metadata_matches_migration_integrity():
    assert _checks(ExternalSemanticSchemeVersion) == {
        "ck_external_semantic_scheme_versions_version_label": (
            "btrim(version_label) <> '' and char_length(version_label) <= 128"
        ),
        "ck_external_semantic_scheme_versions_source_uri": (
            "source_uri is null or (source_uri ~ '^https?://' and char_length(source_uri) <= 1024)"
        ),
        # The `is not null` tests are load-bearing: without them a checksum
        # with a NULL algorithm makes the branch NULL rather than false, and
        # PostgreSQL accepts a check constraint that evaluates to NULL.
        "ck_external_semantic_scheme_versions_checksum_pairing": (
            "(checksum is null and checksum_algorithm is null) or "
            "(checksum is not null and checksum_algorithm is not null "
            "and checksum ~ '^[0-9a-f]{32,128}$' and checksum_algorithm in "
            "('md5', 'sha1', 'sha256', 'sha512'))"
        ),
        "ck_external_semantic_scheme_versions_etag": (
            "etag is null or (btrim(etag) <> '' and char_length(etag) <= 256)"
        ),
        "ck_external_semantic_scheme_versions_integrity_retrieval": (
            "(checksum is null and etag is null) or retrieved_at is not null"
        ),
        "ck_external_semantic_scheme_versions_status": (
            "status in ('active', 'deprecated', 'withdrawn')"
        ),
    }


def test_mapping_orm_metadata_matches_migration_integrity():
    assert _checks(ConceptExternalReference) == {
        "ck_concept_external_references_external_identifier": (
            "btrim(external_identifier) <> '' and char_length(external_identifier) <= 512"
        ),
        "ck_concept_external_references_external_label": (
            "external_label is null or (btrim(external_label) <> '' "
            "and char_length(external_label) <= 512)"
        ),
        "ck_concept_external_references_mapping_type": (
            "mapping_type in ('exact', 'close', 'broader', 'narrower', 'related')"
        ),
        "ck_concept_external_references_mapping_status": (
            "mapping_status in ('proposed', 'verified', 'rejected', 'retired')"
        ),
        "ck_concept_external_references_mapping_method": (
            "mapping_method in ('manual', 'imported', 'rule', 'ai_assisted')"
        ),
        "ck_concept_external_references_confidence": (
            "confidence is null or (confidence >= 0 and confidence <= 1)"
        ),
        "ck_concept_external_references_review_decision": (
            "mapping_status in ('proposed', 'retired') or reviewed_at is not null"
        ),
        "ck_concept_external_references_evidence_json_object": (
            "jsonb_typeof(evidence_json) = 'object'"
        ),
        "ck_concept_external_references_verified_exact_reviewer": (
            "mapping_status <> 'verified' or mapping_type <> 'exact' "
            "or reviewed_by_user_id is not null or mapping_method = 'imported'"
        ),
        "ck_concept_external_references_verified_exact_evidence": (
            "mapping_status <> 'verified' or mapping_type <> 'exact' "
            "or evidence_json <> '{}'::jsonb"
        ),
    }


def test_verified_exact_index_is_partial_on_type_and_status():
    index = next(
        candidate
        for candidate in ConceptExternalReference.__table__.indexes
        if candidate.name == "uq_concept_external_references_verified_exact"
    )
    assert index.unique is True
    assert [column.name for column in index.columns] == ["semantic_concept_id", "scheme_version_id"]
    assert _compact(str(index.dialect_options["postgresql"]["where"])).lower() == (
        "mapping_type = 'exact' and mapping_status = 'verified'"
    )


def test_active_mapping_index_excludes_closed_governance_states():
    """Rejected and retired rows stay out, so a mapping's governance history
    survives alongside whatever replaced it."""
    index = next(
        candidate
        for candidate in ConceptExternalReference.__table__.indexes
        if candidate.name == "uq_concept_external_references_active_mapping"
    )
    assert index.unique is True
    assert [column.name for column in index.columns] == [
        "semantic_concept_id",
        "scheme_version_id",
        "external_identifier",
    ]
    assert _compact(str(index.dialect_options["postgresql"]["where"])).lower() == (
        "mapping_status in ('proposed', 'verified')"
    )


def test_new_models_are_exported():
    exports = EXPORTS.read_text(encoding="utf-8")
    for name in ("ConceptExternalReference", "ExternalSemanticScheme", "ExternalSemanticSchemeVersion"):
        assert re.search(rf"\b{name},", exports)
        assert f'"{name}"' in exports


# --------------------------------------------------------------------------
# Controlled vocabularies
# --------------------------------------------------------------------------


def test_controlled_vocabularies_match_the_specification():
    assert EXTERNAL_MAPPING_TYPES == {"exact", "close", "broader", "narrower", "related"}
    assert EXTERNAL_MAPPING_STATUSES == {"proposed", "verified", "rejected", "retired"}
    assert EXTERNAL_MAPPING_METHODS == {"manual", "imported", "rule", "ai_assisted"}
    assert EXTERNAL_SCHEME_STATUSES == {"active", "deprecated", "withdrawn"}
    assert CHECKSUM_ALGORITHMS == {"md5", "sha1", "sha256", "sha512"}


def test_mapping_method_stays_distinct_from_the_assignment_method():
    """Specification section 7.5 names `imported` where section 7.9 names
    `source_mapping`. The two vocabularies are deliberately not unified: doing
    so would be a specification change, not an implementation tidy-up."""
    assert "imported" in EXTERNAL_MAPPING_METHODS
    assert "imported" not in SEMANTIC_ASSIGNMENT_METHODS
    assert "source_mapping" in SEMANTIC_ASSIGNMENT_METHODS
    assert "source_mapping" not in EXTERNAL_MAPPING_METHODS


def test_mapping_lifecycle_covers_every_status_and_starts_only_at_proposed():
    assert set(EXTERNAL_MAPPING_TRANSITIONS) == EXTERNAL_MAPPING_STATUSES
    for reachable in EXTERNAL_MAPPING_TRANSITIONS.values():
        assert reachable <= EXTERNAL_MAPPING_STATUSES
    assert EXTERNAL_MAPPING_TRANSITIONS["proposed"] == {"verified", "rejected", "retired"}
    assert EXTERNAL_MAPPING_TRANSITIONS["rejected"] == set()
    assert EXTERNAL_MAPPING_TRANSITIONS["retired"] == set()
    assert EXTERNAL_MAPPING_TRANSITIONS["verified"] == {"retired"}


def test_scheme_status_lifecycle_makes_withdrawn_terminal():
    assert set(EXTERNAL_SCHEME_STATUS_TRANSITIONS) == EXTERNAL_SCHEME_STATUSES
    assert EXTERNAL_SCHEME_STATUS_TRANSITIONS["withdrawn"] == set()
    assert "active" in EXTERNAL_SCHEME_STATUS_TRANSITIONS["deprecated"]


def test_string_similarity_is_a_recorded_but_insufficient_basis():
    """Specification section 16.2. The basis is in the vocabulary so a
    similarity-driven decision can be recorded honestly, and excluded for
    `exact` rather than quietly relabelled."""
    assert BASES_INSUFFICIENT_FOR_EXACT <= EXTERNAL_MAPPING_VERIFICATION_BASES
    assert BASES_INSUFFICIENT_FOR_EXACT == {"string_similarity"}
    assert EXTERNAL_MAPPING_VERIFICATION_BASES == {
        "human_review",
        "authoritative_source",
        "string_similarity",
    }


# --------------------------------------------------------------------------
# Pure validators
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["DEXPI-RDL", "dexpi-rdl", " CFIHOS-RDL ", "ISO15926.4"])
def test_scheme_code_normalizes_to_upper_case(value):
    assert normalize_external_scheme_code(value) == value.strip().upper()


@pytest.mark.parametrize("value", ["", "  ", "-DEXPI", "DEXPI-", "DEXPI RDL", "DEXPI/RDL", "D", 7, None])
def test_scheme_code_rejects_codes_outside_the_grammar(value):
    with pytest.raises(ValueError):
        normalize_external_scheme_code(value)


def test_scheme_code_rejects_a_code_past_the_length_limit():
    with pytest.raises(ValueError):
        normalize_external_scheme_code("A" * 65)


@pytest.mark.parametrize("value", ["ftp://example.test/rdl", "example.test/rdl", "", 7])
def test_scheme_uri_requires_http_or_https(value):
    with pytest.raises(ValueError):
        normalize_external_scheme_uri(value, "base URI")


def test_scheme_uri_accepts_none_and_trims():
    assert normalize_external_scheme_uri(None, "base URI") is None
    assert normalize_external_scheme_uri(" https://example.test/rdl ", "base URI") == "https://example.test/rdl"


def test_release_integrity_requires_a_checksum_and_its_algorithm_together():
    with pytest.raises(ValueError, match="together"):
        normalize_scheme_version_integrity("a" * 64, None, None, NOW)
    with pytest.raises(ValueError, match="together"):
        normalize_scheme_version_integrity(None, "sha256", None, NOW)


def test_release_integrity_requires_a_retrieval_time():
    """A hash or etag with no retrieval time cannot be reproduced."""
    with pytest.raises(ValueError, match="retrieved"):
        normalize_scheme_version_integrity("a" * 64, "sha256", None, None)
    with pytest.raises(ValueError, match="retrieved"):
        normalize_scheme_version_integrity(None, None, 'W/"abc"', None)


def test_release_integrity_accepts_a_bare_release():
    assert normalize_scheme_version_integrity(None, None, None, None) == (None, None, None, None)


def test_release_integrity_normalizes_a_hex_digest():
    checksum, algorithm, etag, retrieved = normalize_scheme_version_integrity(
        "A" * 64, "sha256", 'W/"abc"', NOW
    )
    assert checksum == "a" * 64
    assert (algorithm, etag, retrieved) == ("sha256", 'W/"abc"', NOW)


@pytest.mark.parametrize("checksum", ["zz" * 16, "abc", "a" * 200])
def test_release_integrity_rejects_a_malformed_digest(checksum):
    with pytest.raises(ValueError):
        normalize_scheme_version_integrity(checksum, "sha256", None, NOW)


def test_release_integrity_rejects_an_unknown_algorithm():
    with pytest.raises(ValueError, match="algorithm"):
        normalize_scheme_version_integrity("a" * 64, "crc32", None, NOW)


def test_external_identifier_preserves_case():
    """External schemes are the authority on their own identifiers."""
    assert normalize_external_identifier("  DEXPI:GateValve  ") == "DEXPI:GateValve"


@pytest.mark.parametrize("value", ["", "   ", None, 7, "x" * 513])
def test_external_identifier_rejects_empty_or_oversized_values(value):
    with pytest.raises(ValueError):
        normalize_external_identifier(value)


def test_external_label_is_optional_but_never_blank():
    assert normalize_external_label(None) is None
    assert normalize_external_label(" Gate valve ") == "Gate valve"
    with pytest.raises(ValueError):
        normalize_external_label("   ")


@pytest.mark.parametrize("value", [0, 1, 0.5, Decimal("0.9999")])
def test_confidence_accepts_the_closed_unit_interval(value):
    assert normalize_external_mapping_confidence(value) == Decimal(str(value))


@pytest.mark.parametrize("value", [-0.0001, 1.0001, "0.5", True, object()])
def test_confidence_rejects_values_outside_the_unit_interval_or_wrong_type(value):
    with pytest.raises(ValueError):
        normalize_external_mapping_confidence(value)


def test_evidence_must_be_an_object_when_present():
    assert normalize_external_mapping_evidence(None) == {}
    assert normalize_external_mapping_evidence({"source_uri": "https://example.test"}) == {
        "source_uri": "https://example.test"
    }
    with pytest.raises(ValueError):
        normalize_external_mapping_evidence(["not", "an", "object"])


def test_release_date_must_be_a_date_not_a_timestamp():
    assert isinstance(date(2026, 6, 1), date)
    assert ExternalSemanticSchemeVersion.__table__.columns["release_date"].type.python_type is date


def test_service_modules_expose_no_bulk_import_entry_point():
    """Specification section 15.1: scheme definitions only in P0. A bulk import
    helper landing here would be SM-P2-01/02 arriving early."""
    for module in (scheme_service, mapping_service):
        for name in dir(module):
            assert "bulk" not in name.lower(), f"unexpected bulk entry point: {module.__name__}.{name}"
            assert "import_rdl" not in name.lower()
