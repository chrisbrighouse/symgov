"""add external semantic schemes, versions and concept mappings

Revision ID: 20260909_0049
Revises: 20260909_0048
Create Date: 2026-09-09 00:00:00.000000

SM-P0-03 of the Semantic Model & Classification Change Specification: external
reference-data libraries modelled as *versioned dependencies* (section 3.4),
plus the governed mapping from a SymGov concept to an identifier in one exact
release of one of them. Purely additive -- no existing table, column or
behaviour changes.

Three storage decisions carry the governance weight:

* `concept_external_references.scheme_version_id` is NOT NULL, so the
  "timeless lookup list" failure named in section 3.4 is structurally
  impossible: no mapping can exist without naming the release it was observed
  in (acceptance criterion, section 16.2).
* The external identifier lives on the mapping, never on the concept. Section
  7.5 and principle P-04: SymGov concept identity must survive an external
  scheme splitting, merging, renaming or deprecating a class.
* A verified `exact` mapping must name a human reviewer unless it came from a
  deterministic import, and must carry evidence. That is as far as a check
  constraint can go towards section 16.2's "no verified exact mapping from
  string similarity alone"; the remainder is enforced in
  `concept_external_references.py` and covered by tests.

Identifier lengths: `concept_external_references` and
`external_semantic_scheme_versions` are long enough that three foreign keys
would exceed PostgreSQL's 63-character limit under the naming convention (68,
82 and 61 characters), so those are named explicitly. Check constraints are
given *bare* names and left to `NAMING_CONVENTION` to prefix -- passing an
already-prefixed name to `sa.CheckConstraint` inside `op.create_table` gets it
silently double-prefixed and hash-truncated.

Seed data: the three scheme *definitions* named in section 15.1, and nothing
else. No versions and no reference-data import -- bulk RDL ingestion is
SM-P2-01/02. The seeded `base_uri` values are the authoritative reference URLs
cited in the specification's Appendix D; where Appendix D cites no namespace
for a scheme, `base_uri` is left NULL rather than invented.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260909_0049"
down_revision: Union[str, None] = "20260909_0048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "external_semantic_schemes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("scheme_code", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("issuing_body", sa.Text(), nullable=False),
        sa.Column("base_uri", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_external_semantic_schemes"),
        sa.CheckConstraint(
            "scheme_code ~ '^[A-Z0-9][A-Z0-9.-]{0,62}[A-Z0-9]$'",
            name="scheme_code",
        ),
        sa.CheckConstraint(
            "btrim(title) <> '' and char_length(title) <= 256",
            name="title",
        ),
        sa.CheckConstraint(
            "btrim(issuing_body) <> '' and char_length(issuing_body) <= 256",
            name="issuing_body",
        ),
        sa.CheckConstraint(
            "base_uri is null or (base_uri ~ '^https?://' and char_length(base_uri) <= 1024)",
            name="base_uri",
        ),
        sa.CheckConstraint(
            "status in ('active', 'deprecated', 'withdrawn')",
            name="status",
        ),
    )
    op.create_index(
        "uq_external_semantic_schemes_scheme_code",
        "external_semantic_schemes",
        ["scheme_code"],
        unique=True,
    )
    op.create_index(
        "ix_external_semantic_schemes_status_scheme_code",
        "external_semantic_schemes",
        ["status", "scheme_code"],
    )

    op.create_table(
        "external_semantic_scheme_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "scheme_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("external_semantic_schemes.id", ondelete="RESTRICT", name="fk_external_semantic_scheme_versions_scheme_id"),
            nullable=False,
        ),
        sa.Column("version_label", sa.Text(), nullable=False),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column("checksum", sa.Text(), nullable=True),
        sa.Column("checksum_algorithm", sa.Text(), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_external_semantic_scheme_versions_created_by_user_id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_external_semantic_scheme_versions"),
        sa.CheckConstraint(
            "btrim(version_label) <> '' and char_length(version_label) <= 128",
            name="version_label",
        ),
        sa.CheckConstraint(
            "source_uri is null or (source_uri ~ '^https?://' and char_length(source_uri) <= 1024)",
            name="source_uri",
        ),
        sa.CheckConstraint(
            # Both `is not null` tests are load-bearing: without them a
            # checksum with a NULL algorithm makes the second branch NULL
            # rather than false, and PostgreSQL accepts a check constraint
            # that evaluates to NULL.
            "(checksum is null and checksum_algorithm is null) or "
            "(checksum is not null and checksum_algorithm is not null "
            "and checksum ~ '^[0-9a-f]{32,128}$' "
            "and checksum_algorithm in ('md5', 'sha1', 'sha256', 'sha512'))",
            name="checksum_pairing",
        ),
        sa.CheckConstraint(
            "etag is null or (btrim(etag) <> '' and char_length(etag) <= 256)",
            name="etag",
        ),
        # A hash or etag with no retrieval time cannot be reproduced, which
        # defeats the configuration-management traceability section 3.2 asks of
        # a versioned external dependency.
        sa.CheckConstraint(
            "(checksum is null and etag is null) or retrieved_at is not null",
            name="integrity_retrieval",
        ),
        sa.CheckConstraint(
            "status in ('active', 'deprecated', 'withdrawn')",
            name="status",
        ),
    )
    op.create_index(
        "uq_external_semantic_scheme_versions_scheme_version_label",
        "external_semantic_scheme_versions",
        ["scheme_id", "version_label"],
        unique=True,
    )
    op.create_index(
        "ix_external_semantic_scheme_versions_scheme_status",
        "external_semantic_scheme_versions",
        ["scheme_id", "status"],
    )

    op.create_table(
        "concept_external_references",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "semantic_concept_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("semantic_concepts.id", ondelete="RESTRICT", name="fk_concept_external_references_semantic_concept_id"),
            nullable=False,
        ),
        # NOT NULL is the acceptance criterion in section 16.2: no external
        # mapping may be stored without the scheme version it was observed in.
        sa.Column(
            "scheme_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("external_semantic_scheme_versions.id", ondelete="RESTRICT", name="fk_concept_external_references_scheme_version_id"),
            nullable=False,
        ),
        sa.Column("external_identifier", sa.Text(), nullable=False),
        sa.Column("external_label", sa.Text(), nullable=True),
        sa.Column("mapping_type", sa.Text(), nullable=False),
        sa.Column("mapping_status", sa.Text(), nullable=False, server_default=sa.text("'proposed'")),
        sa.Column("mapping_method", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("proposed_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_concept_external_references"),
        sa.CheckConstraint(
            "btrim(external_identifier) <> '' and char_length(external_identifier) <= 512",
            name="external_identifier",
        ),
        sa.CheckConstraint(
            "external_label is null or (btrim(external_label) <> '' and char_length(external_label) <= 512)",
            name="external_label",
        ),
        sa.CheckConstraint(
            "mapping_type in ('exact', 'close', 'broader', 'narrower', 'related')",
            name="mapping_type",
        ),
        sa.CheckConstraint(
            "mapping_status in ('proposed', 'verified', 'rejected', 'retired')",
            name="mapping_status",
        ),
        sa.CheckConstraint(
            "mapping_method in ('manual', 'imported', 'rule', 'ai_assisted')",
            name="mapping_method",
        ),
        sa.CheckConstraint(
            "confidence is null or (confidence >= 0 and confidence <= 1)",
            name="confidence",
        ),
        sa.CheckConstraint(
            "mapping_status in ('proposed', 'retired') or reviewed_at is not null",
            name="review_decision",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_json) = 'object'",
            name="evidence_json_object",
        ),
        # Section 16.2, as far as a constraint can carry it: the strongest
        # mapping type may not reach `verified` anonymously. A deterministic
        # import (section 8.4) is the only exception, and even that is gated by
        # explicit policy in the service layer.
        sa.CheckConstraint(
            "mapping_status <> 'verified' or mapping_type <> 'exact' "
            "or reviewed_by_user_id is not null or mapping_method = 'imported'",
            name="verified_exact_reviewer",
        ),
        sa.CheckConstraint(
            "mapping_status <> 'verified' or mapping_type <> 'exact' "
            "or evidence_json <> '{}'::jsonb",
            name="verified_exact_evidence",
        ),
    )
    # Section 14.3 asks for these two read paths explicitly.
    op.create_index(
        "ix_concept_external_references_scheme_version_identifier",
        "concept_external_references",
        ["scheme_version_id", "external_identifier"],
    )
    op.create_index(
        "ix_concept_external_references_concept_status",
        "concept_external_references",
        ["semantic_concept_id", "mapping_status"],
    )
    # One live assertion per (concept, release, external identifier). Rejected
    # and retired rows stay out of the index so the governance history of a
    # mapping survives alongside its replacement.
    op.create_index(
        "uq_concept_external_references_active_mapping",
        "concept_external_references",
        ["semantic_concept_id", "scheme_version_id", "external_identifier"],
        unique=True,
        postgresql_where=sa.text("mapping_status in ('proposed', 'verified')"),
    )
    # Two different classes of one release cannot both be exactly this concept
    # without asserting that those two classes are themselves identical --
    # precisely the false equivalence section 8.1 says `exact` must avoid.
    op.create_index(
        "uq_concept_external_references_verified_exact",
        "concept_external_references",
        ["semantic_concept_id", "scheme_version_id"],
        unique=True,
        postgresql_where=sa.text("mapping_type = 'exact' and mapping_status = 'verified'"),
    )

    # Section 15.1: seed the three scheme definitions, and nothing else. The
    # identifiers are uuid5 of `urn:symgov:external-semantic-scheme:<code>`, so
    # every environment agrees on them without a lookup by code.
    op.execute(
        """
        INSERT INTO external_semantic_schemes
            (id, scheme_code, title, issuing_body, base_uri, status, created_at, updated_at)
        VALUES
            ('154348c5-bcb3-55c8-ac3c-c7e5be122395', 'DEXPI-RDL',
             'DEXPI Sandbox Reference Data Library', 'DEXPI e.V.',
             'https://dexpi.org/tools-service/', 'active', now(), now()),
            ('3d9c4bf2-d8d6-53fc-800b-2cda40ba2c91', 'ISO15926-RDL-PCA',
             'ISO 15926 Reference Data Library', 'POSC Caesar Association',
             NULL, 'active', now(), now()),
            ('5d40bb55-fc0a-563f-bc7c-9ac7a973bddc', 'CFIHOS-RDL',
             'CFIHOS Reference Data Library', 'IOGP JIP36 / CFIHOS',
             'https://www.jip36-cfihos.org/cfihos-standards/', 'active', now(), now())
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("uq_concept_external_references_verified_exact", table_name="concept_external_references")
    op.drop_index("uq_concept_external_references_active_mapping", table_name="concept_external_references")
    op.drop_index("ix_concept_external_references_concept_status", table_name="concept_external_references")
    op.drop_index("ix_concept_external_references_scheme_version_identifier", table_name="concept_external_references")
    op.drop_table("concept_external_references")

    op.drop_index("ix_external_semantic_scheme_versions_scheme_status", table_name="external_semantic_scheme_versions")
    op.drop_index("uq_external_semantic_scheme_versions_scheme_version_label", table_name="external_semantic_scheme_versions")
    op.drop_table("external_semantic_scheme_versions")

    op.drop_index("ix_external_semantic_schemes_status_scheme_code", table_name="external_semantic_schemes")
    op.drop_index("uq_external_semantic_schemes_scheme_code", table_name="external_semantic_schemes")
    op.drop_table("external_semantic_schemes")
