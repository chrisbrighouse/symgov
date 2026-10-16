"""The audit reader (backlog F1.5): organization scoping by entity, allow-listed payloads, safe CSV."""

from __future__ import annotations

from datetime import date, timedelta
import uuid

import pytest

from symgov_backend.audit_reader import (
    AuditReaderError, MAX_CSV_DAYS, ORGANIZATION_ENTITY_TYPES, audit_csv, csv_safe, read_audit, safe_payload,
)
from symgov_backend.models import (
    AuditEvent, CatalogApiKey, CatalogApiKeyHolder, OrganizationMembership, OrganizationRoleAssignment, Project, SymbolSet,
)

from usage_tables import NOW, seed_organization, seed_symbol, seed_user, usage_session_factory


@pytest.fixture()
def session():
    with usage_session_factory()() as value:
        yield value


def _audit(session, entity_type, entity_id, action="thing.happened", actor=None, payload=None, at=NOW):
    session.add(AuditEvent(
        id=uuid.uuid4(), entity_type=entity_type, entity_id=entity_id, action=action, actor_id=actor.id if actor else None,
        payload_json=payload or {}, created_at=at,
    ))


def _project(session, organization, creator, code="P-1"):
    row = Project(
        id=uuid.uuid4(), organization_id=organization.id, code=code, normalized_code=code.lower(), name="Plant", short_description="",
        status="active", metadata_json={}, created_by_user_id=creator.id, created_at=NOW, updated_at=NOW,
    )
    session.add(row)
    session.flush()
    return row


def _world(session):
    acme, other = seed_organization(session, "ACME"), seed_organization(session, "OTHER")
    admin, member = seed_user(session, "Admin"), seed_user(session, "Member")
    membership = OrganizationMembership(
        id=uuid.uuid4(), organization_id=acme.id, user_id=member.id, status="active", created_at=NOW, updated_at=NOW,
    )
    other_membership = OrganizationMembership(
        id=uuid.uuid4(), organization_id=other.id, user_id=admin.id, status="active", created_at=NOW, updated_at=NOW,
    )
    session.add_all([membership, other_membership])
    session.flush()
    return acme, other, admin, member, membership, other_membership


def test_an_organization_sees_only_events_about_its_own_entities(session):
    acme, other, admin, member, membership, other_membership = _world(session)
    mine, theirs = _project(session, acme, admin, "MINE"), _project(session, other, admin, "THEIRS")
    _audit(session, "organization", acme.id, "organization.updated", admin)
    _audit(session, "organization", other.id, "organization.updated", admin)
    _audit(session, "organization_membership", membership.id, "membership.deactivated", admin)
    _audit(session, "organization_membership", other_membership.id, "membership.deactivated", admin)
    _audit(session, "project", mine.id, "project.created", admin)
    _audit(session, "project", theirs.id, "project.created", admin)
    session.commit()

    page = read_audit(session, organization_id=acme.id)
    assert page["total"] == 3
    assert {item["entityId"] for item in page["items"]} == {str(acme.id), str(membership.id), str(mine.id)}
    assert read_audit(session, organization_id=other.id)["total"] == 3


def test_every_covered_entity_type_resolves_to_its_owner(session):
    acme, other, admin, member, membership, other_membership = _world(session)
    role = OrganizationRoleAssignment(id=uuid.uuid4(), membership_id=membership.id, base_role="user", is_active=True, assigned_at=NOW)
    owned_set = SymbolSet(
        id=uuid.uuid4(), owner_organization_id=acme.id, code="S1", normalized_code="s1", name="Set", status="active",
        disciplines_json=[], use_cases_json=[], created_by_user_id=admin.id, created_at=NOW, updated_at=NOW,
    )
    symbol = seed_symbol(session, admin, "own", organization=acme)
    foreign_symbol = seed_symbol(session, admin, "foreign", organization=other)
    key = CatalogApiKey(
        id=uuid.uuid4(), customer_name="c", integration_name="i", key_prefix="p", key_hash=uuid.uuid4().hex, scopes_json=[],
        status="active", allowed_origins_json=[], created_at=NOW, updated_at=NOW,
    )
    session.add_all([role, owned_set, key])
    session.flush()
    session.add(CatalogApiKeyHolder(api_key_id=key.id, key_kind="organization", holder_user_id=member.id, organization_id=acme.id))
    session.flush()
    for entity_type, entity_id in (
        ("organization_role_assignment", role.id), ("symbol_set", owned_set.id), ("governed_symbol", symbol.id), ("catalog_api_key", key.id),
    ):
        _audit(session, entity_type, entity_id)
    _audit(session, "governed_symbol", foreign_symbol.id)
    session.commit()
    page = read_audit(session, organization_id=acme.id)
    assert {item["entityType"] for item in page["items"]} == {"organization_role_assignment", "symbol_set", "governed_symbol", "catalog_api_key"}
    assert str(foreign_symbol.id) not in repr(page)


def test_entity_types_outside_the_covered_list_are_platform_only(session):
    acme, other, admin, *_ = _world(session)
    for entity_type in ("agent_finding", "review_case", "platform_role_assignment", "published_symbol"):
        _audit(session, entity_type, acme.id)
    _audit(session, "organization", acme.id)
    session.commit()
    assert read_audit(session, organization_id=acme.id)["total"] == 1
    assert read_audit(session, organization_id=acme.id, entity_type="agent_finding")["items"] == []
    assert read_audit(session, organization_id=None)["total"] == 5
    assert read_audit(session, organization_id=None, entity_type="agent_finding")["total"] == 1
    assert "agent_finding" not in ORGANIZATION_ENTITY_TYPES


def test_filters_paging_and_actor_names(session):
    acme, other, admin, *_ = _world(session)
    for index in range(5):
        _audit(session, "organization", acme.id, "organization.updated" if index % 2 else "organization.created", admin, at=NOW - timedelta(days=index))
    session.commit()
    first = read_audit(session, organization_id=acme.id, page=1, page_size=2)
    assert first["total"] == 5 and len(first["items"]) == 2 and first["items"][0]["actorEmail"] == admin.email
    assert first["items"][0]["occurredAt"] > first["items"][1]["occurredAt"]
    assert read_audit(session, organization_id=acme.id, action="organization.updated")["total"] == 2
    assert read_audit(session, organization_id=acme.id, since=(NOW - timedelta(days=1)).date())["total"] == 2
    assert read_audit(session, organization_id=acme.id, until=(NOW - timedelta(days=3)).date())["total"] == 2
    assert read_audit(session, organization_id=acme.id, actor_id=uuid.uuid4())["total"] == 0
    assert len(read_audit(session, organization_id=acme.id, page_size=9999)["items"]) == 5


def test_payloads_are_filtered_to_allowed_keys_and_small_values():
    payload = {
        "reason": "membership_deactivated", "pin_hash": "secret", "key_hash": "abc", "raw_key": "symgov_live_x", "email": "a@b.test",
        "affectedSymbolIds": [str(n) for n in range(50)], "before": {"status": "active", "token": "t"}, "name": "x" * 500,
        "scopes": ["catalog.read"], "unknown": {"deep": {"er": 1}},
    }
    cleaned = safe_payload(payload)
    assert set(cleaned) == {"reason", "affectedSymbolIds", "before", "name", "scopes"}
    assert len(cleaned["affectedSymbolIds"]) == 20 and len(cleaned["name"]) == 200
    assert safe_payload(None) == {} and safe_payload("x") == {}


def test_csv_cells_that_a_spreadsheet_would_run_are_neutralised():
    assert csv_safe("=HYPERLINK(\"http://evil\")") == "'=HYPERLINK(\"http://evil\")"
    assert csv_safe("+1") == "'+1" and csv_safe("-2") == "'-2" and csv_safe("@SUM(A1)") == "'@SUM(A1)"
    assert csv_safe("fine") == "fine" and csv_safe(None) == "" and csv_safe("a\nb") == "a b"


def test_the_csv_has_one_row_per_event_and_refuses_long_ranges(session):
    acme, other, admin, *_ = _world(session)
    admin.display_name = "=cmd|' /C calc'!A0"
    _audit(session, "organization", acme.id, "organization.updated", admin, {"reason": "r", "pin_hash": "leak"})
    session.commit()
    body = audit_csv(session, organization_id=acme.id, since=NOW.date(), until=NOW.date())
    lines = body.strip().splitlines()
    assert lines[0].startswith("occurred_at,entity_type") and len(lines) == 2
    assert "leak" not in body and "'=cmd" in body
    with pytest.raises(AuditReaderError):
        audit_csv(session, organization_id=acme.id, since=NOW.date() - timedelta(days=MAX_CSV_DAYS), until=NOW.date())
    with pytest.raises(AuditReaderError):
        audit_csv(session, organization_id=acme.id, since=NOW.date(), until=NOW.date() - timedelta(days=1))
    assert audit_csv(session, organization_id=acme.id, since=NOW.date() - timedelta(days=MAX_CSV_DAYS - 1), until=NOW.date())
