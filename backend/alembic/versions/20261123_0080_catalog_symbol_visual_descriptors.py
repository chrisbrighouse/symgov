"""catalog symbol visual descriptors: the index behind visual symbol matching

Revision ID: 20261123_0080
Revises: 20261025_0079
Create Date: 2026-11-23 00:00:00.000000

Visual matching ranks a pasted image against the symbols a person may search.
Each published public symbol revision gets one row per matcher version holding
everything the matcher needs without touching storage at query time:

- `gemini_embedding`: the stage-1 image embedding (little-endian float32, unit
  length, the 768-value prefix). NULL when the embedding call failed; the
  local descriptor still serves the D19 fallback and the worker retries.
- `local_descriptor`: the 360-value hand-made descriptor (float32, unit length).
- `skeleton`: the 128 x 128 centreline as a bit array (2048 bytes) and
  `content_aspect`, which the exact rerank needs, and the 256 x 256 centreline
  (`skeleton_fine`, 8192 bytes) that the interchangeable-symbol grouping needs.
- `asset_object_key`, `source_sha256`, `renderer_version`, `font_set_id`: what
  the row was built from. A row is current while the object key, renderer and
  font set are unchanged, so the worker never refetches a current symbol.

Like the text embeddings (20261010_0071) the vectors are plain bytes, not a
pgvector column (D11 is deferred). A row is keyed by (revision, matcher
version), so a new matcher version builds alongside the old one. `scope` is
'public' for now; organization-private matching (D5) will add a second value.
Deleting a revision or symbol deletes its rows. Check constraint names are
bare, because the naming convention adds the prefix.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261123_0080"
down_revision = "20261025_0079"
branch_labels = None
depends_on = None

TABLE = "catalog_symbol_visual_descriptors"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column(
            "symbol_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("symbol_revisions.id", ondelete="CASCADE", name="fk_catalog_symbol_visual_descriptors_revision"),
            nullable=False,
        ),
        sa.Column(
            "governed_symbol_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("governed_symbols.id", ondelete="CASCADE", name="fk_catalog_symbol_visual_descriptors_symbol"),
            nullable=False,
        ),
        sa.Column("matcher_version", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False, server_default="public"),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("dimensions", sa.Integer(), nullable=True),
        sa.Column("gemini_embedding", sa.LargeBinary(), nullable=True),
        sa.Column("local_descriptor", sa.LargeBinary(), nullable=False),
        sa.Column("skeleton", sa.LargeBinary(), nullable=False),
        sa.Column("skeleton_fine", sa.LargeBinary(), nullable=False),
        sa.Column("content_aspect", sa.Float(), nullable=False),
        sa.Column("asset_object_key", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.Text(), nullable=False),
        sa.Column("renderer_version", sa.Text(), nullable=False),
        sa.Column("font_set_id", sa.Text(), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("symbol_revision_id", "matcher_version"),
        sa.CheckConstraint("scope in ('public')", name="scope"),
        sa.CheckConstraint(
            "(gemini_embedding IS NULL AND dimensions IS NULL AND embedding_model IS NULL) "
            "OR (gemini_embedding IS NOT NULL AND dimensions BETWEEN 1 AND 8192 AND embedding_model IS NOT NULL "
            "AND length(gemini_embedding) = dimensions * 4)",
            name="embedding",
        ),
        sa.CheckConstraint("length(local_descriptor) > 0 AND length(local_descriptor) % 4 = 0", name="local"),
        sa.CheckConstraint("length(skeleton) = 2048", name="skeleton"),
        sa.CheckConstraint("length(skeleton_fine) = 8192", name="fine"),
        sa.CheckConstraint("content_aspect > 0", name="aspect"),
        sa.CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$'", name="hash"),
    )
    op.create_index("ix_catalog_symbol_visual_descriptors_matcher", TABLE, ["matcher_version", "scope"])
    op.create_index("ix_catalog_symbol_visual_descriptors_symbol", TABLE, ["governed_symbol_id"])


def downgrade() -> None:
    op.drop_index("ix_catalog_symbol_visual_descriptors_symbol", table_name=TABLE)
    op.drop_index("ix_catalog_symbol_visual_descriptors_matcher", table_name=TABLE)
    op.drop_table(TABLE)
