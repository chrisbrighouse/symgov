"""catalog API key holders and per-minute rate windows

Revision ID: 20261014_0072
Revises: 20261010_0071
Create Date: 2026-10-14 00:00:00.000000

A Catalog API key had a `created_by` and nothing else, so nothing said whose
key it was in the sense that matters when the person leaves, and nothing could
tell a personal key from one issued to a person on an organization's behalf.
`catalog_api_key_holders` records that: one row per key, `personal` (no
organization) or `organization` (issued by an admin to a member, claimed by the
holder). Every key that has an owner today becomes a personal key held by that
owner.

A key with no `created_by` was made by the operator CLI for nobody in
particular. It has no holder, so it can no longer be tied to a person who can
be deactivated, and by decision (2026-10-13) it is revoked here, with one audit
event each. Downgrading drops the tables and does not un-revoke those keys.

`catalog_api_rate_windows` counts a key's requests per minute for the enforced
`rate_limit_per_minute`. Old windows are deleted by the limiter itself.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261014_0072"
down_revision = "20261010_0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalog_api_key_holders",
        sa.Column(
            "api_key_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("catalog_api_keys.id", ondelete="CASCADE", name="fk_catalog_api_key_holders_key"),
            primary_key=True,
        ),
        sa.Column("key_kind", sa.Text(), nullable=False),
        sa.Column(
            "holder_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", name="fk_catalog_api_key_holders_holder"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", name="fk_catalog_api_key_holders_organization"),
            nullable=True,
        ),
        sa.Column(
            "issued_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", name="fk_catalog_api_key_holders_issuer"),
            nullable=True,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("key_kind in ('personal', 'organization')", name="key_kind"),
        sa.CheckConstraint("(key_kind = 'personal') = (organization_id is null)", name="kind_matches_organization"),
    )
    op.create_index(
        "ix_catalog_api_key_holders_organization_kind", "catalog_api_key_holders", ["organization_id", "key_kind"]
    )
    op.create_index("ix_catalog_api_key_holders_holder", "catalog_api_key_holders", ["holder_user_id"])

    op.create_table(
        "catalog_api_rate_windows",
        sa.Column(
            "api_key_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("catalog_api_keys.id", ondelete="CASCADE", name="fk_catalog_api_rate_windows_key"),
            primary_key=True,
        ),
        sa.Column("window_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("request_count >= 0", name="request_count"),
    )

    op.execute(
        sa.text(
            """
            INSERT INTO catalog_api_key_holders (api_key_id, key_kind, holder_user_id, claimed_at)
            SELECT id, 'personal', created_by, created_at
            FROM catalog_api_keys
            WHERE created_by IS NOT NULL
            """
        )
    )
    # Ownerless keys: one audit event each, then revoke. The audit insert reads
    # the rows before the update changes their status.
    op.execute(
        sa.text(
            """
            INSERT INTO audit_events (id, entity_type, entity_id, action, actor_id, payload_json, created_at)
            SELECT gen_random_uuid(), 'catalog_api_key', id, 'catalog_api_key.revoked', NULL,
                   jsonb_build_object(
                       'actor_type', 'migration',
                       'reason', 'legacy_key_without_owner',
                       'api_key_id', id::text,
                       'key_prefix', key_prefix
                   ),
                   now()
            FROM catalog_api_keys
            WHERE created_by IS NULL AND status = 'active' AND revoked_at IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE catalog_api_keys
            SET status = 'revoked', revoked_at = now(), updated_at = now()
            WHERE created_by IS NULL AND status = 'active' AND revoked_at IS NULL
            """
        )
    )


def downgrade() -> None:
    # Keys revoked in upgrade() stay revoked.
    op.drop_table("catalog_api_rate_windows")
    op.drop_index("ix_catalog_api_key_holders_holder", table_name="catalog_api_key_holders")
    op.drop_index("ix_catalog_api_key_holders_organization_kind", table_name="catalog_api_key_holders")
    op.drop_table("catalog_api_key_holders")
