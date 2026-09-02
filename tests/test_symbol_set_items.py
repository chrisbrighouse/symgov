from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import CheckConstraint, JSON

from test_projects_api import _stage4_client
from symgov_backend.models import AuditEvent, GovernedSymbol, SymbolSetItem, User
import symgov_backend.symbol_set_service as symbol_set_service


def _active_set(client, code="SET-01"):
    created = client.post(
        "/api/v1/org/me/symbol-sets",
        json={"code": code, "name": "Electrical"},
    )
    assert created.status_code == 201
    set_id = created.json()["id"]
    activated = client.patch(
        f"/api/v1/org/me/symbol-sets/{set_id}",
        json={"status": "active"},
    )
    assert activated.status_code == 200
    return set_id


def _ensure_symbol_tables(Session):
    bind = Session.kw["bind"]

    for model in (GovernedSymbol, SymbolSetItem):
        table = model.__table__
        original_constraints = table.constraints
        original_types = {column.name: column.type for column in table.columns}
        original_defaults = {column.name: column.server_default for column in table.columns}
        try:
            for column in table.columns:
                if column.type.__class__.__name__ == "JSONB":
                    column.type = JSON()
                    column.server_default = None
            table.constraints = {
                item for item in original_constraints
                if not isinstance(item, CheckConstraint)
                or not any(token in str(item.sqltext) for token in ("jsonb", "char_length", "convert_to"))
            }
            table.create(bind, checkfirst=True)
        finally:
            table.constraints = original_constraints
            for column in table.columns:
                column.type = original_types[column.name]
                column.server_default = original_defaults[column.name]


def _symbol(Session, slug, identifier=None):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        owner = session.query(User).first()
        row = GovernedSymbol(
            id=identifier or uuid.uuid4(),
            slug=slug,
            canonical_name=slug,
            category="test",
            discipline="test",
            owner_id=owner.id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        return row.id


def _eligibility(monkeypatch, values):
    monkeypatch.setattr(
        symbol_set_service,
        "current_public_symbols",
        lambda session, ids: {symbol_id: values[symbol_id] for symbol_id in ids if symbol_id in values},
    )


def test_item_replacement_is_ordered_and_uses_current_revision_availability(monkeypatch):
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    set_id = _active_set(client)
    first_id = _symbol(Session, "first-symbol")
    second_id = _symbol(Session, "second-symbol")
    first_revision = uuid.uuid4()
    second_revision = uuid.uuid4()
    _eligibility(monkeypatch, {first_id: first_revision, second_id: second_revision})

    response = client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/items",
        json={
            "items": [
                {"governedSymbolId": str(second_id), "sortOrder": 2},
                {"governedSymbolId": str(first_id), "sortOrder": 1, "provenance": {"source": "test"}},
            ]
        },
    )

    assert response.status_code == 200
    assert [item["governedSymbolId"] for item in response.json()["items"]] == [str(first_id), str(second_id)]
    with Session() as session:
        event = session.query(AuditEvent).filter(AuditEvent.action == "symbol_set.items_replaced").one()
        assert event.payload_json["affectedSymbolIds"] == sorted([str(first_id), str(second_id)])
        assert event.payload_json["beforeItemCount"] == 0
        assert event.payload_json["afterItemCount"] == 2
        assert "provenance" not in event.payload_json
    assert all(
        key in response.json()["items"][0]
        for key in (
            "groupName",
            "displayLabel",
            "notes",
            "preferredFormat",
            "provenance",
            "currentRevisionId",
            "availabilityStatus",
            "availabilityReason",
        )
    )

    replacement_revision = uuid.uuid4()
    _eligibility(monkeypatch, {first_id: replacement_revision})
    current = client.get(f"/api/v1/org/me/symbol-sets/{set_id}/items")
    assert current.status_code == 200
    by_id = {item["governedSymbolId"]: item for item in current.json()["items"]}
    assert by_id[str(first_id)]["currentRevisionId"] == str(replacement_revision)
    assert by_id[str(first_id)]["availabilityStatus"] == "active"
    assert by_id[str(second_id)]["currentRevisionId"] is None
    assert by_id[str(second_id)]["availabilityStatus"] == "unavailable"
    assert by_id[str(second_id)]["availabilityReason"]

    removed = client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/items",
        json={"items": [{"governedSymbolId": str(first_id), "sortOrder": 1}]},
    )
    assert removed.status_code == 200
    with Session() as session:
        assert session.get(GovernedSymbol, second_id) is not None
        assert session.query(SymbolSetItem).filter_by(symbol_set_id=uuid.UUID(set_id), governed_symbol_id=second_id).one_or_none() is None


def test_item_replacement_locks_requested_and_removed_symbols_in_uuid_order_before_delete(monkeypatch):
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    set_id = _active_set(client)
    removed_id = _symbol(Session, "removed-low-symbol", uuid.UUID("a0000000-0000-0000-0000-000000000001"))
    retained_id = _symbol(Session, "retained-high-symbol", uuid.UUID("f0000000-0000-0000-0000-000000000001"))
    _eligibility(monkeypatch, {removed_id: uuid.uuid4(), retained_id: uuid.uuid4()})
    initial = {"items": [
        {"governedSymbolId": str(removed_id), "sortOrder": 1},
        {"governedSymbolId": str(retained_id), "sortOrder": 2},
    ]}
    assert client.put(f"/api/v1/org/me/symbol-sets/{set_id}/items", json=initial).status_code == 200

    locked_ids = []
    original_get = Session.class_.get
    original_delete = Session.class_.delete

    def record_get(session, entity, identifier, **kwargs):
        if entity is GovernedSymbol and kwargs.get("with_for_update"):
            locked_ids.append(identifier)
        return original_get(session, entity, identifier, **kwargs)

    def assert_full_lock_set_before_delete(session, instance):
        if isinstance(instance, SymbolSetItem):
            assert locked_ids == [removed_id, retained_id]
        return original_delete(session, instance)

    monkeypatch.setattr(Session.class_, "get", record_get)
    monkeypatch.setattr(Session.class_, "delete", assert_full_lock_set_before_delete)

    response = client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/items",
        json={"items": [{"governedSymbolId": str(retained_id), "sortOrder": 2}]},
    )

    assert response.status_code == 200
    assert locked_ids == [removed_id, retained_id]


def test_identical_item_replacement_is_a_no_op(monkeypatch):
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    set_id = _active_set(client)
    symbol_id = _symbol(Session, "idempotent-symbol")
    _eligibility(monkeypatch, {symbol_id: uuid.uuid4()})
    payload = {"items": [{"governedSymbolId": str(symbol_id), "sortOrder": 1}]}

    first = client.put(f"/api/v1/org/me/symbol-sets/{set_id}/items", json=payload)
    assert first.status_code == 200
    with Session() as session:
        first_count = session.query(AuditEvent).filter(AuditEvent.action == "symbol_set.items_replaced").count()

    second = client.put(f"/api/v1/org/me/symbol-sets/{set_id}/items", json=payload)
    assert second.status_code == 200
    with Session() as session:
        second_count = session.query(AuditEvent).filter(AuditEvent.action == "symbol_set.items_replaced").count()

    assert second_count == first_count


def test_item_replacement_distinguishes_json_booleans_from_numbers(monkeypatch):
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    set_id = _active_set(client)
    symbol_id = _symbol(Session, "provenance-json-types")
    _eligibility(monkeypatch, {symbol_id: uuid.uuid4()})
    path = f"/api/v1/org/me/symbol-sets/{set_id}/items"
    numeric = {"items": [{
        "governedSymbolId": str(symbol_id),
        "sortOrder": 1,
        "provenance": {"nested": {"flag": 1}},
    }]}
    boolean = {"items": [{
        "governedSymbolId": str(symbol_id),
        "sortOrder": 1,
        "provenance": {"nested": {"flag": True}},
    }]}
    assert client.put(path, json=numeric).status_code == 200
    with Session() as session:
        before_audits = session.query(AuditEvent).filter(
            AuditEvent.action == "symbol_set.items_replaced"
        ).count()

    response = client.put(path, json=boolean)

    assert response.status_code == 200
    assert response.json()["items"][0]["provenance"] == {"nested": {"flag": True}}
    with Session() as session:
        assert session.query(AuditEvent).filter(
            AuditEvent.action == "symbol_set.items_replaced"
        ).count() == before_audits + 1


def test_multi_item_replacement_compares_each_item_to_its_own_provenance(monkeypatch):
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    set_id = _active_set(client)
    first_id = _symbol(Session, "provenance-first")
    second_id = _symbol(Session, "provenance-second")
    _eligibility(monkeypatch, {first_id: uuid.uuid4(), second_id: uuid.uuid4()})
    payload = {"items": [
        {"governedSymbolId": str(first_id), "sortOrder": 1, "provenance": {"source": "one"}},
        {"governedSymbolId": str(second_id), "sortOrder": 2, "provenance": {"source": "two"}},
    ]}
    assert client.put(f"/api/v1/org/me/symbol-sets/{set_id}/items", json=payload).status_code == 200
    with Session() as session:
        before = {item.governed_symbol_id: (item.updated_at, item.provenance_json) for item in session.query(SymbolSetItem).all()}
        audit_count = session.query(AuditEvent).filter(AuditEvent.action == "symbol_set.items_replaced").count()
    assert client.put(f"/api/v1/org/me/symbol-sets/{set_id}/items", json=payload).status_code == 200
    with Session() as session:
        after = {item.governed_symbol_id: (item.updated_at, item.provenance_json) for item in session.query(SymbolSetItem).all()}
        assert session.query(AuditEvent).filter(AuditEvent.action == "symbol_set.items_replaced").count() == audit_count
    assert after == before


def test_distinct_item_replacements_have_stable_noncolliding_audit_ids(monkeypatch):
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    set_id = _active_set(client)
    first_id = _symbol(Session, "audit-first")
    second_id = _symbol(Session, "audit-second")
    _eligibility(monkeypatch, {first_id: uuid.uuid4(), second_id: uuid.uuid4()})
    first = {"items": [{"governedSymbolId": str(first_id), "sortOrder": 1, "provenance": {"source": "one"}}]}
    second = {"items": [{"governedSymbolId": str(second_id), "sortOrder": 1, "provenance": {"source": "two"}}]}
    assert client.put(f"/api/v1/org/me/symbol-sets/{set_id}/items", json=first).status_code == 200
    with Session() as session:
        first_event = session.query(AuditEvent).filter(AuditEvent.action == "symbol_set.items_replaced").one()
    assert client.put(f"/api/v1/org/me/symbol-sets/{set_id}/items", json=second).status_code == 200
    with Session() as session:
        events = session.query(AuditEvent).filter(AuditEvent.action == "symbol_set.items_replaced").order_by(AuditEvent.created_at, AuditEvent.id).all()
    assert len(events) == 2
    assert first_event.id in {event.id for event in events}
    assert events[0].id != events[1].id


def test_recurrent_item_replacement_records_distinct_audit_events(monkeypatch):
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    set_id = _active_set(client)
    first_id = _symbol(Session, "audit-recurrence-first")
    second_id = _symbol(Session, "audit-recurrence-second")
    _eligibility(monkeypatch, {first_id: uuid.uuid4(), second_id: uuid.uuid4()})
    first = {"items": [{
        "governedSymbolId": str(first_id),
        "sortOrder": 1,
        "displayLabel": "First state",
        "provenance": {"source": "first"},
    }]}
    second = {"items": [{
        "governedSymbolId": str(second_id),
        "sortOrder": 2,
        "displayLabel": "Second state",
        "provenance": {"source": "second"},
    }]}

    responses = [
        client.put(f"/api/v1/org/me/symbol-sets/{set_id}/items", json=payload)
        for payload in (first, second, first)
    ]

    assert [response.status_code for response in responses] == [200, 200, 200]
    with Session() as session:
        events = session.query(AuditEvent).filter(
            AuditEvent.entity_id == uuid.UUID(set_id),
            AuditEvent.action == "symbol_set.items_replaced",
        ).all()
        items = session.query(SymbolSetItem).filter_by(symbol_set_id=uuid.UUID(set_id)).all()
    assert len(events) == 3
    assert len({event.id for event in events}) == 3
    assert [(item.governed_symbol_id, item.display_label, item.provenance_json) for item in items] == [
        (first_id, "First state", {"source": "first"}),
    ]


def test_new_item_must_be_currently_public_and_failure_writes_nothing(monkeypatch):
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    set_id = _active_set(client)
    symbol_id = _symbol(Session, "ineligible-symbol")
    _eligibility(monkeypatch, {})

    response = client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/items",
        json={"items": [{"governedSymbolId": str(symbol_id), "sortOrder": 1}]},
    )

    assert response.status_code == 409
    with Session() as session:
        assert session.query(SymbolSetItem).filter_by(symbol_set_id=uuid.UUID(set_id)).count() == 0


def test_items_carry_human_readable_governed_symbol_identity(monkeypatch):
    """Stage 6 WP6.4 needs a human-readable name/category/discipline per
    item so the Symbol Set Builder never has to show a bare UUID
    (CLAUDE.md: keep human-readable symbol IDs prominent)."""
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    set_id = _active_set(client)
    symbol_id = _symbol(Session, "fire-hydrant")
    _eligibility(monkeypatch, {symbol_id: uuid.uuid4()})

    put_response = client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/items",
        json={"items": [{"governedSymbolId": str(symbol_id), "sortOrder": 1}]},
    )
    assert put_response.status_code == 200
    for payload in (put_response.json(), client.get(f"/api/v1/org/me/symbol-sets/{set_id}/items").json()):
        item = payload["items"][0]
        assert item["canonicalName"] == "fire-hydrant"
        assert item["category"] == "test"
        assert item["discipline"] == "test"
        assert item["slug"] == "fire-hydrant"
