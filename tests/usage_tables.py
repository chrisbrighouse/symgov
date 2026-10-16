"""SQLite sessions with the usage and audit tables, for the WP-B tests.

PostgreSQL-only pieces (JSONB, `char_length`, regex checks) are swapped or
dropped while the tables are created, as the other SQLite suites do; the real
constraints are proved against PostgreSQL in the migration rehearsal.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, JSON, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.models import (
    AuditEvent,
    CatalogApiKey,
    CatalogApiKeyHolder,
    CatalogApiRateWindow,
    GovernedSymbol,
    Organization,
    OrganizationMemberCapability,
    OrganizationMembership,
    OrganizationRoleAssignment,
    OrganizationSymbolReviewSubmission,
    ProductUsageDailyRollup,
    ProductUsageEvent,
    Project,
    SymbolRevision,
    SymbolSet,
    SymbolSetItem,
    SymbolUsageDailyRollup,
    SymbolUsageEvent,
    User,
)

USAGE_MODELS = (
    User, Organization, OrganizationMembership, OrganizationRoleAssignment, OrganizationMemberCapability, Project, SymbolSet, SymbolSetItem,
    GovernedSymbol, SymbolRevision, OrganizationSymbolReviewSubmission, CatalogApiKey, CatalogApiKeyHolder, CatalogApiRateWindow,
    AuditEvent, SymbolUsageEvent, SymbolUsageDailyRollup, ProductUsageEvent, ProductUsageDailyRollup,
)
_PG_ONLY = ("~", "interval", "jsonb", "char_length", "::", "convert_to", "octet_length")


def create_tables(engine, models=USAGE_MODELS) -> None:
    for model in models:
        table = model.__table__
        constraints = table.constraints
        indexes = set(table.indexes)
        types = {c.name: c.type for c in table.columns}
        defaults = {c.name: c.server_default for c in table.columns}
        try:
            # SQLite ignores `postgresql_where`, which would make a partial unique index a full one.
            for index in list(table.indexes):
                if index.dialect_options["postgresql"]["where"] is not None:
                    table.indexes.discard(index)
            for column in table.columns:
                if column.type.__class__.__name__ == "JSONB":
                    column.type = JSON()
                    column.server_default = None
            table.constraints = {
                item for item in constraints
                if not (isinstance(item, CheckConstraint) and any(token in str(item.sqltext) for token in _PG_ONLY))
            }
            table.create(engine, checkfirst=True)
        finally:
            table.constraints = constraints
            table.indexes.update(indexes)
            for column in table.columns:
                column.type = types[column.name]
                column.server_default = defaults[column.name]


def usage_session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_tables(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# --- small seeders ---------------------------------------------------------------

from datetime import datetime, timezone  # noqa: E402
import uuid  # noqa: E402

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def seed_user(session, name="Person", *, active=True) -> User:
    user = User(
        id=uuid.uuid4(), email=f"{name.lower()}-{uuid.uuid4().hex[:6]}@example.test", display_name=f"{name} {uuid.uuid4().hex[:4]}",
        pin_hash="x", pin_set_at=NOW, is_active=active, created_at=NOW, updated_at=NOW,
    )
    session.add(user)
    session.flush()
    return user


def seed_organization(session, code="ACME") -> Organization:
    suffix = uuid.uuid4().hex[:6]
    organization = Organization(
        id=uuid.uuid4(), code=f"{code}-{suffix.upper()}", normalized_code=f"{code.lower()}-{suffix}", display_name=f"{code} {suffix}",
        name_key=uuid.uuid4().hex, entitlement_status="active", is_active=True, is_protected=False, fallback_icon_svg="<svg/>",
        created_at=NOW, updated_at=NOW,
    )
    session.add(organization)
    session.flush()
    return organization


def seed_symbol(session, owner: User, slug="valve", *, catalog_id=None, organization: Organization | None = None) -> GovernedSymbol:
    symbol = GovernedSymbol(
        id=uuid.uuid4(), slug=f"{slug}-{uuid.uuid4().hex[:6]}", canonical_name=slug.title(), category="equipment", discipline="piping",
        owner_id=owner.id, catalog_symbol_id=catalog_id, owner_organization_id=organization.id if organization else None,
        visibility="organization_private" if organization else "public", created_at=NOW, updated_at=NOW,
    )
    session.add(symbol)
    session.flush()
    return symbol
