from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid

from fastapi import HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .models import AuditEvent, GovernedSymbol, Organization, Project, ProjectSymbolSet, SymbolSet, SymbolSetItem, UserProjectSetSelection
from .project_service import audit, get_principal, json_values_equal, normalize_code, normalize_optional_text, normalize_text, project_dict, validate_json
from .public_symbol_eligibility import current_public_symbols

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


def list_sets(session: Session, request: Request, settings, *, page: int, page_size: int, status: str | None = None, project_id: uuid.UUID | None = None):
    principal = get_principal(session, request, settings)
    q = session.query(SymbolSet).filter(SymbolSet.owner_organization_id == principal.organization.id)
    if principal.is_admin:
        if status: q = q.filter(SymbolSet.status == status)
    else: q = q.filter(SymbolSet.status == "active")
    if project_id is not None:
        project = session.query(Project).filter(Project.id == project_id, Project.organization_id == principal.organization.id).one_or_none()
        if project is None or (project.status != "active" and not principal.is_admin):
            raise HTTPException(404, "Not found.")
        q = q.join(ProjectSymbolSet, ProjectSymbolSet.symbol_set_id == SymbolSet.id).filter(
            ProjectSymbolSet.project_id == project_id, ProjectSymbolSet.status == "active"
        )
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
    organization = None
    if data.status in {"superseded", "archived"}:
        organization = _lock_organization_anchor(session, principal.organization.id)
    _lock_project_set_anchors(session, set_id, principal.organization.id)
    row = session.query(SymbolSet).filter(SymbolSet.id == set_id, SymbolSet.owner_organization_id == principal.organization.id).with_for_update().one_or_none()
    if row is None or (row.status != "active" and not principal.is_admin):
        raise HTTPException(404, "Not found.")
    if not principal.is_admin:
        raise HTTPException(403, "Organization Admin privileges are required.")
    if row.status in {"superseded", "archived"} and any(
        field in data.model_fields_set for field in ("name", "description", "disciplines", "useCases")
    ):
        raise HTTPException(409, "Symbol Set lifecycle transition is not permitted.")
    changed = []
    previous_status = row.status
    affected_project_ids = []
    before_available_project_count = 0
    after_available_project_count = 0
    if row.status == "archived" and data.status != "archived": raise HTTPException(409, "Symbol Set lifecycle transition is not permitted.")
    if "name" in data.model_fields_set:
        candidate = normalize_text(data.name, "Name", 200)
        if candidate != row.name:
            row.name = candidate
            changed.append("name")
    if "description" in data.model_fields_set:
        candidate = normalize_optional_text(data.description, 2000)
        if candidate != row.description:
            row.description = candidate
            changed.append("description")
    if "disciplines" in data.model_fields_set:
        candidate = labels(data.disciplines)
        if candidate != row.disciplines_json:
            row.disciplines_json = candidate
            changed.append("disciplines")
    if "useCases" in data.model_fields_set:
        candidate = labels(data.useCases)
        if candidate != row.use_cases_json:
            row.use_cases_json = candidate
            changed.append("useCases")
    if data.status is not None and data.status != row.status:
        if data.status not in TRANSITIONS[row.status]: raise HTTPException(409, "Symbol Set lifecycle transition is not permitted.")
        row.status = data.status; changed.append("status")
        if data.status == "superseded": row.superseded_at = stamp_now = stamp()
        if data.status == "archived": row.archived_at = stamp_now = stamp()
        if data.status in {"superseded", "archived"}:
            links = session.query(ProjectSymbolSet).filter(ProjectSymbolSet.symbol_set_id == row.id).order_by(ProjectSymbolSet.project_id).with_for_update().all()
            changed_links = [link for link in links if link.status != "inactive" or link.is_default]
            affected_project_ids = sorted({link.project_id for link in changed_links}, key=str)
            before_available_project_count = sum(link.status == "active" for link in links)
            active_links_before = session.query(ProjectSymbolSet).filter(
                ProjectSymbolSet.project_id.in_(affected_project_ids),
                ProjectSymbolSet.status == "active",
            ).order_by(ProjectSymbolSet.project_id, ProjectSymbolSet.symbol_set_id).with_for_update().all() if affected_project_ids else []
            project_defaults_before = {
                link.project_id: link.symbol_set_id for link in active_links_before if link.is_default
            }
            project_counts_before = {
                project_id: sum(link.project_id == project_id for link in active_links_before)
                for project_id in affected_project_ids
            }
            session.query(UserProjectSetSelection).filter(UserProjectSetSelection.active_symbol_set_id == row.id).with_for_update().all()
            assert organization is not None
            organization_default_before = organization.default_symbol_set_id
            session.query(ProjectSymbolSet).filter(ProjectSymbolSet.symbol_set_id == row.id).update({"status": "inactive", "is_default": False})
            session.query(UserProjectSetSelection).filter(UserProjectSetSelection.active_symbol_set_id == row.id).delete(synchronize_session=False)
            if organization.default_symbol_set_id == row.id:
                organization.default_symbol_set_id = None
                organization.updated_at = stamp()
            session.flush()
            active_links_after = session.query(ProjectSymbolSet).filter(
                ProjectSymbolSet.project_id.in_(affected_project_ids),
                ProjectSymbolSet.status == "active",
            ).order_by(ProjectSymbolSet.project_id, ProjectSymbolSet.symbol_set_id).all() if affected_project_ids else []
            project_defaults_after = {
                link.project_id: link.symbol_set_id for link in active_links_after if link.is_default
            }
            project_counts_after = {
                project_id: sum(link.project_id == project_id for link in active_links_after)
                for project_id in affected_project_ids
            }
            after_available_project_count = sum(
                link.symbol_set_id == row.id for link in active_links_after
            )
            for project_id in affected_project_ids:
                if project_defaults_before.get(project_id) != project_defaults_after.get(project_id):
                    audit(session, principal, "project", project_id, "symbol_set.project_default_changed", {
                        "projectId": str(project_id),
                        "symbolSetId": str(row.id),
                        "oldDefaultSymbolSetId": str(project_defaults_before[project_id]) if project_id in project_defaults_before else None,
                        "newDefaultSymbolSetId": str(project_defaults_after[project_id]) if project_id in project_defaults_after else None,
                        "affectedSymbolSetIds": [str(row.id)],
                        "beforeAvailableSymbolSetCount": project_counts_before[project_id],
                        "afterAvailableSymbolSetCount": project_counts_after[project_id],
                    })
            if organization_default_before == row.id:
                audit(session, principal, "organization", organization.id, "organization.symbol_set_default_changed", {
                    "oldDefaultSymbolSetId": str(row.id),
                    "newDefaultSymbolSetId": None,
                    "affectedProjectIds": [str(value) for value in affected_project_ids],
                    "beforeAvailableProjectCount": before_available_project_count,
                    "afterAvailableProjectCount": after_available_project_count,
                })
    if changed:
        row.updated_at = stamp()
        action = "symbol_set.updated"
        if row.status != previous_status:
            action = {
                "active": "symbol_set.activated",
                "superseded": "symbol_set.superseded",
                "archived": "symbol_set.archived",
            }[row.status]
        details = {"symbolSetId": str(row.id), "changedFields": changed}
        if row.status != previous_status:
            details.update({
                "oldStatus": previous_status,
                "newStatus": row.status,
                "affectedProjectIds": [str(value) for value in affected_project_ids],
                "beforeAvailableProjectCount": before_available_project_count,
                "afterAvailableProjectCount": after_available_project_count,
            })
        audit(session, principal, "symbol_set", row.id, action, details)
    return row


def _lock_project_set_anchors(session: Session, set_id, organization_id, requested_ids=()):
    """Lock affected Project anchors, then the Set, before dependent rows."""
    project_query = session.query(Project)
    if not all(hasattr(project_query, name) for name in ("filter", "order_by", "with_for_update")):
        return [], session.query(SymbolSet).filter(SymbolSet.id == set_id).with_for_update().one_or_none()
    requested_ids = tuple(requested_ids)
    projects = project_query.outerjoin(
        ProjectSymbolSet,
        ProjectSymbolSet.project_id == Project.id,
    ).filter(
        Project.organization_id == organization_id,
        or_(Project.id.in_(requested_ids), ProjectSymbolSet.symbol_set_id == set_id),
    ).order_by(Project.id).with_for_update(of=Project).all()
    symbol_set = session.query(SymbolSet).filter(
        SymbolSet.id == set_id, SymbolSet.owner_organization_id == organization_id,
    ).with_for_update().one_or_none()
    return projects, symbol_set


def _lock_organization_default_anchors(session: Session, organization_id):
    # Authorization holds a shared Organization lock. Upgrade it before taking
    # Project/Set locks so a concurrent availability writer cannot wait on our
    # Project lock while we wait on its shared Organization lock.
    organization = _lock_organization_anchor(session, organization_id)
    default_id = organization.default_symbol_set_id
    if default_id is None:
        return None, organization
    _, symbol_set = _lock_project_set_anchors(session, default_id, organization_id)
    if organization.default_symbol_set_id != default_id:
        raise HTTPException(409, "Organization default changed during cleanup; retry.")
    return symbol_set, organization


def _lock_organization_anchor(session: Session, organization_id):
    """Upgrade authorization's shared Organization lock before lower anchors."""
    return session.query(Organization).filter(
        Organization.id == organization_id,
    ).with_for_update().one()


def _admin_set(session, request, settings, set_id):
    principal, row = get_set(session, request, settings, set_id)
    if not principal.is_admin:
        raise HTTPException(403, "Organization Admin privileges are required.")
    return principal, row


def _item_dict(row, current_revision_id=None, available=True, governed=None):
    return {"id": row.id, "governedSymbolId": row.governed_symbol_id, "sortOrder": row.sort_order,
            "groupName": row.group_name, "displayLabel": row.display_label, "notes": row.notes,
            "preferredFormat": row.preferred_format, "provenance": row.provenance_json or {},
            "currentRevisionId": current_revision_id, "availabilityStatus": "active" if available else "unavailable",
            "availabilityReason": None if available else (row.availability_reason or "Public Catalog eligibility is no longer current."),
            "canonicalName": governed.canonical_name if governed is not None else None,
            "category": governed.category if governed is not None else None,
            "discipline": governed.discipline if governed is not None else None,
            "slug": governed.slug if governed is not None else None,
            "createdAt": row.created_at, "updatedAt": row.updated_at}


def list_items(session, request, settings, set_id, *, page, page_size):
    principal, row = get_set(session, request, settings, set_id)
    q = session.query(SymbolSetItem, GovernedSymbol).join(
        GovernedSymbol, GovernedSymbol.id == SymbolSetItem.governed_symbol_id,
    ).filter(SymbolSetItem.symbol_set_id == row.id)
    total = q.count()
    rows = q.order_by(SymbolSetItem.sort_order, SymbolSetItem.governed_symbol_id).offset((page - 1) * page_size).limit(page_size).all()
    current = current_public_symbols(session, [item.governed_symbol_id for item, _ in rows])
    return principal, {"items": [
        _item_dict(item, current.get(item.governed_symbol_id), item.governed_symbol_id in current, governed)
        for item, governed in rows
    ],
                       "page": page, "pageSize": page_size, "total": total}


def replace_items(session, request, settings, set_id, data):
    principal, row = _admin_set(session, request, settings, set_id)
    row = session.query(SymbolSet).filter(SymbolSet.id == row.id, SymbolSet.owner_organization_id == principal.organization.id).with_for_update().one_or_none()
    if row is None:
        raise HTTPException(404, "Not found.")
    if row.status in {"superseded", "archived"}:
        raise HTTPException(409, "Symbol Set items cannot be changed in this lifecycle state.")
    values = data.items
    ids = [item.governedSymbolId for item in values]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate governedSymbolId values are not permitted.")
    if any(item.sortOrder < 0 for item in values):
        raise ValueError("sortOrder must be non-negative.")
    if len(ids) > 1000:
        raise ValueError("At most 1000 items may be supplied.")
    existing = {item.governed_symbol_id: item for item in session.query(SymbolSetItem).filter(
        SymbolSetItem.symbol_set_id == row.id,
    ).all()}
    for symbol_id in sorted(set(ids) | set(existing), key=str):
        if session.get(GovernedSymbol, symbol_id, with_for_update=True) is None:
            raise HTTPException(404, "Not found.")
    current = current_public_symbols(session, ids)
    prepared = []
    for item in values:
        provenance = validate_json(item.provenance)
        for field, maximum in (("groupName", 200), ("displayLabel", 200), ("preferredFormat", 200), ("notes", 2000)):
            value = getattr(item, field)
            if value is not None and len(value) > maximum:
                raise ValueError(f"{field} must be at most {maximum} characters.")
        prepared.append((item, provenance))
    replacement_identity = [
        {
            "governedSymbolId": str(item.governedSymbolId), "sortOrder": item.sortOrder,
            "groupName": item.groupName, "displayLabel": item.displayLabel, "notes": item.notes,
            "preferredFormat": item.preferredFormat, "provenance": provenance,
        }
        for item, provenance in sorted(prepared, key=lambda entry: str(entry[0].governedSymbolId))
    ]
    prepared_by_id = {item.governedSymbolId: (item, provenance) for item, provenance in prepared}
    if set(existing) == set(ids) and all(
        existing[symbol_id].sort_order == item.sortOrder
        and existing[symbol_id].group_name == item.groupName
        and existing[symbol_id].display_label == item.displayLabel
        and existing[symbol_id].notes == item.notes
        and existing[symbol_id].preferred_format == item.preferredFormat
        and json_values_equal(existing[symbol_id].provenance_json or {}, provenance)
        for symbol_id, (item, provenance) in prepared_by_id.items()
    ):
        return list_items(session, request, settings, row.id, page=1, page_size=50)[1]
    now = stamp()
    for item, provenance in prepared:
        old = existing.get(item.governedSymbolId)
        if old is None and item.governedSymbolId not in current:
            raise HTTPException(409, "Every new item must be currently eligible in the public Catalog.")
        if old is None:
            old = SymbolSetItem(id=uuid.uuid4(), symbol_set_id=row.id, governed_symbol_id=item.governedSymbolId, created_at=now)
            session.add(old)
        old.sort_order = item.sortOrder; old.group_name = item.groupName; old.display_label = item.displayLabel
        old.notes = item.notes; old.preferred_format = item.preferredFormat; old.provenance_json = provenance
        old.availability_status = "active" if item.governedSymbolId in current else "unavailable"
        old.availability_reason = None if item.governedSymbolId in current else "Public Catalog eligibility is no longer current."
        old.updated_at = now
    supplied = set(ids)
    for symbol_id, old in existing.items():
        if symbol_id not in supplied:
            session.delete(old)
    session.flush()
    replacement_sequence = session.query(AuditEvent).filter(
        AuditEvent.entity_type == "symbol_set",
        AuditEvent.entity_id == row.id,
        AuditEvent.action == "symbol_set.items_replaced",
    ).count() + 1
    audit(session, principal, "symbol_set", row.id, "symbol_set.items_replaced", {
        "symbolSetId": str(row.id),
        "affectedSymbolIds": sorted({str(value) for value in set(existing) | set(ids)}),
        "beforeItemCount": len(existing),
        "afterItemCount": len(ids),
    }, event_id=uuid.uuid5(
        uuid.NAMESPACE_URL,
        "symbol-set-items:" + str(row.id) + ":" + str(replacement_sequence) + ":"
        + json.dumps(replacement_identity, sort_keys=True, separators=(",", ":")),
    ))
    return list_items(session, request, settings, row.id, page=1, page_size=50)[1]


def replace_projects(session, request, settings, set_id, data):
    principal, row = _admin_set(session, request, settings, set_id)
    requested_ids = [entry.projectId for entry in data.projects]
    # Discover and lock the complete affected Project anchor set in one query.
    # The join is deliberately part of the locking query: an unlocked link read
    # must not define the anchors that are subsequently locked.
    locked_projects, locked_set = _lock_project_set_anchors(session, set_id, principal.organization.id, requested_ids)
    projects = {project.id: project for project in locked_projects}
    row = locked_set
    if row is None:
        raise HTTPException(404, "Not found.")
    if row.status != "active":
        raise HTTPException(409, "Only active Symbol Sets may be made available to Projects.")
    entries = data.projects
    project_ids = [entry.projectId for entry in entries]
    if len(project_ids) != len(set(project_ids)):
        raise ValueError("Duplicate projectId values are not permitted.")
    existing = {link.project_id: link for link in session.query(ProjectSymbolSet).filter(
        ProjectSymbolSet.symbol_set_id == row.id,
    ).order_by(ProjectSymbolSet.project_id).with_for_update().all()}
    requested_projects = [projects[project_id] for project_id in project_ids if project_id in projects]
    if len(requested_projects) != len(project_ids):
        raise HTTPException(404, "Not found.")
    if any(project.status != "active" for project in requested_projects):
        raise HTTPException(409, "Project is not active.")
    desired = {entry.projectId: bool(entry.isDefault) for entry in entries}
    active_existing = {project_id: link for project_id, link in existing.items() if link.status == "active"}
    affected_project_ids = sorted(set(active_existing) | set(desired), key=str)
    active_links_before = session.query(ProjectSymbolSet).filter(
        ProjectSymbolSet.project_id.in_(affected_project_ids),
        ProjectSymbolSet.status == "active",
    ).order_by(ProjectSymbolSet.project_id, ProjectSymbolSet.symbol_set_id).with_for_update().all() if affected_project_ids else []
    defaults_before = {
        link.project_id: link.symbol_set_id for link in active_links_before if link.is_default
    }
    counts_before = {
        project_id: sum(link.project_id == project_id for link in active_links_before)
        for project_id in affected_project_ids
    }
    if set(active_existing) == set(desired) and all(
        link.is_default == desired[project_id]
        for project_id, link in active_existing.items()
    ):
        competing_defaults = any(
            desired[project_id] and defaults_before.get(project_id) != row.id
            for project_id in desired
        )
        if not competing_defaults:
            return list_projects_for_set(session, row.id, principal, page=1, page_size=50)
    now = stamp()
    for entry in entries:
        if entry.isDefault:
            session.query(ProjectSymbolSet).filter(
                ProjectSymbolSet.project_id == entry.projectId,
                ProjectSymbolSet.symbol_set_id != row.id,
                ProjectSymbolSet.status == "active",
            ).update({"is_default": False}, synchronize_session="fetch")
    for entry in entries:
        link = existing.get(entry.projectId)
        if link is None:
            link = ProjectSymbolSet(id=uuid.uuid4(), project_id=entry.projectId, symbol_set_id=row.id, created_by_user_id=principal.user.id, created_at=now, updated_at=now)
            session.add(link)
        link.status = "active"; link.is_default = bool(entry.isDefault); link.updated_at = now
    for project_id, link in existing.items():
        if link.status == "active" and project_id not in desired:
            session.query(UserProjectSetSelection).filter(UserProjectSetSelection.project_id == project_id, UserProjectSetSelection.active_symbol_set_id == row.id).delete(synchronize_session=False)
            session.delete(link)
    session.flush()
    active_links_after = session.query(ProjectSymbolSet).filter(
        ProjectSymbolSet.project_id.in_(affected_project_ids),
        ProjectSymbolSet.status == "active",
    ).order_by(ProjectSymbolSet.project_id, ProjectSymbolSet.symbol_set_id).all() if affected_project_ids else []
    defaults_after = {
        link.project_id: link.symbol_set_id for link in active_links_after if link.is_default
    }
    counts_after = {
        project_id: sum(link.project_id == project_id for link in active_links_after)
        for project_id in affected_project_ids
    }
    for project_id in affected_project_ids:
        if defaults_before.get(project_id) != defaults_after.get(project_id):
            audit(session, principal, "project", project_id, "symbol_set.project_default_changed", {
                "projectId": str(project_id),
                "symbolSetId": str(row.id),
                "oldDefaultSymbolSetId": str(defaults_before[project_id]) if project_id in defaults_before else None,
                "newDefaultSymbolSetId": str(defaults_after[project_id]) if project_id in defaults_after else None,
                "beforeAvailableSymbolSetCount": counts_before[project_id],
                "afterAvailableSymbolSetCount": counts_after[project_id],
            })
    affected_ids = [str(value) for value in affected_project_ids]
    audit(session, principal, "symbol_set", row.id, "symbol_set.project_availability_replaced", {
        "symbolSetId": str(row.id),
        "projectIds": sorted(str(value) for value in project_ids),
        "affectedProjectIds": affected_ids,
        "beforeProjectCount": len(active_existing),
        "afterProjectCount": len(desired),
    })
    return list_projects_for_set(session, row.id, principal, page=1, page_size=50)


def list_projects_for_set(session, set_id, principal, *, page, page_size):
    q = session.query(ProjectSymbolSet, Project).join(Project, Project.id == ProjectSymbolSet.project_id).filter(ProjectSymbolSet.symbol_set_id == set_id, ProjectSymbolSet.status == "active")
    total = q.count(); rows = q.order_by(Project.normalized_code, Project.id).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": [{"project": project_dict(project), "isDefault": link.is_default} for link, project in rows], "page": page, "pageSize": page_size, "total": total}


def set_organization_default(session, request, settings, set_id):
    principal, row = _admin_set(session, request, settings, set_id)
    if row.status != "active":
        raise HTTPException(409, "Only active Symbol Sets may be the organization default.")
    organization = _lock_organization_anchor(session, principal.organization.id)
    _, row = _lock_project_set_anchors(session, row.id, principal.organization.id)
    if row is None:
        raise HTTPException(404, "Not found.")
    if row.status != "active":
        raise HTTPException(409, "Only active Symbol Sets may be the organization default.")
    if session.query(ProjectSymbolSet).join(Project, Project.id == ProjectSymbolSet.project_id).filter(
        ProjectSymbolSet.symbol_set_id == row.id,
        ProjectSymbolSet.status == "active",
        Project.status == "active",
    ).first() is None:
        raise HTTPException(409, "Organization default Symbol Set must be available to an active Project.")
    if organization.default_symbol_set_id == row.id:
        return {"defaultSymbolSetId": str(row.id)}
    previous_default = organization.default_symbol_set_id
    organization.default_symbol_set_id = row.id
    organization.updated_at = stamp()
    audit(session, principal, "organization", organization.id, "organization.symbol_set_default_changed", {
        "oldDefaultSymbolSetId": str(previous_default) if previous_default else None,
        "newDefaultSymbolSetId": str(row.id),
    })
    return {"defaultSymbolSetId": str(row.id)}


def clear_organization_default(session: Session, request, settings):
    principal = get_principal(session, request, settings, admin=True)
    _, organization = _lock_organization_default_anchors(session, principal.organization.id)
    if organization.default_symbol_set_id is None:
        return
    previous_default = organization.default_symbol_set_id
    organization.default_symbol_set_id = None
    organization.updated_at = stamp()
    audit(session, principal, "organization", organization.id, "organization.symbol_set_default_changed", {
        "oldDefaultSymbolSetId": str(previous_default),
        "newDefaultSymbolSetId": None,
    })


def copy_set(session, request, settings, set_id, data):
    principal, source = _admin_set(session, request, settings, set_id)
    source = session.get(SymbolSet, source.id, with_for_update=True)
    code, normalized = normalize_code(data.code)
    if session.query(SymbolSet.id).filter(SymbolSet.owner_organization_id == principal.organization.id, SymbolSet.normalized_code == normalized).first():
        raise HTTPException(409, "Code is already in use.")
    source_items = session.query(SymbolSetItem).filter(SymbolSetItem.symbol_set_id == source.id).order_by(SymbolSetItem.sort_order, SymbolSetItem.governed_symbol_id).all()
    ids = sorted({item.governed_symbol_id for item in source_items}, key=str)
    for symbol_id in ids:
        if session.get(GovernedSymbol, symbol_id, with_for_update=True) is None:
            raise HTTPException(409, "Source contains an unavailable item.")
    current = current_public_symbols(session, ids)
    if len(current) != len(ids):
        raise HTTPException(409, "Source contains an unavailable item.")
    now = stamp()
    target = SymbolSet(id=uuid.uuid4(), owner_organization_id=principal.organization.id, code=code, normalized_code=normalized,
                       name=normalize_text(data.name, "Name", 200), description=source.description,
                       disciplines_json=list(source.disciplines_json or []), use_cases_json=list(source.use_cases_json or []), status="draft",
                       copied_from_symbol_set_id=source.id, created_by_user_id=principal.user.id, created_at=now, updated_at=now)
    session.add(target); session.flush()
    for item in source_items:
        session.add(SymbolSetItem(id=uuid.uuid4(), symbol_set_id=target.id, governed_symbol_id=item.governed_symbol_id, sort_order=item.sort_order,
                                  group_name=item.group_name, display_label=item.display_label, notes=item.notes, preferred_format=item.preferred_format,
                                  provenance_json=dict(item.provenance_json or {}), availability_status="active", availability_reason=None, created_at=now, updated_at=now))
    audit(session, principal, "symbol_set", target.id, "symbol_set.copied", {"symbolSetId": str(target.id), "copiedFromSymbolSetId": str(source.id)})
    return target
