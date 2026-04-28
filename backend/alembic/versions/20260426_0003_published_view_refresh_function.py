from __future__ import annotations

from alembic import op


revision = "20260426_0003"
down_revision = "20260419_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION refresh_published_symbol_views()
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $$
        BEGIN
            REFRESH MATERIALIZED VIEW published_symbol_views;
        END;
        $$;
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION refresh_published_symbol_views() TO symgov_app")


def downgrade() -> None:
    op.execute("REVOKE EXECUTE ON FUNCTION refresh_published_symbol_views() FROM symgov_app")
    op.execute("DROP FUNCTION refresh_published_symbol_views()")
