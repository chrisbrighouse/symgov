"""Stage 6 WP6.2 — Symbol Set Builder search.

SQLite-backed route-level tests (mirrors `test_effective_palette.py`'s
pattern): the public-catalog half of the search
(`symbol_set_builder._search_public_symbols`) is Postgres-only raw SQL
(`PUBLISHED_SYMBOLS_SQL`), so it is monkeypatched here; the organization
half is plain ORM and is exercised for real against SQLite.
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import CheckConstraint, JSON

from test_projects_api import _stage4_client
from test_symbol_set_items import _ensure_symbol_tables
from symgov_backend.models import GovernedSymbol, Organization, OrganizationMemberCapability, OrganizationMembership, OrganizationSymbolReviewDecision, OrganizationSymbolReviewSubmission, SymbolRevision, User
import symgov_backend.symbol_set_builder as symbol_set_builder_module


def _grant_capability(Session, organization_id, user_id, capability):
    with Session() as session:
        membership = session.query(OrganizationMembership).filter(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user_id,
        ).one()
        session.add(OrganizationMemberCapability(
            id=uuid.uuid4(), membership_id=membership.id, capability=capability,
            is_active=True, granted_at=datetime.now(timezone.utc).replace(microsecond=0),
        ))
        session.commit()


def _organization_id(Session) -> uuid.UUID:
    with Session() as session:
        return session.query(Organization).one().id


def _fake_public_entries(entries):
    def fake(session, *, query_text):
        if query_text:
            return [entry for entry in entries if query_text.lower() in entry["canonicalName"].lower()]
        return list(entries)
    return fake


def _approved_organization_symbol(Session, organization_id, canonical_name, *, organization_wide=False):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        owner = session.query(User).first()
        symbol_id = uuid.uuid4()
        revision_id = uuid.uuid4()
        submission_id = uuid.uuid4()
        decision_id = uuid.uuid4()
        session.add(GovernedSymbol(
            id=symbol_id, slug=canonical_name.lower().replace(" ", "-"), canonical_name=canonical_name,
            category="fire", discipline="fire-safety", owner_id=owner.id,
            owner_organization_id=organization_id, visibility="organization_private",
            organization_wide=organization_wide, current_revision_id=revision_id,
            created_at=now, updated_at=now,
        ))
        session.add(SymbolRevision(
            id=revision_id, symbol_id=symbol_id, revision_label="1", lifecycle_state="approved",
            payload_json={}, author_id=owner.id, created_at=now,
        ))
        session.add(OrganizationSymbolReviewSubmission(
            id=submission_id, organization_id=organization_id, governed_symbol_id=symbol_id,
            symbol_revision_id=revision_id, submitted_by_user_id=owner.id, status="closed",
            submitted_at=now, closed_at=now,
        ))
        session.add(OrganizationSymbolReviewDecision(
            id=decision_id, submission_id=submission_id, organization_id=organization_id,
            governed_symbol_id=symbol_id, symbol_revision_id=revision_id, decided_by_user_id=owner.id,
            decision="approved", decided_at=now,
        ))
        session.commit()
        return symbol_id


def _draft_organization_symbol(Session, organization_id, canonical_name):
    """An organization-private symbol with no approved decision -- must
    never appear in Builder search results."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        owner = session.query(User).first()
        symbol_id = uuid.uuid4()
        revision_id = uuid.uuid4()
        session.add(GovernedSymbol(
            id=symbol_id, slug=canonical_name.lower().replace(" ", "-"), canonical_name=canonical_name,
            category="fire", discipline="fire-safety", owner_id=owner.id,
            owner_organization_id=organization_id, visibility="organization_private",
            organization_wide=False, current_revision_id=revision_id,
            created_at=now, updated_at=now,
        ))
        session.add(SymbolRevision(
            id=revision_id, symbol_id=symbol_id, revision_label="1", lifecycle_state="draft",
            payload_json={}, author_id=owner.id, created_at=now,
        ))
        session.commit()
        return symbol_id


def _ensure_review_tables(Session):
    bind = Session.kw["bind"]
    for model in (SymbolRevision, OrganizationSymbolReviewSubmission, OrganizationSymbolReviewDecision):
        table = model.__table__
        original_constraints = table.constraints
        original_types = {column.name: column.type for column in table.columns}
        try:
            for column in table.columns:
                if column.type.__class__.__name__ == "JSONB":
                    column.type = JSON()
            table.constraints = {
                item for item in original_constraints
                if not (isinstance(item, CheckConstraint) and any(
                    token in str(item.sqltext) for token in ("btrim", "char_length", "jsonb")
                ))
            }
            table.create(bind, checkfirst=True)
        finally:
            table.constraints = original_constraints
            for column in table.columns:
                column.type = original_types[column.name]


def test_builder_search_shows_only_public_symbols_to_a_plain_member(monkeypatch):
    client, Session = _stage4_client(role="user")
    _ensure_symbol_tables(Session)
    _ensure_review_tables(Session)
    organization_id = _organization_id(Session)
    public_id = uuid.uuid4()
    monkeypatch.setattr(symbol_set_builder_module, "_search_public_symbols", _fake_public_entries([
        {"governedSymbolId": public_id, "source": "public", "canonicalName": "Public Fire Alarm", "category": "fire",
         "discipline": "fire-safety", "slug": "public-fire-alarm", "organizationWide": None, "currentRevisionId": uuid.uuid4()},
    ]))
    _approved_organization_symbol(Session, organization_id, "Org Approved Symbol")

    response = client.get("/api/v1/org/me/symbol-sets/builder-search")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["source"] == "public"
    assert body["items"][0]["governedSymbolId"] == str(public_id)


def test_builder_search_shows_organization_symbols_to_admin(monkeypatch):
    client, Session = _stage4_client(role="admin")
    _ensure_symbol_tables(Session)
    _ensure_review_tables(Session)
    organization_id = _organization_id(Session)
    monkeypatch.setattr(symbol_set_builder_module, "_search_public_symbols", _fake_public_entries([]))
    org_symbol_id = _approved_organization_symbol(Session, organization_id, "Org Approved Symbol")

    response = client.get("/api/v1/org/me/symbol-sets/builder-search")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["source"] == "organization"
    assert body["items"][0]["governedSymbolId"] == str(org_symbol_id)
    assert body["items"][0]["organizationWide"] is False


def test_builder_search_shows_organization_symbols_to_symbol_reviewer_non_admin(monkeypatch):
    client, Session = _stage4_client(role="user")
    _ensure_symbol_tables(Session)
    _ensure_review_tables(Session)
    organization_id = _organization_id(Session)
    with Session() as session:
        membership = session.query(OrganizationMembership).filter(
            OrganizationMembership.organization_id == organization_id,
        ).one()
        user_id = membership.user_id
    _grant_capability(Session, organization_id, user_id, "symbol_reviewer")
    monkeypatch.setattr(symbol_set_builder_module, "_search_public_symbols", _fake_public_entries([]))
    org_symbol_id = _approved_organization_symbol(Session, organization_id, "Reviewer Visible Symbol")

    response = client.get("/api/v1/org/me/symbol-sets/builder-search")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["governedSymbolId"] == str(org_symbol_id)


def test_builder_search_excludes_draft_organization_symbols(monkeypatch):
    client, Session = _stage4_client(role="admin")
    _ensure_symbol_tables(Session)
    _ensure_review_tables(Session)
    organization_id = _organization_id(Session)
    monkeypatch.setattr(symbol_set_builder_module, "_search_public_symbols", _fake_public_entries([]))
    _draft_organization_symbol(Session, organization_id, "Unapproved Draft Symbol")

    response = client.get("/api/v1/org/me/symbol-sets/builder-search")
    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_builder_search_query_filters_organization_half(monkeypatch):
    client, Session = _stage4_client(role="admin")
    _ensure_symbol_tables(Session)
    _ensure_review_tables(Session)
    organization_id = _organization_id(Session)
    monkeypatch.setattr(symbol_set_builder_module, "_search_public_symbols", _fake_public_entries([]))
    _approved_organization_symbol(Session, organization_id, "Alpha Beacon")
    _approved_organization_symbol(Session, organization_id, "Zeta Sprinkler")

    response = client.get("/api/v1/org/me/symbol-sets/builder-search", params={"q": "beacon"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["canonicalName"] == "Alpha Beacon"


def test_builder_search_merges_and_sorts_deterministically_across_sources(monkeypatch):
    client, Session = _stage4_client(role="admin")
    _ensure_symbol_tables(Session)
    _ensure_review_tables(Session)
    organization_id = _organization_id(Session)
    monkeypatch.setattr(symbol_set_builder_module, "_search_public_symbols", _fake_public_entries([
        {"governedSymbolId": uuid.uuid4(), "source": "public", "canonicalName": "B Public Symbol", "category": "fire",
         "discipline": "fire-safety", "slug": "b-public-symbol", "organizationWide": None, "currentRevisionId": uuid.uuid4()},
    ]))
    _approved_organization_symbol(Session, organization_id, "A Org Symbol")
    _approved_organization_symbol(Session, organization_id, "C Org Symbol")

    response = client.get("/api/v1/org/me/symbol-sets/builder-search")
    assert response.status_code == 200
    names = [item["canonicalName"] for item in response.json()["items"]]
    assert names == ["A Org Symbol", "B Public Symbol", "C Org Symbol"]


def test_builder_search_pagination_is_bounded(monkeypatch):
    client, Session = _stage4_client(role="admin")
    _ensure_symbol_tables(Session)
    _ensure_review_tables(Session)
    organization_id = _organization_id(Session)
    monkeypatch.setattr(symbol_set_builder_module, "_search_public_symbols", _fake_public_entries([]))
    for index in range(5):
        _approved_organization_symbol(Session, organization_id, f"Symbol {index:02d}")

    first_page = client.get("/api/v1/org/me/symbol-sets/builder-search", params={"pageSize": 2}).json()
    assert first_page["total"] == 5
    assert len(first_page["items"]) == 2
