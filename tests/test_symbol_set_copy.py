from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import CheckConstraint, JSON

from test_projects_api import _stage4_client
from symgov_backend.models import GovernedSymbol, ProjectSymbolSet, SymbolSet, SymbolSetItem, User
import symgov_backend.symbol_set_service as symbol_set_service


def _set(client, code, *, active=True):
    created = client.post(
        "/api/v1/org/me/symbol-sets",
        json={"code": code, "name": code},
    )
    assert created.status_code == 201
    set_id = created.json()["id"]
    if active:
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


def _symbol(Session, slug):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        owner = session.query(User).first()
        row = GovernedSymbol(
            id=uuid.uuid4(),
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


def _eligible(monkeypatch, values):
    monkeypatch.setattr(
        symbol_set_service,
        "current_public_symbols",
        lambda session, ids: {symbol_id: values[symbol_id] for symbol_id in ids if symbol_id in values},
    )


def test_copy_preserves_lineage_and_items_without_copying_projects_or_symbols(monkeypatch):
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    source_id = _set(client, "SOURCE")
    symbol_id = _symbol(Session, "copy-symbol")
    _eligible(monkeypatch, {symbol_id: uuid.uuid4()})
    inserted = client.put(
        f"/api/v1/org/me/symbol-sets/{source_id}/items",
        json={
            "items": [
                {
                    "governedSymbolId": str(symbol_id),
                    "sortOrder": 3,
                    "displayLabel": "Copied label",
                    "provenance": {"source": "test"},
                }
            ]
        },
    )
    assert inserted.status_code == 200
    with Session() as session:
        symbol_count = session.query(GovernedSymbol).count()

    copied = client.post(
        f"/api/v1/org/me/symbol-sets/{source_id}/copy",
        json={"code": "TARGET", "name": "Target"},
    )

    assert copied.status_code == 201
    target_id = copied.json()["id"]
    assert copied.json()["status"] == "draft"
    assert copied.json()["copiedFromSymbolSetId"] == source_id
    source_items = client.get(f"/api/v1/org/me/symbol-sets/{source_id}/items").json()["items"]
    target_items = client.get(f"/api/v1/org/me/symbol-sets/{target_id}/items").json()["items"]
    assert target_items[0]["id"] != source_items[0]["id"]
    for key in ("governedSymbolId", "sortOrder", "displayLabel", "provenance", "currentRevisionId", "availabilityStatus", "availabilityReason"):
        assert target_items[0][key] == source_items[0][key]
    with Session() as session:
        assert session.query(GovernedSymbol).count() == symbol_count
        assert session.query(ProjectSymbolSet).filter_by(symbol_set_id=uuid.UUID(target_id)).count() == 0
        assert session.query(SymbolSetItem).filter_by(symbol_set_id=uuid.UUID(target_id)).count() == 1


def test_copy_from_empty_source_is_supported():
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    source_id = _set(client, "EMPTY")

    copied = client.post(
        f"/api/v1/org/me/symbol-sets/{source_id}/copy",
        json={"code": "EMPTYCOPY", "name": "Empty Copy"},
    )

    assert copied.status_code == 201
    target_id = copied.json()["id"]
    assert client.get(f"/api/v1/org/me/symbol-sets/{target_id}/items").json()["total"] == 0
    with Session() as session:
        assert session.get(SymbolSet, uuid.UUID(target_id)).copied_from_symbol_set_id == uuid.UUID(source_id)


def test_copy_fails_atomically_when_a_source_item_is_no_longer_public(monkeypatch):
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    source_id = _set(client, "UNAVAILABLE")
    symbol_id = _symbol(Session, "unavailable-copy-symbol")
    _eligible(monkeypatch, {symbol_id: uuid.uuid4()})
    assert client.put(
        f"/api/v1/org/me/symbol-sets/{source_id}/items",
        json={"items": [{"governedSymbolId": str(symbol_id), "sortOrder": 1}]},
    ).status_code == 200
    _eligible(monkeypatch, {})
    with Session() as session:
        before = session.query(SymbolSet).count()

    copied = client.post(
        f"/api/v1/org/me/symbol-sets/{source_id}/copy",
        json={"code": "SHOULDFAIL", "name": "Should Fail"},
    )

    assert copied.status_code == 409
    with Session() as session:
        assert session.query(SymbolSet).count() == before
        assert session.query(SymbolSetItem).filter_by(symbol_set_id=uuid.UUID(source_id)).count() == 1
