from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import re
import unicodedata
import uuid

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from .models import AuditEvent, Project, ProjectSymbolSet, UserProjectSetSelection, UserSessionProjectContext
from .stage4_authorization import Stage4Principal, require_stage4_principal

CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,31}$")
MAX_PAGE_SIZE = 200


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def normalize_code(value: str) -> tuple[str, str]:
    value = unicodedata.normalize("NFKC", value) if isinstance(value, str) else ""
    if not CODE_RE.fullmatch(value):
        raise ValueError("Code must match ^[A-Z0-9][A-Z0-9-]{0,31}$.")
    return value, value.lower()


def normalize_text(value: str, field: str, maximum: int) -> str:
    value = unicodedata.normalize("NFKC", value).strip() if isinstance(value, str) else ""
    if not value or len(value) > maximum:
        raise ValueError(f"{field} must be 1-{maximum} characters.")
    return value


def normalize_optional_text(value: str | None, maximum: int) -> str | None:
    if value is None:
        return None
    value = unicodedata.normalize("NFKC", value).strip()
    if not value:
        return None
    if len(value) > maximum:
        raise ValueError(f"Value must be at most {maximum} characters.")
    return value


def validate_json(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Metadata must be an object.")

    def walk(item: object, depth: int) -> None:
        if depth > 4:
            raise ValueError("Metadata nesting is too deep.")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or not 1 <= len(key) <= 64:
                    raise ValueError("Metadata keys must be 1-64 characters.")
                walk(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                walk(child, depth + 1)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Metadata numbers must be finite.")
        elif not isinstance(item, (str, int, float, bool)) and item is not None:
            raise ValueError("Metadata contains an unsupported value.")

    walk(value, 1)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > 16384:
        raise ValueError("Metadata is too large.")
    return value


def audit(session: Session, principal: Stage4Principal, entity_type: str, entity_id: uuid.UUID, action: str, details: dict) -> None:
    payload = {"source": "stage4", "organizationId": str(principal.organization.id), **details}
    session.add(AuditEvent(id=uuid.uuid4(), entity_type=entity_type, entity_id=entity_id, action=action, actor_id=principal.user.id, payload_json=payload, created_at=now()))


def project_dict(row: Project) -> dict:
    return {"id": str(row.id), "code": row.code, "name": row.name, "shortDescription": row.short_description, "status": row.status,
            "externalReference": row.external_reference, "metadata": row.metadata_json or {}, "createdAt": row.created_at.isoformat(),
            "updatedAt": row.updated_at.isoformat(), "closedAt": row.closed_at.isoformat() if row.closed_at else None}


def get_principal(session: Session, request: Request, settings, admin=False):
    return require_stage4_principal(session, request, settings, admin=admin)


def list_projects(session: Session, request: Request, settings, *, page: int, page_size: int, include_closed: bool = False):
    principal = get_principal(session, request, settings, admin=False)
    q = session.query(Project).filter(Project.organization_id == principal.organization.id)
    if not include_closed or not principal.is_admin:
        q = q.filter(Project.status == "active")
    total = q.count()
    rows = q.order_by(Project.normalized_code, Project.id).offset((page - 1) * page_size).limit(page_size).all()
    return principal, {"items": [project_dict(r) for r in rows], "page": page, "pageSize": page_size, "total": total}


def create_project(session: Session, request: Request, settings, data):
    principal = get_principal(session, request, settings, admin=True); stamp = now()
    code, normalized = normalize_code(data.code); name = normalize_text(data.name, "Name", 200)
    short = data.shortDescription
    if short is not None:
        short = unicodedata.normalize("NFKC", short)
        if len(short) > 50: raise ValueError("shortDescription must be at most 50 characters.")
    short = short or None
    ext = normalize_optional_text(data.externalReference, 200); normalized_ext = ext.casefold() if ext else None
    if session.query(Project.id).filter(Project.organization_id == principal.organization.id, Project.normalized_code == normalized).first() is not None:
        raise HTTPException(409, "Code is already in use.")
    if normalized_ext is not None and session.query(Project.id).filter(Project.organization_id == principal.organization.id, Project.normalized_external_reference == normalized_ext).first() is not None:
        raise HTTPException(409, "External reference is already in use.")
    row = Project(id=uuid.uuid4(), organization_id=principal.organization.id, code=code, normalized_code=normalized, name=name,
                  short_description=short, status="active", external_reference=ext, normalized_external_reference=normalized_ext,
                  metadata_json=validate_json(data.metadata or {}), created_by_user_id=principal.user.id, created_at=stamp, updated_at=stamp)
    session.add(row); session.flush(); audit(session, principal, "project", row.id, "project.created", {"projectId": str(row.id)}); return row


def get_project(session: Session, request: Request, settings, project_id: uuid.UUID, *, admin=False):
    principal = get_principal(session, request, settings, admin=admin)
    row = session.query(Project).filter(Project.id == project_id, Project.organization_id == principal.organization.id).one_or_none()
    if row is None or (row.status != "active" and not principal.is_admin): raise HTTPException(404, "Not found.")
    return principal, row


def patch_project(session: Session, request: Request, settings, project_id: uuid.UUID, data):
    principal, row = get_project(session, request, settings, project_id)
    if not principal.is_admin:
        raise HTTPException(403, "Organization Admin privileges are required.")
    if row.status == "closed":
        if data.status == "closed" and data.only_status(): return row
        raise HTTPException(409, "Project lifecycle transition is not permitted.")
    changed = []
    if "name" in data.model_fields_set: row.name = normalize_text(data.name, "Name", 200); changed.append("name")
    if "shortDescription" in data.model_fields_set:
        value = unicodedata.normalize("NFKC", data.shortDescription or "")
        if len(value) > 50: raise ValueError("shortDescription must be at most 50 characters.")
        row.short_description = value or None; changed.append("shortDescription")
    if "externalReference" in data.model_fields_set:
        candidate = normalize_optional_text(data.externalReference, 200); candidate_key = candidate.casefold() if candidate else None
        if candidate_key is not None and session.query(Project.id).filter(Project.organization_id == principal.organization.id, Project.normalized_external_reference == candidate_key, Project.id != row.id).first() is not None:
            raise ValueError("External reference is already in use.")
        row.external_reference = candidate; row.normalized_external_reference = candidate_key; changed.append("externalReference")
    if "metadata" in data.model_fields_set: row.metadata_json = validate_json(data.metadata or {}); changed.append("metadata")
    if data.status is not None and data.status != row.status:
        if data.status != "closed": raise HTTPException(409, "Project lifecycle transition is not permitted.")
        row.status = "closed"; row.closed_at = now()
        session.query(ProjectSymbolSet).filter(ProjectSymbolSet.project_id == row.id).update({"status": "inactive", "is_default": False})
        session.query(UserProjectSetSelection).filter(UserProjectSetSelection.project_id == row.id).delete(synchronize_session=False)
        session.query(UserSessionProjectContext).filter(UserSessionProjectContext.project_id == row.id).delete(synchronize_session=False)
        changed.append("status")
    if changed:
        row.updated_at = now(); audit(session, principal, "project", row.id, "project.closed" if row.status == "closed" else "project.updated", {"projectId": str(row.id), "changedFields": changed})
    return row
