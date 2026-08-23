from __future__ import annotations

from datetime import datetime, timezone
import uuid

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from .models import Organization, ProjectSymbolSet, SymbolSet, UserProjectSetSelection
from .project_service import audit, get_principal, normalize_code, normalize_optional_text, normalize_text

TRANSITIONS = {"draft": {"active", "archived"}, "active": {"superseded", "archived"}, "superseded": {"archived"}, "archived": set()}


def stamp():
    return datetime.now(timezone.utc).replace(microsecond=0)


def labels(values: list[str] | None) -> list[str]:
    result = []; seen = set()
    for value in values or []:
        normalized = normalize_text(value, "Label", 100)
        key = normalized.casefold()
        if key not in seen:
            seen.add(key); result.append(normalized)
    if len(result) > 32: raise ValueError("Labels must contain at most 32 values.")
    return result


def set_dict(row: SymbolSet) -> dict:
    return {"id": str(row.id), "code": row.code, "name": row.name, "description": row.description,
            "disciplines": row.disciplines_json or [], "useCases": row.use_cases_json or [], "status": row.status,
            "copiedFromSymbolSetId": str(row.copied_from_symbol_set_id) if row.copied_from_symbol_set_id else None,
            "createdAt": row.created_at.isoformat(), "updatedAt": row.updated_at.isoformat(),
            "supersededAt": row.superseded_at.isoformat() if row.superseded_at else None,
            "archivedAt": row.archived_at.isoformat() if row.archived_at else None}


def list_sets(session: Session, request: Request, settings, *, page: int, page_size: int, status: str | None = None):
    principal = get_principal(session, request, settings)
    q = session.query(SymbolSet).filter(SymbolSet.owner_organization_id == principal.organization.id)
    if principal.is_admin:
        if status: q = q.filter(SymbolSet.status == status)
    else: q = q.filter(SymbolSet.status == "active")
    total = q.count(); rows = q.order_by(SymbolSet.normalized_code, SymbolSet.id).offset((page - 1) * page_size).limit(page_size).all()
    return principal, {"items": [set_dict(r) for r in rows], "page": page, "pageSize": page_size, "total": total}


def create_set(session: Session, request: Request, settings, data):
    principal = get_principal(session, request, settings, admin=True); stamp_now = stamp(); code, normalized = normalize_code(data.code)
    if session.query(SymbolSet.id).filter(SymbolSet.owner_organization_id == principal.organization.id, SymbolSet.normalized_code == normalized).first() is not None:
        raise HTTPException(409, "Code is already in use.")
    row = SymbolSet(id=uuid.uuid4(), owner_organization_id=principal.organization.id, code=code, normalized_code=normalized,
                    name=normalize_text(data.name, "Name", 200), description=normalize_optional_text(data.description, 2000),
                    disciplines_json=labels(data.disciplines), use_cases_json=labels(data.useCases), status="draft",
                    created_by_user_id=principal.user.id, created_at=stamp_now, updated_at=stamp_now)
    session.add(row); session.flush(); audit(session, principal, "symbol_set", row.id, "symbol_set.created", {"symbolSetId": str(row.id)}); return row


def get_set(session: Session, request: Request, settings, set_id: uuid.UUID, *, admin=False):
    principal = get_principal(session, request, settings, admin=admin)
    row = session.query(SymbolSet).filter(SymbolSet.id == set_id, SymbolSet.owner_organization_id == principal.organization.id).one_or_none()
    if row is None or (row.status != "active" and not principal.is_admin): raise HTTPException(404, "Not found.")
    return principal, row


def patch_set(session: Session, request: Request, settings, set_id: uuid.UUID, data):
    principal, row = get_set(session, request, settings, set_id)
    if not principal.is_admin:
        raise HTTPException(403, "Organization Admin privileges are required.")
    changed = []
    if row.status == "archived" and data.status != "archived": raise HTTPException(409, "Symbol Set lifecycle transition is not permitted.")
    if "name" in data.model_fields_set: row.name = normalize_text(data.name, "Name", 200); changed.append("name")
    if "description" in data.model_fields_set: row.description = normalize_optional_text(data.description, 2000); changed.append("description")
    if "disciplines" in data.model_fields_set: row.disciplines_json = labels(data.disciplines); changed.append("disciplines")
    if "useCases" in data.model_fields_set: row.use_cases_json = labels(data.useCases); changed.append("useCases")
    if data.status is not None and data.status != row.status:
        if data.status not in TRANSITIONS[row.status]: raise HTTPException(409, "Symbol Set lifecycle transition is not permitted.")
        row.status = data.status; changed.append("status")
        if data.status == "superseded": row.superseded_at = stamp_now = stamp()
        if data.status == "archived": row.archived_at = stamp_now = stamp()
        if data.status in {"superseded", "archived"}:
            session.query(ProjectSymbolSet).filter(ProjectSymbolSet.symbol_set_id == row.id).update({"status": "inactive", "is_default": False})
            session.query(UserProjectSetSelection).filter(UserProjectSetSelection.active_symbol_set_id == row.id).delete(synchronize_session=False)
            session.query(Organization).filter(Organization.default_symbol_set_id == row.id).update({"default_symbol_set_id": None})
    if changed:
        row.updated_at = stamp(); action = {"active":"symbol_set.activated", "superseded":"symbol_set.superseded", "archived":"symbol_set.archived"}.get(row.status, "symbol_set.updated")
        audit(session, principal, "symbol_set", row.id, action, {"symbolSetId": str(row.id), "changedFields": changed})
    return row
