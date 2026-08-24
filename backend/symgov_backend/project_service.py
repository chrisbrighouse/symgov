from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import re
import unicodedata
import uuid

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from .models import AuditEvent, Project, ProjectSymbolSet, SymbolSet, UserProjectSetSelection, UserSessionProjectContext
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


def json_values_equal(left: object, right: object) -> bool:
    return json.dumps(left, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) == json.dumps(
        right, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def audit(session: Session, principal: Stage4Principal, entity_type: str, entity_id: uuid.UUID, action: str, details: dict, *, event_id: uuid.UUID | None = None) -> None:
    payload = {"source": "stage4", "organizationId": str(principal.organization.id), **details}
    session.add(AuditEvent(id=event_id or uuid.uuid4(), entity_type=entity_type, entity_id=entity_id, action=action, actor_id=principal.user.id, payload_json=payload, created_at=now()))


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
    row = session.query(Project).filter(Project.id == project_id, Project.organization_id == principal.organization.id).with_for_update().one_or_none()
    if row is None or (row.status != "active" and not principal.is_admin):
        raise HTTPException(404, "Not found.")
    if not principal.is_admin:
        raise HTTPException(403, "Organization Admin privileges are required.")
    if row.status == "closed":
        if data.status == "closed" and data.only_status(): return row
        raise HTTPException(409, "Project lifecycle transition is not permitted.")
    changed = []
    previous_status = row.status
    affected_symbol_set_ids = []
    before_available_symbol_set_count = 0
    after_available_symbol_set_count = 0
    if "name" in data.model_fields_set:
        candidate = normalize_text(data.name, "Name", 200)
        if candidate != row.name:
            row.name = candidate
            changed.append("name")
    if "shortDescription" in data.model_fields_set:
        candidate = unicodedata.normalize("NFKC", data.shortDescription or "")
        if len(candidate) > 50: raise ValueError("shortDescription must be at most 50 characters.")
        candidate = candidate or None
        if candidate != row.short_description:
            row.short_description = candidate
            changed.append("shortDescription")
    if "externalReference" in data.model_fields_set:
        candidate = normalize_optional_text(data.externalReference, 200); candidate_key = candidate.casefold() if candidate else None
        if candidate != row.external_reference or candidate_key != row.normalized_external_reference:
            if candidate_key is not None and session.query(Project.id).filter(Project.organization_id == principal.organization.id, Project.normalized_external_reference == candidate_key, Project.id != row.id).first() is not None:
                raise ValueError("External reference is already in use.")
            row.external_reference = candidate
            row.normalized_external_reference = candidate_key
            changed.append("externalReference")
    if "metadata" in data.model_fields_set:
        candidate = validate_json(data.metadata)
        if not json_values_equal(candidate, row.metadata_json):
            row.metadata_json = candidate
            changed.append("metadata")
    if data.status is not None and data.status != row.status:
        if data.status != "closed": raise HTTPException(409, "Project lifecycle transition is not permitted.")
        row.status = "closed"; row.closed_at = now()
        # The Project anchor is already locked above. Discover and lock only
        # the affected Set anchors in the same statement, rather than using an
        # unlocked dependent-link read to define which Sets to lock. The
        # dependent rows are locked only after the Project and Set anchors.
        locked_sets = session.query(SymbolSet).join(
            ProjectSymbolSet,
            ProjectSymbolSet.symbol_set_id == SymbolSet.id,
        ).filter(
            ProjectSymbolSet.project_id == row.id,
            SymbolSet.owner_organization_id == principal.organization.id,
        ).order_by(SymbolSet.id).with_for_update(of=SymbolSet).all()
        links = session.query(ProjectSymbolSet).filter(ProjectSymbolSet.project_id == row.id).order_by(
            ProjectSymbolSet.symbol_set_id
        ).with_for_update().all()
        active_set_ids = {link.symbol_set_id for link in links if link.status == "active"}
        affected_symbol_set_ids = sorted(
            {symbol_set.id for symbol_set in locked_sets if symbol_set.id in active_set_ids}, key=str
        )
        before_available_symbol_set_count = len(active_set_ids)
        session.query(UserProjectSetSelection).filter(UserProjectSetSelection.project_id == row.id).with_for_update().all()
        session.query(UserSessionProjectContext).filter(UserSessionProjectContext.project_id == row.id).with_for_update().all()
        session.query(ProjectSymbolSet).filter(ProjectSymbolSet.project_id == row.id).update({"status": "inactive", "is_default": False})
        session.query(UserProjectSetSelection).filter(UserProjectSetSelection.project_id == row.id).delete(synchronize_session=False)
        session.query(UserSessionProjectContext).filter(UserSessionProjectContext.project_id == row.id).delete(synchronize_session=False)
        changed.append("status")
    if changed:
        row.updated_at = now()
        details = {"projectId": str(row.id), "changedFields": changed}
        if row.status != previous_status:
            details.update({
                "oldStatus": previous_status,
                "newStatus": row.status,
                "affectedSymbolSetIds": [str(value) for value in affected_symbol_set_ids],
                "beforeAvailableSymbolSetCount": before_available_symbol_set_count,
                "afterAvailableSymbolSetCount": after_available_symbol_set_count,
            })
        audit(session, principal, "project", row.id, "project.closed" if row.status == "closed" else "project.updated", details)
    return row
