import uuid
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from symgov_backend.models import Base, LLMUsageEvent


def test_llm_usage_migration_head_is_single_and_linear():
    cfg = Config("backend/alembic.ini")
    cfg.set_main_option("script_location", "backend/alembic")
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1
    assert heads[0] == "20260829_0033"

    rev = script.get_revision("20260730_0025")
    assert rev is not None
    assert rev.down_revision == "20260721_0024"


def test_llm_usage_event_orm_matches_table_name():
    assert LLMUsageEvent.__tablename__ == "llm_usage_events"
    table = Base.metadata.tables["llm_usage_events"]
    assert table is not None
    assert "event_id" in table.columns
    assert "trace_id" in table.columns
    assert "observation_id" in table.columns
