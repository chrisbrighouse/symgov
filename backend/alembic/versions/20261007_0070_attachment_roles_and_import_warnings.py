"""attachment roles for state variants, a private place for import warnings, node aliases

Revision ID: 20261007_0070
Revises: 20261004_0069
Create Date: 2026-10-07 00:00:00.000000

`attachments.asset_role` says whether a stored file is a symbol revision's
primary drawing or one of its option (state) variants, and `option_index` and
`condition` say which variant and under what condition it applies, so the
catalogue can tell an option SVG from the primary without reading JSON.
Existing rows take the default `primary`, which changes nothing: no code read
a role from this table before.

`source_package_entries.import_warnings_json` holds what an importer noticed
about one entry. It is a private field: `symbol_revisions.payload_json` is
served to the public API as it is, so a warning for a reviewer cannot live
there.

`classification_nodes.aliases_json` and `.source_label` let an imported scheme
keep the name its source gave a node (its code grammar is upper-case snake, so
the source's CamelCase name cannot be the code) and say where the node came
from.

Like the generation columns of `20260925_0064`, none of these columns is mapped
in the ORM models and none carries a check constraint: many disposable-PostgreSQL
fixtures pin older heads, and an ORM column those databases lack would break
every query on these tables in them. The server defaults keep ORM inserts
valid, and the importer validates what it writes (`services/disc_dexpi_ingestion`).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261007_0070"
down_revision: Union[str, None] = "20261004_0069"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "attachments",
        sa.Column("asset_role", sa.Text(), nullable=False, server_default=sa.text("'primary'")),
    )
    op.add_column("attachments", sa.Column("option_index", sa.Integer(), nullable=True))
    op.add_column("attachments", sa.Column("condition", sa.Text(), nullable=True))
    op.add_column(
        "source_package_entries",
        sa.Column(
            "import_warnings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "classification_nodes",
        sa.Column(
            "aliases_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("classification_nodes", sa.Column("source_label", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("classification_nodes", "source_label")
    op.drop_column("classification_nodes", "aliases_json")
    op.drop_column("source_package_entries", "import_warnings_json")
    op.drop_column("attachments", "condition")
    op.drop_column("attachments", "option_index")
    op.drop_column("attachments", "asset_role")
