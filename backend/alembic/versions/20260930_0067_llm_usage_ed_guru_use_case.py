"""allow the ed_guru use case in llm_usage_events

Revision ID: 20260930_0067
Revises: 20260926_0066
Create Date: 2026-09-30 00:00:00.000000

Ed's provider calls send use_case `ed_guru`, which neither the telemetry
allowlist nor this check constraint named, so every Ed call was dropped from
the usage ledger and the telemetry export (live evaluation, 2026-09-30).

The constraint is created and dropped under the bare name `20260730_0025`
used; the naming convention adds the same prefix both times.
"""

from alembic import op
import sqlalchemy as sa

revision = "20260930_0067"
down_revision = "20260926_0066"
branch_labels = None
depends_on = None

NEW_USE_CASE_CHECK = (
    "use_case in ('workspace_chat', 'admin_llm_test', 'symbol_property_vision', 'vlad_graphic_edit', 'ed_guru')"
)
OLD_USE_CASE_CHECK = (
    "use_case in ('workspace_chat', 'admin_llm_test', 'symbol_property_vision', 'vlad_graphic_edit')"
)


def upgrade() -> None:
    op.drop_constraint("llm_usage_events_use_case", "llm_usage_events", type_="check")
    op.create_check_constraint("llm_usage_events_use_case", "llm_usage_events", NEW_USE_CASE_CHECK)


def downgrade() -> None:
    # As in 20260904_0039: the narrower constraint cannot hold the rows this
    # migration made possible, so they are deleted first rather than letting
    # the constraint creation fail.
    op.execute(sa.text("DELETE FROM llm_usage_events WHERE use_case = 'ed_guru'"))
    op.drop_constraint("llm_usage_events_use_case", "llm_usage_events", type_="check")
    op.create_check_constraint("llm_usage_events_use_case", "llm_usage_events", OLD_USE_CASE_CHECK)
