from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "backend" / "alembic" / "versions" / "20260720_0023_user_subscriptions.py"
MODEL = ROOT / "backend" / "symgov_backend" / "models" / "schema.py"


def test_subscription_migration_chains_from_head_and_backfills_confirmed_policy():
    migration = MIGRATION.read_text(encoding="utf-8")
    model = MODEL.read_text(encoding="utf-8")

    assert 'revision: str = "20260720_0023"' in migration
    assert 'down_revision: Union[str, None] = "20260718_0022"' in migration
    assert 'OWNER_EMAIL = "chris.brighouse@hotmail.co.uk"' in migration
    assert "CREATE" not in migration  # Alembic operations remain dialect-managed.
    assert "DELETE FROM user_roles WHERE user_id NOT IN" in migration
    assert "SELECT id, 'admin'" in migration
    assert 'class UserSubscription(Base):' in model
    assert 'class SubscriptionEvent(Base):' in model
    assert 'deleted_at:' in model


def test_subscription_migration_explicitly_rejects_destructive_downgrade():
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "intentionally irreversible" in migration
    assert "permanently removes non-owner roles" in migration


def test_subscription_migration_disables_legacy_interactive_service_accounts():
    migration = MIGRATION.read_text(encoding="utf-8").lower()

    assert "ed@symgov.local" in migration
    assert "symgov-publication-service@symgov.local" in migration
    assert "disabled-service-account" in migration
    assert "is_active = false" in migration
