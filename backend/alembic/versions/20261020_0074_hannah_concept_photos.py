"""hannah concept targets and concept photos

Revision ID: 20261020_0074
Revises: 20261015_0073
Create Date: 2026-10-20 00:00:00.000000

Hannah now finds real-world photos per semantic concept instead of per symbol
(plan: docs/plans/2026-10-19-hannah-concept-photo-curation-plan.md), so every
symbol on a concept shares one photo set.

`hannah_concept_targets` holds Hannah's own view of a concept: whether it can
be photographed and on what basis, the plain search terms, and the Wikidata
item and Commons category it was linked to. It deliberately does not touch
`semantic_concepts.concept_kind` or `concept_external_references`, which are
under concept governance (Chris, 2026-10-19, Q3).

`hannah_concept_photos` holds each candidate image with its credit, the
vision model's verdict, and the steward's (or, later, Hannah's own) decision.
An approved photo is stored in object storage under `object_key`; there is no
`attachments` row, because attachment parents are revisions and these belong
to concepts.

The `llm_usage_events` checks gain the use cases `hannah_vision` and
`hannah_targets` and the agent slug `hannah`, widened under their existing
names as in `20260930_0067`.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261020_0074"
down_revision = "20261015_0073"
branch_labels = None
depends_on = None

NEW_USE_CASE_CHECK = (
    "use_case in ('workspace_chat', 'admin_llm_test', 'symbol_property_vision', 'vlad_graphic_edit', 'ed_guru', "
    "'catalog_embedding', 'hannah_vision', 'hannah_targets')"
)
OLD_USE_CASE_CHECK = (
    "use_case in ('workspace_chat', 'admin_llm_test', 'symbol_property_vision', 'vlad_graphic_edit', 'ed_guru', "
    "'catalog_embedding')"
)
NEW_AGENT_SLUG_CHECK = "agent_slug is null or agent_slug in ('libby', 'vlad', 'ed', 'hannah')"
OLD_AGENT_SLUG_CHECK = "agent_slug is null or agent_slug in ('libby', 'vlad', 'ed')"


def upgrade() -> None:
    op.create_table(
        "hannah_concept_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "semantic_concept_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("semantic_concepts.id", ondelete="RESTRICT", name="fk_hannah_concept_targets_concept"),
            nullable=False,
        ),
        sa.Column("photographable", sa.Text()),
        sa.Column("photographable_basis", sa.Text()),
        sa.Column("search_terms_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("wikidata_qid", sa.Text()),
        sa.Column("commons_category", sa.Text()),
        sa.Column("wikipedia_url", sa.Text()),
        sa.Column("link_confidence", sa.Numeric(5, 4)),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("next_eligible_at", sa.DateTime(timezone=True)),
        sa.Column("notes_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "updated_by_user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_hannah_concept_targets_updated_by"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("photographable is null or photographable in ('yes', 'no', 'unsure')", name="photographable"),
        sa.CheckConstraint(
            "photographable_basis is null or photographable_basis in ('concept_kind', 'llm', 'steward')",
            name="photographable_basis",
        ),
        sa.CheckConstraint("(photographable is null) = (photographable_basis is null)", name="basis_with_decision"),
        sa.CheckConstraint(
            "status in ('pending', 'searched', 'no_candidates', 'has_proposals', 'has_photos', 'excluded')",
            name="status",
        ),
        sa.CheckConstraint("link_confidence is null or (link_confidence >= 0 and link_confidence <= 1)", name="link_confidence"),
        sa.CheckConstraint("attempt_count >= 0", name="attempt_count"),
    )
    op.create_index(
        "uq_hannah_concept_targets_concept", "hannah_concept_targets", ["semantic_concept_id"], unique=True
    )
    op.create_index(
        "ix_hannah_concept_targets_status_eligible", "hannah_concept_targets", ["status", "next_eligible_at"]
    )

    op.create_table(
        "hannah_concept_photos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "semantic_concept_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("semantic_concepts.id", ondelete="RESTRICT", name="fk_hannah_concept_photos_concept"),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("author", sa.Text()),
        sa.Column("license_label", sa.Text()),
        sa.Column("license_url", sa.Text()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("sha256", sa.Text()),
        sa.Column("vision_model", sa.Text()),
        sa.Column("vision_verdict_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("score", sa.Numeric(5, 4)),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'candidate'")),
        sa.Column("filter_reasons_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("rank", sa.Integer()),
        sa.Column("object_key", sa.Text()),
        sa.Column("content_type", sa.Text()),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column(
            "decided_by_user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_hannah_concept_photos_decided_by"),
        ),
        sa.Column("decided_by_agent", sa.Text()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column(
            "queue_item_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_queue_items.id", ondelete="SET NULL", name="fk_hannah_concept_photos_queue_item"),
        ),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source in ('wikidata_image', 'commons_category', 'commons_search', 'wikipedia_lead', 'web_search')",
            name="source",
        ),
        sa.CheckConstraint(
            "status in ('candidate', 'filtered', 'proposed', 'approved', 'rejected', 'removed')",
            name="status",
        ),
        sa.CheckConstraint("score is null or (score >= 0 and score <= 1)", name="score"),
        sa.CheckConstraint("decided_by_agent is null or decided_by_agent = 'hannah'", name="decided_by_agent"),
        sa.CheckConstraint(
            "status not in ('approved', 'rejected', 'removed') or decided_at is not null",
            name="decision_is_dated",
        ),
        sa.CheckConstraint("status <> 'approved' or object_key is not null", name="approved_has_object"),
    )
    op.create_index(
        "uq_hannah_concept_photos_concept_image", "hannah_concept_photos", ["semantic_concept_id", "image_url"], unique=True
    )
    op.create_index(
        "ix_hannah_concept_photos_concept_status_rank", "hannah_concept_photos", ["semantic_concept_id", "status", "rank"]
    )
    op.create_index("ix_hannah_concept_photos_status_seen", "hannah_concept_photos", ["status", "last_seen_at"])

    op.drop_constraint("llm_usage_events_use_case", "llm_usage_events", type_="check")
    op.create_check_constraint("llm_usage_events_use_case", "llm_usage_events", NEW_USE_CASE_CHECK)
    op.drop_constraint("llm_usage_events_agent_slug", "llm_usage_events", type_="check")
    op.create_check_constraint("llm_usage_events_agent_slug", "llm_usage_events", NEW_AGENT_SLUG_CHECK)


def downgrade() -> None:
    # As in 20260930_0067: the narrower checks cannot hold Hannah's usage rows,
    # so they are deleted first rather than letting the constraint creation fail.
    op.execute(
        sa.text(
            "DELETE FROM llm_usage_events "
            "WHERE use_case in ('hannah_vision', 'hannah_targets') OR agent_slug = 'hannah'"
        )
    )
    op.drop_constraint("llm_usage_events_agent_slug", "llm_usage_events", type_="check")
    op.create_check_constraint("llm_usage_events_agent_slug", "llm_usage_events", OLD_AGENT_SLUG_CHECK)
    op.drop_constraint("llm_usage_events_use_case", "llm_usage_events", type_="check")
    op.create_check_constraint("llm_usage_events_use_case", "llm_usage_events", OLD_USE_CASE_CHECK)

    op.drop_index("ix_hannah_concept_photos_status_seen", table_name="hannah_concept_photos")
    op.drop_index("ix_hannah_concept_photos_concept_status_rank", table_name="hannah_concept_photos")
    op.drop_index("uq_hannah_concept_photos_concept_image", table_name="hannah_concept_photos")
    op.drop_table("hannah_concept_photos")
    op.drop_index("ix_hannah_concept_targets_status_eligible", table_name="hannah_concept_targets")
    op.drop_index("uq_hannah_concept_targets_concept", table_name="hannah_concept_targets")
    op.drop_table("hannah_concept_targets")
