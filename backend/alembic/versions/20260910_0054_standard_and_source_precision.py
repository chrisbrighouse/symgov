"""extend symbol standard links and source packages with precision metadata

Revision ID: 20260910_0054
Revises: 20260909_0053
Create Date: 2026-09-10 00:00:00.000000

SM-P0-05 of the Semantic Model & Classification Change Specification: standard
and source precision. Section 15.1 asks for "structured source locators and
hashes" so that Appendix B.2's chain -- catalogue symbol -> source package ->
release/version -> exact provider entry/symbol ID -> hashes -- is
representable. Everything in that chain except rights lands here; rights is
SM-P0-06, and `source_packages.licence_reference` is the forward reference
into it rather than a rights model in miniature.

Purely additive at the column level. Three tables gain columns, every one of
them nullable or server-defaulted, so the migration applies to a table that
already holds rows.

**`source_packages` already holds rows in production.** Unlike the four other
tables named in section 7.10/7.11, this one has a live writer:
`runtime.ensure_source_package_for_intake` creates a package per submission
sheet (`package_type='submission_sheet'`, `status='active'`), and
`workspace.py` reads it back. Nothing here touches `package_code`, `title`,
`provider`, `package_type` or `status`, and no check constraint is placed on
any of them: a constraint on a pre-existing column is validated against
existing rows, and the intake path's values were never governed by a
vocabulary. Every constraint below tests only columns this migration adds, all
of which are NULL on every existing row.

`standards`, `standard_versions`, `symbol_standard_links` and
`source_package_entries` have no writer anywhere in the repository, so
constraining `symbol_standard_links.relationship_type` to section 8.3's eight
values validates against an empty table and accommodates no legacy value.

Two pre-existing defects are corrected, both recorded rather than silently
repaired:

* `uq_symbol_standard_links_revision_standard_relationship_clause` includes
  the nullable `clause_reference`, and PostgreSQL treats NULLs as distinct in
  a unique index. Two rows with the same revision, standard version and
  relationship type and a NULL clause both insert freely today, so the index
  does not enforce what its name claims. It is replaced by
  `uq_symbol_standard_links_active_assertion`, over
  `COALESCE(clause_reference, '')` and restricted to the two live governance
  states. `NULLS NOT DISTINCT` would be the tidier spelling but needs
  PostgreSQL 15 or later; the deployed server version is not recorded
  anywhere in this repository, and a `COALESCE` expression index behaves the
  same on every version. A companion check constraint forbids an empty-string
  clause, so collapsing NULL onto `''` conflates nothing.
* The partial predicate is not cosmetic. This package gives the table
  governance statuses, and without it a rejected assertion would permanently
  block re-proposing the same one, and the retire-the-predecessor supersession
  the delivered packages all use could never run.

Because the replaced index is one of the nine that `20260409_0001`'s own
`downgrade()` drops by name, `downgrade()` here restores it exactly as
`20260409_0001` created it, following `20260909_0053`'s `_rename(restore=...)`
reasoning rather than `20260909_0050`'s no-op. That rollback path cannot
currently be walked end to end -- `20260720_0023` is intentionally
irreversible and stops any downgrade well above the initial schema -- so the
restore guards a path that is correct rather than one that is exercised. It
costs one statement, and the alternative is a migration whose reversibility
depends on an unrelated migration staying irreversible.

Deviations from the specification's literal text, recorded deliberately:

* Section 7.11 offers `source_path/source_locator` for the entry-level column.
  It ships as `source_path`: it addresses an entry *inside* the acquired
  package, and the package's own addressable location is
  `source_packages.source_uri`. `source_locator` would suggest a second
  independent locator and duplicate that column's job.
* Sections 7.10 and 7.11 name SHA-256 specifically, so all three hash columns
  take a fixed `^[0-9a-f]{64}$` and no algorithm column. This is deliberately
  *not* SM-P0-03's shape: `external_semantic_scheme_versions` pairs a
  `^[0-9a-f]{32,128}$` digest with an algorithm because section 3.4 lets a
  publisher choose one, and copying that here would let a 32-character MD5
  digest into a column the specification says is SHA-256.
* `verification_method` and `acquisition_method` are the fourth and fifth
  distinct method vocabularies in this model, and are deliberately not
  unified with `symbol_semantic_assignments.method`,
  `concept_external_references.mapping_method` or the classification
  assignments' `method`. Unifying is a specification change, not an
  implementation tidy-up; `tests/test_standard_source_precision.py` pins all
  five apart.
* No check constraint is added to `standards.status` or
  `standard_versions.status`. Section 7.10 says to preserve those two entities
  and extends neither, and the specification names no status vocabulary for
  them, so declaring one in the database would be inventing a workflow state.
  `standard_sources.py` applies `active | deprecated | withdrawn` as service
  policy, and the tests pin it there.

Identifier lengths: the section 14.3 index over
`(standard_version_id, source_symbol_identifier)` is named
`ix_symbol_standard_links_version_source_symbol_identifier` (57). The literal
column-name spelling that `NAMING_CONVENTION` would produce is 66 characters
and would be rejected outright. Check constraints are given *bare* names and
left to `NAMING_CONVENTION` to prefix -- an already-prefixed name gets
double-prefixed and then silently hash-truncated.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260910_0054"
down_revision: Union[str, None] = "20260909_0053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Section 8.3, in full. `symbol_standard_links.relationship_type` has been
# NOT NULL and completely unconstrained since 20260409_0001, and no code has
# ever written it, so there is no legacy value to accommodate.
_RELATIONSHIP_TYPES = (
    "normative_definition",
    "normative_equivalent",
    "informative_example",
    "vendor_implementation",
    "owner_variant",
    "project_deviation",
    "derived_from",
    "comparison_only",
)

# Section 7.10. The fourth method vocabulary; see the docstring.
_VERIFICATION_METHODS = ("manual", "import_manifest", "source_api", "ai_assisted")

# Section 7.11. The fifth, and the only one that overlaps none of the others.
_ACQUISITION_METHODS = (
    "manual_upload",
    "public_download",
    "licensed_download",
    "api",
    "contributed",
    "generated",
)

# The index 20260409_0001 created, reproduced verbatim so downgrade() can put
# it back. Its NULL-distinctness hole is the defect this migration closes.
_LEGACY_LINK_INDEX = "uq_symbol_standard_links_revision_standard_relationship_clause"
_LEGACY_LINK_COLUMNS = [
    "symbol_revision_id",
    "standard_version_id",
    "relationship_type",
    "clause_reference",
]

# Governance states in which an assertion is live. Rejected and retired rows
# stay out of the unique indexes so an assertion's history survives its
# successor -- the shape SM-P0-02, -03 and -04 all use.
_LIVE_ASSERTION_STATES = "assertion_status in ('proposed', 'verified')"


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    _upgrade_symbol_standard_links()
    _upgrade_source_packages()
    _upgrade_source_package_entries()


def _upgrade_symbol_standard_links() -> None:
    op.add_column("symbol_standard_links", sa.Column("source_symbol_identifier", sa.Text(), nullable=True))
    op.add_column("symbol_standard_links", sa.Column("figure_reference", sa.Text(), nullable=True))
    op.add_column("symbol_standard_links", sa.Column("table_reference", sa.Text(), nullable=True))
    op.add_column("symbol_standard_links", sa.Column("source_uri", sa.Text(), nullable=True))
    # NOT NULL with a server default: an existing row becomes `proposed`,
    # which is the honest reading of a link recorded before this package gave
    # the table a governance state at all.
    op.add_column(
        "symbol_standard_links",
        sa.Column("assertion_status", sa.Text(), nullable=False, server_default=sa.text("'proposed'")),
    )
    op.add_column("symbol_standard_links", sa.Column("verification_method", sa.Text(), nullable=True))
    op.add_column("symbol_standard_links", sa.Column("source_asset_sha256", sa.Text(), nullable=True))
    op.add_column(
        "symbol_standard_links",
        sa.Column(
            "verified_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_symbol_standard_links_verified_by_user_id"),
            nullable=True,
        ),
    )
    op.add_column("symbol_standard_links", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "symbol_standard_links",
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )

    op.create_check_constraint(
        "relationship_type",
        "symbol_standard_links",
        f"relationship_type in ({_quoted(_RELATIONSHIP_TYPES)})",
    )
    op.create_check_constraint(
        "assertion_status",
        "symbol_standard_links",
        "assertion_status in ('proposed', 'verified', 'rejected', 'retired')",
    )
    op.create_check_constraint(
        "verification_method",
        "symbol_standard_links",
        f"verification_method is null or verification_method in ({_quoted(_VERIFICATION_METHODS)})",
    )
    # A verification decision must record when it happened and what it relied
    # on. `verified_by_user_id` stays optional so a deterministic import
    # verified under explicit policy (section 8.4) needs no invented user.
    # `assertion_status` is NOT NULL, so neither branch can evaluate to NULL.
    op.create_check_constraint(
        "verified_decision",
        "symbol_standard_links",
        "assertion_status <> 'verified' or (verified_at is not null and verification_method is not null)",
    )
    # Empty is not a clause reference, and forbidding it is what makes
    # COALESCE(clause_reference, '') in the unique index below unambiguous.
    op.create_check_constraint(
        "clause_reference",
        "symbol_standard_links",
        "clause_reference is null or (btrim(clause_reference) <> '' and char_length(clause_reference) <= 256)",
    )
    op.create_check_constraint(
        "source_symbol_identifier",
        "symbol_standard_links",
        "source_symbol_identifier is null or (btrim(source_symbol_identifier) <> '' "
        "and char_length(source_symbol_identifier) <= 512)",
    )
    op.create_check_constraint(
        "figure_reference",
        "symbol_standard_links",
        "figure_reference is null or (btrim(figure_reference) <> '' and char_length(figure_reference) <= 256)",
    )
    op.create_check_constraint(
        "table_reference",
        "symbol_standard_links",
        "table_reference is null or (btrim(table_reference) <> '' and char_length(table_reference) <= 256)",
    )
    op.create_check_constraint(
        "source_uri",
        "symbol_standard_links",
        "source_uri is null or (source_uri ~ '^https?://' and char_length(source_uri) <= 1024)",
    )
    op.create_check_constraint(
        "source_asset_sha256",
        "symbol_standard_links",
        "source_asset_sha256 is null or source_asset_sha256 ~ '^[0-9a-f]{64}$'",
    )
    # A hash of an acquired artifact with nothing saying where the artifact
    # came from cannot be reproduced, which is the whole point of recording
    # it. Written as `is null or ... is not null` so the branch is always
    # true or false, never NULL.
    op.create_check_constraint(
        "source_asset_provenance",
        "symbol_standard_links",
        "source_asset_sha256 is null or source_uri is not null",
    )
    op.create_check_constraint(
        "evidence_json_object",
        "symbol_standard_links",
        "jsonb_typeof(evidence_json) = 'object'",
    )

    # Replace the index whose NULL-distinctness hole let duplicate assertions
    # insert freely. downgrade() puts the original back.
    op.drop_index(_LEGACY_LINK_INDEX, table_name="symbol_standard_links")
    op.create_index(
        "uq_symbol_standard_links_active_assertion",
        "symbol_standard_links",
        [
            "symbol_revision_id",
            "standard_version_id",
            "relationship_type",
            sa.text("coalesce(clause_reference, '')"),
        ],
        unique=True,
        postgresql_where=sa.text(_LIVE_ASSERTION_STATES),
    )
    # One standard version cannot formally define the same symbol revision
    # twice. Section 9.2's publication gate wants the graphical authority
    # asserted unambiguously, and Appendix B.2's chain carries exactly one
    # normative definition. Competing proposals stay unconstrained so they can
    # sit side by side for review; verifying a successor retires the
    # predecessor rather than being refused.
    op.create_index(
        "uq_symbol_standard_links_verified_definition",
        "symbol_standard_links",
        ["symbol_revision_id", "standard_version_id"],
        unique=True,
        postgresql_where=sa.text("relationship_type = 'normative_definition' and assertion_status = 'verified'"),
    )
    # Section 14.3 asks for this read path by name: find the symbol a
    # standard's own symbol number refers to.
    op.create_index(
        "ix_symbol_standard_links_version_source_symbol_identifier",
        "symbol_standard_links",
        ["standard_version_id", "source_symbol_identifier"],
    )


def _upgrade_source_packages() -> None:
    op.add_column("source_packages", sa.Column("provider_package_identifier", sa.Text(), nullable=True))
    op.add_column("source_packages", sa.Column("source_uri", sa.Text(), nullable=True))
    op.add_column("source_packages", sa.Column("release_version", sa.Text(), nullable=True))
    op.add_column("source_packages", sa.Column("release_date", sa.Date(), nullable=True))
    op.add_column("source_packages", sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("source_packages", sa.Column("acquisition_method", sa.Text(), nullable=True))
    op.add_column("source_packages", sa.Column("licence_reference", sa.Text(), nullable=True))
    op.add_column("source_packages", sa.Column("package_sha256", sa.Text(), nullable=True))
    op.add_column("source_packages", sa.Column("ingestion_profile", sa.Text(), nullable=True))
    op.add_column(
        "source_packages",
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )

    op.create_check_constraint(
        "acquisition_method",
        "source_packages",
        f"acquisition_method is null or acquisition_method in ({_quoted(_ACQUISITION_METHODS)})",
    )
    # An acquisition method with no acquisition time, or the reverse, records
    # half an event. `(x is null) = (y is null)` is boolean on both sides, so
    # it can never evaluate to NULL.
    op.create_check_constraint(
        "acquisition_pairing",
        "source_packages",
        "(acquired_at is null) = (acquisition_method is null)",
    )
    op.create_check_constraint(
        "package_sha256",
        "source_packages",
        "package_sha256 is null or package_sha256 ~ '^[0-9a-f]{64}$'",
    )
    # A package hash with no record of when the package was obtained cannot be
    # re-verified against the provider. The pairing constraint above then also
    # guarantees an acquisition method.
    op.create_check_constraint(
        "package_integrity_context",
        "source_packages",
        "package_sha256 is null or acquired_at is not null",
    )
    op.create_check_constraint(
        "source_uri",
        "source_packages",
        "source_uri is null or (source_uri ~ '^https?://' and char_length(source_uri) <= 1024)",
    )
    op.create_check_constraint(
        "provider_package_identifier",
        "source_packages",
        "provider_package_identifier is null or (btrim(provider_package_identifier) <> '' "
        "and char_length(provider_package_identifier) <= 512)",
    )
    op.create_check_constraint(
        "release_version",
        "source_packages",
        "release_version is null or (btrim(release_version) <> '' and char_length(release_version) <= 128)",
    )
    # Section 7.12 is explicit that this is "a reference to terms/contract/
    # rights record; not the licence text itself", and the 512-character bound
    # is what keeps it one. The rights record it will point at is SM-P0-06.
    op.create_check_constraint(
        "licence_reference",
        "source_packages",
        "licence_reference is null or (btrim(licence_reference) <> '' and char_length(licence_reference) <= 512)",
    )
    op.create_check_constraint(
        "ingestion_profile",
        "source_packages",
        "ingestion_profile is null or (btrim(ingestion_profile) <> '' and char_length(ingestion_profile) <= 256)",
    )
    # Builtins only, deliberately: a depth or size bound spelled with
    # `stage4_jsonb_max_depth` would break Base.metadata.create_all(), which
    # is what INTENTIONAL_ORM_EXPRESSION_DIVERGENCE exists to record.
    op.create_check_constraint(
        "metadata_json_object",
        "source_packages",
        "jsonb_typeof(metadata_json) = 'object'",
    )

    # Find the package a provider's own release identifier refers to. Partial
    # so the nine-tenths of rows the submission-intake path writes, which have
    # no provider identifier, stay out of it.
    op.create_index(
        "ix_source_packages_provider_package_identifier",
        "source_packages",
        ["provider", "provider_package_identifier"],
        postgresql_where=sa.text("provider_package_identifier is not null"),
    )


def _upgrade_source_package_entries() -> None:
    op.add_column("source_package_entries", sa.Column("provider_entry_identifier", sa.Text(), nullable=True))
    op.add_column("source_package_entries", sa.Column("source_path", sa.Text(), nullable=True))
    op.add_column("source_package_entries", sa.Column("original_asset_sha256", sa.Text(), nullable=True))

    op.create_check_constraint(
        "provider_entry_identifier",
        "source_package_entries",
        "provider_entry_identifier is null or (btrim(provider_entry_identifier) <> '' "
        "and char_length(provider_entry_identifier) <= 512)",
    )
    op.create_check_constraint(
        "source_path",
        "source_package_entries",
        "source_path is null or (btrim(source_path) <> '' and char_length(source_path) <= 1024)",
    )
    op.create_check_constraint(
        "original_asset_sha256",
        "source_package_entries",
        "original_asset_sha256 is null or original_asset_sha256 ~ '^[0-9a-f]{64}$'",
    )
    # A hash with nothing identifying which asset inside the package it
    # belongs to traces nothing.
    op.create_check_constraint(
        "original_asset_context",
        "source_package_entries",
        "original_asset_sha256 is null or source_path is not null or provider_entry_identifier is not null",
    )

    # Appendix B.2's "exact provider entry ID" read path, within one package.
    op.create_index(
        "ix_source_package_entries_provider_entry_identifier",
        "source_package_entries",
        ["source_package_id", "provider_entry_identifier"],
        postgresql_where=sa.text("provider_entry_identifier is not null"),
    )


def downgrade() -> None:
    """Remove only what upgrade() added, and put 20260409_0001's index back.

    Restoring `uq_symbol_standard_links_revision_standard_relationship_clause`
    is not optional. 20260409_0001's own `downgrade()` drops it by name at
    line 522, so rolling back past this migration and on past the initial
    schema would fail with "index ... does not exist" if the replacement were
    left in its place. 20260909_0050's no-op downgrade is not a precedent for
    an object an older migration references; 20260909_0053's restore is.

    None of 20260409_0001's original columns is touched in either direction.
    """
    op.drop_index("ix_source_package_entries_provider_entry_identifier", table_name="source_package_entries")
    for name in ("original_asset_context", "original_asset_sha256", "source_path", "provider_entry_identifier"):
        op.drop_constraint(name, "source_package_entries", type_="check")
    for column in ("original_asset_sha256", "source_path", "provider_entry_identifier"):
        op.drop_column("source_package_entries", column)

    op.drop_index("ix_source_packages_provider_package_identifier", table_name="source_packages")
    for name in (
        "metadata_json_object",
        "ingestion_profile",
        "licence_reference",
        "release_version",
        "provider_package_identifier",
        "source_uri",
        "package_integrity_context",
        "package_sha256",
        "acquisition_pairing",
        "acquisition_method",
    ):
        op.drop_constraint(name, "source_packages", type_="check")
    for column in (
        "metadata_json",
        "ingestion_profile",
        "package_sha256",
        "licence_reference",
        "acquisition_method",
        "acquired_at",
        "release_date",
        "release_version",
        "source_uri",
        "provider_package_identifier",
    ):
        op.drop_column("source_packages", column)

    op.drop_index("ix_symbol_standard_links_version_source_symbol_identifier", table_name="symbol_standard_links")
    op.drop_index("uq_symbol_standard_links_verified_definition", table_name="symbol_standard_links")
    op.drop_index("uq_symbol_standard_links_active_assertion", table_name="symbol_standard_links")
    op.create_index(
        _LEGACY_LINK_INDEX,
        "symbol_standard_links",
        _LEGACY_LINK_COLUMNS,
        unique=True,
    )
    for name in (
        "evidence_json_object",
        "source_asset_provenance",
        "source_asset_sha256",
        "source_uri",
        "table_reference",
        "figure_reference",
        "source_symbol_identifier",
        "clause_reference",
        "verified_decision",
        "verification_method",
        "assertion_status",
        "relationship_type",
    ):
        op.drop_constraint(name, "symbol_standard_links", type_="check")
    for column in (
        "evidence_json",
        "verified_at",
        "verified_by_user_id",
        "source_asset_sha256",
        "verification_method",
        "assertion_status",
        "source_uri",
        "table_reference",
        "figure_reference",
        "source_symbol_identifier",
    ):
        op.drop_column("symbol_standard_links", column)
