"""add governed classification schemes, nodes and assignments

Revision ID: 20260909_0051
Revises: 20260909_0050
Create Date: 2026-09-09 00:00:00.000000

SM-P0-04 of the Semantic Model & Classification Change Specification: the
governed classification data model, plus phase M1 of section 12.1 -- seeding
the catalogue's hard-coded facet orders as version 1 of three schemes.
Purely additive; no existing table, column or write path changes. The
dual-write of legacy `category`/`discipline` is SM-P0-09.

Four tables:

* `classification_schemes` (section 7.6) -- a browse/reporting facet. Section
  7.6 is explicit that this is "not necessarily an ontology": meaning stays in
  `semantic_concepts`, and nodes here are a display hierarchy.
* `classification_nodes` (section 7.6) -- one facet value, with a
  self-referencing parent.
* `concept_classification_assignments` (section 7.7) -- meaning-oriented,
  attached to the concept.
* `symbol_revision_classifications` (section 7.8) -- representation-oriented,
  attached to the exact graphic.

Three storage decisions carry the governance weight:

* The composite unique key `classification_nodes (id, scheme_id)` is the
  target of the parent link *and* of both assignment tables' node foreign
  keys. That is what makes a cross-scheme parent, or an assignment whose
  scheme disagrees with its node, structurally impossible rather than merely
  discouraged.
* `classification_scheme_id` is denormalized onto both assignment tables --
  redundant with the node, held in agreement by that composite foreign key --
  so "at most one verified primary per scheme" can be a partial unique index.
  A primary discipline and a primary category are both legitimate; a second
  primary category is not.
* `method <> 'legacy_backfill' or status <> 'verified'` makes section 12.3's
  rule structural: the phase M2 backfill cannot label its own output verified.

Deviations from the specification's literal text, recorded deliberately:

* Section 7.7 lists `primary | secondary | inherited | proposed` as
  `assignment_role`, and section 7.8 lists `primary | secondary | proposed`.
  `proposed` is a governance *status* everywhere else in this model (section
  8.4, and all three delivered semantic packages), so `status` carries it and
  the role vocabularies stop at `inherited` and `secondary`.
* The section 7.8 table is named `symbol_revision_classifications` (31
  characters) rather than the logical `symbol_revision_classification_-`
  `assignments` (42). Every foreign key on the longer name exceeds
  PostgreSQL's 63-character identifier limit even when named explicitly, and
  so does one check constraint. Section 7 asks that final migration naming
  follow existing repository conventions.
* Section 7.6 also names Industry/Application and Drawing Type as desirable
  schemes. Neither has a hard-coded list to seed from, so neither is created:
  an empty scheme would be an invented vocabulary.

Identifier lengths: every foreign key on the two assignment tables is named
explicitly (the convention would generate names of 63 to 89 characters), and
so is the composite parent link on `classification_nodes` (69). Check
constraints are given *bare* names and left to `NAMING_CONVENTION` to prefix
-- passing an already-prefixed name to `sa.CheckConstraint` inside
`op.create_table` gets it silently double-prefixed and hash-truncated.

Seed data: the three facet orders in `symgov_backend/catalog_taxonomy.py`,
which is what the catalogue UI renders today. `sort_order` reproduces those
list orders in multiples of ten, so a node can later be inserted between two
seeded ones without renumbering. The label lists are repeated literally below
rather than imported: a migration must keep applying after the application
constants move on, and a test pins the two in agreement. `FORMAT_ORDER` is
absent -- it is a file-format list, not a classification facet.
"""
from __future__ import annotations

import re
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260909_0051"
down_revision: Union[str, None] = "20260909_0050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Frozen copies of CATALOG_DISCIPLINE_ORDER, CATALOG_CATEGORY_ORDER and
# CATALOG_USE_CASE_ORDER as they stood when this migration was written.
# `tests/test_classification_data_model.py` fails if they drift from
# `catalog_taxonomy.py`, so a later reordering is a conscious decision with a
# follow-up migration rather than a silent divergence.
_SEED_SCHEMES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "ENGINEERING-DISCIPLINE",
        "Engineering Discipline",
        "Engineering discipline facet, seeded from the catalogue's hard-coded discipline order.",
        (
            "Electrical",
            "Fire & Life Safety",
            "Piping / P&ID",
            "Process",
            "Instrumentation & Controls",
            "Mechanical",
            "HVAC",
            "Civil / Structural",
            "Architectural",
            "Safety / Signage",
            "General / Annotation",
        ),
    ),
    (
        "SYMBOL-CATEGORY-FAMILY",
        "Symbol Category/Family",
        "Symbol category and family facet, seeded from the catalogue's hard-coded category order.",
        (
            "Valves",
            "Pumps",
            "Vessels / Tanks",
            "Pipework / Fittings",
            "Instruments",
            "Fire Alarm Devices",
            "Sensors / Detectors",
            "Motors / Drives",
            "Electrical Devices",
            "Switchgear / Distribution",
            "Lighting",
            "Controls",
            "Actuators",
            "Heating / HVAC",
            "Safety Devices",
            "Annotations / Tags",
            "Drawing Symbols",
            "Doors",
            "Equipment",
            "Miscellaneous / Unclassified",
        ),
    ),
    (
        "USE-CASE",
        "Use Case",
        "Download use-case facet, seeded from the catalogue's hard-coded use-case order.",
        (
            "Insert into CAD drawing",
            "Mark up / annotate drawing",
            "Use in PDF/report",
            "Use as web/app icon",
            "Use as reference only",
            "Compare against standard",
        ),
    ),
)

# Section 7.6: "The existing hard-coded orders can be seeded as version 1 of
# those schemes."
_SEED_VERSION_LABEL = "1"
_SORT_ORDER_STEP = 10


def _node_code(label: str) -> str:
    """Derive a node code from a display label.

    Mirrors `classification_schemes.derive_classification_node_code`. Repeated
    here so this migration stays self-contained; a test runs both over the
    seeded labels and fails if they disagree.
    """
    return re.sub(r"[^A-Z0-9]+", "_", label.upper()).strip("_")


def upgrade() -> None:
    op.create_table(
        "classification_schemes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("scheme_code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        # Section 14.1 places scheme management with a platform admin and
        # names organisation-specific schemes as a future extension, so the
        # P0 deferral is structural rather than implied.
        sa.Column("scope", sa.Text(), nullable=False, server_default=sa.text("'platform'")),
        sa.Column("version_label", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_classification_schemes"),
        sa.CheckConstraint(
            "scheme_code ~ '^[A-Z0-9][A-Z0-9.-]{0,62}[A-Z0-9]$'",
            name="scheme_code",
        ),
        sa.CheckConstraint(
            "btrim(name) <> '' and char_length(name) <= 256",
            name="name",
        ),
        sa.CheckConstraint(
            "scope in ('platform')",
            name="scope",
        ),
        sa.CheckConstraint(
            "btrim(version_label) <> '' and char_length(version_label) <= 64",
            name="version_label",
        ),
        sa.CheckConstraint(
            "status in ('draft', 'active', 'deprecated', 'withdrawn')",
            name="status",
        ),
        sa.CheckConstraint(
            "description is null or (btrim(description) <> '' and char_length(description) <= 4000)",
            name="description",
        ),
    )
    op.create_index(
        "uq_classification_schemes_scheme_code",
        "classification_schemes",
        ["scheme_code"],
        unique=True,
    )
    op.create_index(
        "ix_classification_schemes_status_scheme_code",
        "classification_schemes",
        ["status", "scheme_code"],
    )

    op.create_table(
        "classification_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "scheme_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("classification_schemes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("node_code", sa.Text(), nullable=False),
        sa.Column("parent_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("preferred_label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_classification_nodes"),
        sa.CheckConstraint(
            "node_code ~ '^[A-Z0-9][A-Z0-9_]{0,62}[A-Z0-9]$'",
            name="node_code",
        ),
        sa.CheckConstraint(
            "btrim(preferred_label) <> '' and char_length(preferred_label) <= 256",
            name="preferred_label",
        ),
        sa.CheckConstraint(
            "description is null or (btrim(description) <> '' and char_length(description) <= 4000)",
            name="description",
        ),
        sa.CheckConstraint(
            "sort_order >= 0",
            name="sort_order",
        ),
        sa.CheckConstraint(
            "status in ('draft', 'active', 'deprecated', 'withdrawn')",
            name="status",
        ),
        # `parent_node_id is null` first keeps every branch true/false: a bare
        # `parent_node_id <> id` evaluates to NULL for a root node, and
        # PostgreSQL accepts a check constraint that evaluates to NULL.
        sa.CheckConstraint(
            "parent_node_id is null or parent_node_id <> id",
            name="parent_not_self",
        ),
        # The composite target the parent link and both assignment tables
        # point at. This is what stops a node being parented into, or an
        # assignment being made through, a different scheme.
        sa.UniqueConstraint("id", "scheme_id", name="uq_classification_nodes_id_scheme_id"),
        sa.UniqueConstraint("scheme_id", "node_code", name="uq_classification_nodes_scheme_id_node_code"),
        # Named explicitly: the convention would generate 69 characters.
        sa.ForeignKeyConstraint(
            ["parent_node_id", "scheme_id"],
            ["classification_nodes.id", "classification_nodes.scheme_id"],
            ondelete="RESTRICT",
            name="fk_classification_nodes_parent_node_id_scheme_id",
        ),
    )
    op.create_index(
        "ix_classification_nodes_scheme_id_sort_order",
        "classification_nodes",
        ["scheme_id", "sort_order"],
    )
    op.create_index(
        "ix_classification_nodes_parent_node_id",
        "classification_nodes",
        ["parent_node_id"],
    )

    op.create_table(
        "concept_classification_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "semantic_concept_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("semantic_concepts.id", ondelete="RESTRICT", name="fk_concept_classification_assignments_semantic_concept_id"),
            nullable=False,
        ),
        sa.Column("classification_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Redundant with the node and held in agreement by the composite
        # foreign key below. It exists so the verified-primary rule can be a
        # partial unique index per scheme.
        sa.Column("classification_scheme_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assignment_role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'proposed'")),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "proposed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_concept_classification_assignments_proposed_by_user_id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "reviewed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_concept_classification_assignments_reviewed_by_user_id"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_concept_classification_assignments"),
        # Section 7.7 lists `proposed` among the roles; it is a governance
        # status everywhere else in this model, so `status` carries it.
        sa.CheckConstraint(
            "assignment_role in ('primary', 'secondary', 'inherited')",
            name="assignment_role",
        ),
        sa.CheckConstraint(
            "status in ('proposed', 'verified', 'rejected', 'retired')",
            name="status",
        ),
        # Section 7.9's vocabulary plus section 12.1 phase M2's
        # `legacy_backfill`. Deliberately a third vocabulary: unifying it with
        # `symbol_semantic_assignments.method` or
        # `concept_external_references.mapping_method` would be a
        # specification change, not an implementation tidy-up.
        sa.CheckConstraint(
            "method in ('manual', 'source_mapping', 'rule', 'ai_assisted', 'legacy_backfill')",
            name="method",
        ),
        sa.CheckConstraint(
            "confidence is null or (confidence >= 0 and confidence <= 1)",
            name="confidence",
        ),
        sa.CheckConstraint(
            "status in ('proposed', 'retired') or reviewed_at is not null",
            name="review_decision",
        ),
        # Section 12.3. Both columns are NOT NULL, so neither branch can be
        # NULL and the constraint always evaluates true or false.
        sa.CheckConstraint(
            "method <> 'legacy_backfill' or status <> 'verified'",
            name="backfill_not_verified",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_json) = 'object'",
            name="evidence_json_object",
        ),
        # Named explicitly: the convention would generate 81 characters.
        sa.ForeignKeyConstraint(
            ["classification_node_id", "classification_scheme_id"],
            ["classification_nodes.id", "classification_nodes.scheme_id"],
            ondelete="RESTRICT",
            name="fk_concept_classification_assignments_classification_node_id",
        ),
    )
    # Section 14.3: index classification assignments by target and by node.
    op.create_index(
        "ix_concept_classification_assignments_concept_status",
        "concept_classification_assignments",
        ["semantic_concept_id", "status"],
    )
    op.create_index(
        "ix_concept_classification_assignments_node_status",
        "concept_classification_assignments",
        ["classification_node_id", "status"],
    )
    # At most one verified primary per (concept, scheme). Proposals stay
    # unconstrained so competing candidates can sit side by side for review.
    op.create_index(
        "uq_concept_classification_assignments_verified_primary",
        "concept_classification_assignments",
        ["semantic_concept_id", "classification_scheme_id"],
        unique=True,
        postgresql_where=sa.text("assignment_role = 'primary' and status = 'verified'"),
    )
    # One live assertion per (concept, node). Rejected and retired rows stay
    # out so an assignment's governance history survives its successor.
    op.create_index(
        "uq_concept_classification_assignments_active_node",
        "concept_classification_assignments",
        ["semantic_concept_id", "classification_node_id"],
        unique=True,
        postgresql_where=sa.text("status in ('proposed', 'verified')"),
    )

    op.create_table(
        "symbol_revision_classifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "symbol_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("symbol_revisions.id", ondelete="RESTRICT", name="fk_symbol_revision_classifications_symbol_revision_id"),
            nullable=False,
        ),
        sa.Column("classification_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("classification_scheme_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assignment_role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'proposed'")),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "proposed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_symbol_revision_classifications_proposed_by_user_id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "reviewed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_symbol_revision_classifications_reviewed_by_user_id"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_symbol_revision_classifications"),
        # Section 7.8 lists `primary | secondary | proposed`. As in section
        # 7.7, `proposed` is carried by `status`. There is no `inherited`
        # here -- a revision inherits nothing, its concept does.
        sa.CheckConstraint(
            "assignment_role in ('primary', 'secondary')",
            name="assignment_role",
        ),
        sa.CheckConstraint(
            "status in ('proposed', 'verified', 'rejected', 'retired')",
            name="status",
        ),
        sa.CheckConstraint(
            "method in ('manual', 'source_mapping', 'rule', 'ai_assisted', 'legacy_backfill')",
            name="method",
        ),
        sa.CheckConstraint(
            "confidence is null or (confidence >= 0 and confidence <= 1)",
            name="confidence",
        ),
        sa.CheckConstraint(
            "status in ('proposed', 'retired') or reviewed_at is not null",
            name="review_decision",
        ),
        # Section 12.3: the phase M2 backfill cannot label its own output
        # verified. This is the constraint that makes that structural.
        sa.CheckConstraint(
            "method <> 'legacy_backfill' or status <> 'verified'",
            name="backfill_not_verified",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_json) = 'object'",
            name="evidence_json_object",
        ),
        sa.ForeignKeyConstraint(
            ["classification_node_id", "classification_scheme_id"],
            ["classification_nodes.id", "classification_nodes.scheme_id"],
            ondelete="RESTRICT",
            name="fk_symbol_revision_classifications_classification_node_id",
        ),
    )
    op.create_index(
        "ix_symbol_revision_classifications_revision_status",
        "symbol_revision_classifications",
        ["symbol_revision_id", "status"],
    )
    op.create_index(
        "ix_symbol_revision_classifications_node_status",
        "symbol_revision_classifications",
        ["classification_node_id", "status"],
    )
    op.create_index(
        "uq_symbol_revision_classifications_verified_primary",
        "symbol_revision_classifications",
        ["symbol_revision_id", "classification_scheme_id"],
        unique=True,
        postgresql_where=sa.text("assignment_role = 'primary' and status = 'verified'"),
    )
    op.create_index(
        "uq_symbol_revision_classifications_active_node",
        "symbol_revision_classifications",
        ["symbol_revision_id", "classification_node_id"],
        unique=True,
        postgresql_where=sa.text("status in ('proposed', 'verified')"),
    )

    _seed_schemes_and_nodes()


def _seed_schemes_and_nodes() -> None:
    """Section 12.1 phase M1: seed the three current facet orders.

    Identifiers are uuid5 of `urn:symgov:classification-scheme:<code>` and
    `urn:symgov:classification-node:<scheme code>:<node code>`, so every
    environment agrees on them without a lookup by code. No assignment rows:
    backfilling those is phase M2 / SM-P0-09.
    """
    connection = op.get_bind()
    for scheme_code, name, description, labels in _SEED_SCHEMES:
        scheme_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"urn:symgov:classification-scheme:{scheme_code}"
        )
        connection.execute(
            sa.text(
                "INSERT INTO classification_schemes "
                "(id, scheme_code, name, scope, version_label, status, description, "
                "created_at, updated_at) "
                "VALUES (:id, :scheme_code, :name, 'platform', :version_label, 'active', "
                ":description, now(), now()) "
                "ON CONFLICT DO NOTHING"
            ),
            {
                "id": scheme_id,
                "scheme_code": scheme_code,
                "name": name,
                "version_label": _SEED_VERSION_LABEL,
                "description": description,
            },
        )
        for index, label in enumerate(labels):
            node_code = _node_code(label)
            connection.execute(
                sa.text(
                    "INSERT INTO classification_nodes "
                    "(id, scheme_id, node_code, parent_node_id, preferred_label, "
                    "sort_order, status, created_at, updated_at) "
                    "VALUES (:id, :scheme_id, :node_code, NULL, :preferred_label, "
                    ":sort_order, 'active', now(), now()) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"urn:symgov:classification-node:{scheme_code}:{node_code}",
                    ),
                    "scheme_id": scheme_id,
                    "node_code": node_code,
                    "preferred_label": label,
                    "sort_order": (index + 1) * _SORT_ORDER_STEP,
                },
            )


def downgrade() -> None:
    op.drop_index("uq_symbol_revision_classifications_active_node", table_name="symbol_revision_classifications")
    op.drop_index("uq_symbol_revision_classifications_verified_primary", table_name="symbol_revision_classifications")
    op.drop_index("ix_symbol_revision_classifications_node_status", table_name="symbol_revision_classifications")
    op.drop_index("ix_symbol_revision_classifications_revision_status", table_name="symbol_revision_classifications")
    op.drop_table("symbol_revision_classifications")

    op.drop_index("uq_concept_classification_assignments_active_node", table_name="concept_classification_assignments")
    op.drop_index("uq_concept_classification_assignments_verified_primary", table_name="concept_classification_assignments")
    op.drop_index("ix_concept_classification_assignments_node_status", table_name="concept_classification_assignments")
    op.drop_index("ix_concept_classification_assignments_concept_status", table_name="concept_classification_assignments")
    op.drop_table("concept_classification_assignments")

    op.drop_index("ix_classification_nodes_parent_node_id", table_name="classification_nodes")
    op.drop_index("ix_classification_nodes_scheme_id_sort_order", table_name="classification_nodes")
    op.drop_table("classification_nodes")

    op.drop_index("ix_classification_schemes_status_scheme_code", table_name="classification_schemes")
    op.drop_index("uq_classification_schemes_scheme_code", table_name="classification_schemes")
    op.drop_table("classification_schemes")
