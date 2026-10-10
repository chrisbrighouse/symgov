"""catalog symbol embeddings, and the telemetry values that record their cost

Revision ID: 20261010_0071
Revises: 20261007_0070
Create Date: 2026-10-10 00:00:00.000000

Ed's Catalog search matches words. A meaning-based match needs one vector per
published public symbol revision, so this adds `catalog_symbol_embeddings`.
Vectors are ordinary bytes (little-endian float32, unit length), not a
pgvector column: production runs plain postgres:16, and a few thousand rows
are scored in the application. A row is keyed by (revision, model), so a new
model coexists with the old one until the old one is deleted, and a new
revision of a symbol is a new row. Deleting a revision or symbol deletes its
vectors.

`content_hash` is the SHA-256 of the exact text that was embedded, so the
indexer re-embeds only what changed.

Embedding calls are recorded in the LLM usage ledger like any other, so the
`use_case` constraint gains `catalog_embedding` and the `request_kind`
constraint gains `embedding`. As in 20260930_0067, the constraints are dropped
and created under their bare names, because the naming convention adds the
prefix both times.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261010_0071"
down_revision = "20261007_0070"
branch_labels = None
depends_on = None

NEW_USE_CASE_CHECK = (
    "use_case in ('workspace_chat', 'admin_llm_test', 'symbol_property_vision', 'vlad_graphic_edit', "
    "'ed_guru', 'catalog_embedding')"
)
OLD_USE_CASE_CHECK = (
    "use_case in ('workspace_chat', 'admin_llm_test', 'symbol_property_vision', 'vlad_graphic_edit', 'ed_guru')"
)
NEW_REQUEST_KIND_CHECK = "request_kind in ('text', 'vision', 'image_generation', 'embedding')"
OLD_REQUEST_KIND_CHECK = "request_kind in ('text', 'vision', 'image_generation')"


def upgrade() -> None:
    op.create_table(
        "catalog_symbol_embeddings",
        sa.Column(
            "symbol_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("symbol_revisions.id", ondelete="CASCADE", name="fk_catalog_symbol_embeddings_revision"),
            nullable=False,
        ),
        sa.Column(
            "governed_symbol_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("governed_symbols.id", ondelete="CASCADE", name="fk_catalog_symbol_embeddings_symbol"),
            nullable=False,
        ),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=False),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("symbol_revision_id", "model"),
        sa.CheckConstraint("dimensions between 1 and 8192", name="dimensions"),
        sa.CheckConstraint("length(embedding) = dimensions * 4", name="length"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="hash"),
    )
    op.create_index("ix_catalog_symbol_embeddings_model", "catalog_symbol_embeddings", ["model"])
    op.create_index("ix_catalog_symbol_embeddings_symbol", "catalog_symbol_embeddings", ["governed_symbol_id"])

    op.drop_constraint("llm_usage_events_use_case", "llm_usage_events", type_="check")
    op.create_check_constraint("llm_usage_events_use_case", "llm_usage_events", NEW_USE_CASE_CHECK)
    op.drop_constraint("llm_usage_events_request_kind", "llm_usage_events", type_="check")
    op.create_check_constraint("llm_usage_events_request_kind", "llm_usage_events", NEW_REQUEST_KIND_CHECK)


def downgrade() -> None:
    # The narrower constraints cannot hold the rows this migration made
    # possible, so they are deleted first, as in 20260930_0067.
    op.execute(
        sa.text(
            "DELETE FROM llm_usage_events WHERE use_case = 'catalog_embedding' OR request_kind = 'embedding'"
        )
    )
    op.drop_constraint("llm_usage_events_request_kind", "llm_usage_events", type_="check")
    op.create_check_constraint("llm_usage_events_request_kind", "llm_usage_events", OLD_REQUEST_KIND_CHECK)
    op.drop_constraint("llm_usage_events_use_case", "llm_usage_events", type_="check")
    op.create_check_constraint("llm_usage_events_use_case", "llm_usage_events", OLD_USE_CASE_CHECK)

    op.drop_index("ix_catalog_symbol_embeddings_symbol", table_name="catalog_symbol_embeddings")
    op.drop_index("ix_catalog_symbol_embeddings_model", table_name="catalog_symbol_embeddings")
    op.drop_table("catalog_symbol_embeddings")
