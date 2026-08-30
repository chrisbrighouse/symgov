from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from symgov_backend.models import AuthLoginAttemptEvent, AuthLoginThrottleBucket, AuthThrottleRecoveryEvent, UserSession

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "backend" / "alembic" / "versions" / "20260808_0027_account_security_invariants.py"


def test_account_security_migration_is_single_linear_head():
    cfg = Config("backend/alembic.ini")
    cfg.set_main_option("script_location", "backend/alembic")
    script = ScriptDirectory.from_config(cfg)

    assert script.get_heads() == ["20260829_0033"]
    revision = script.get_revision("20260808_0027")
    assert revision is not None
    assert revision.down_revision == "20260802_0026"


def test_account_security_migration_has_bounded_state_audit_and_downgrade_contract():
    source = MIGRATION.read_text(encoding="utf-8")

    assert '"auth_login_throttle_buckets"' in source
    assert '"auth_login_attempt_events"' in source
    assert '"auth_throttle_recovery_events"' in source
    assert 'sa.Column("request_metadata_json", sa.Text()' in source
    assert "postgresql.JSONB" not in source
    assert '"purpose"' in source
    assert "credential_change" in source
    assert "prevent_auth_security_event_mutation" in source
    assert "BEFORE UPDATE OR DELETE" in source
    assert "BEFORE TRUNCATE" in source
    assert 'op.drop_table("auth_throttle_recovery_events")' in source
    assert 'op.drop_table("auth_login_attempt_events")' in source
    assert 'op.drop_table("auth_login_throttle_buckets")' in source
    assert 'op.drop_column("user_sessions", "purpose")' in source


def test_account_security_models_match_storage_contract():
    assert UserSession.__table__.columns.purpose.server_default is not None
    assert AuthLoginThrottleBucket.__tablename__ == "auth_login_throttle_buckets"
    assert AuthLoginAttemptEvent.__tablename__ == "auth_login_attempt_events"
    assert AuthThrottleRecoveryEvent.__tablename__ == "auth_throttle_recovery_events"
    assert {column.name for column in AuthLoginAttemptEvent.__table__.columns} >= {
        "email_key_hash",
        "resolved_user_id",
        "client_ip_hash",
        "outcome",
        "failure_reason",
        "request_metadata_json",
    }
    for model in (AuthLoginThrottleBucket, AuthLoginAttemptEvent, AuthThrottleRecoveryEvent):
        assert all(len(constraint.name or "") <= 63 for constraint in model.__table__.constraints)
