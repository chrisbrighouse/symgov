from __future__ import annotations

import uuid

from fastapi import HTTPException, Request
from sqlalchemy import and_, case, or_
from sqlalchemy.orm import Session

from .models import Project, ProjectSymbolSet, SymbolSet, UserProjectSetSelection, UserSessionProjectContext
from .project_service import audit, normalize_code, now
from .stage4_authorization import Stage4Principal, require_stage4_principal


def project_summary(row: Project) -> dict:
    return {
        "id": str(row.id),
        "code": row.code,
        "name": row.name,
        "shortDescription": row.short_description,
        "status": row.status,
    }


def symbol_set_summary(row: SymbolSet) -> dict:
    return {
        "id": str(row.id),
        "code": row.code,
        "name": row.name,
        "description": row.description,
        "disciplines": row.disciplines_json or [],
        "useCases": row.use_cases_json or [],
        "status": row.status,
    }


def _context_row(session: Session, principal: Stage4Principal, *, lock: bool = False):
    query = session.query(UserSessionProjectContext).filter(
        UserSessionProjectContext.user_session_id == principal.session.id
    )
    if lock:
        query = query.with_for_update()
    return query.one_or_none()


def _active_project(session: Session, principal: Stage4Principal, project_id: uuid.UUID, *, lock: bool = False):
    query = session.query(Project).filter(
        Project.id == project_id,
        Project.organization_id == principal.organization.id,
        Project.status == "active",
    )
    if lock:
        query = query.with_for_update()
    return query.one_or_none()


def _eligible_set(session: Session, project_id: uuid.UUID, set_id: uuid.UUID):
    return session.query(SymbolSet).join(
        ProjectSymbolSet,
        ProjectSymbolSet.symbol_set_id == SymbolSet.id,
    ).filter(
        ProjectSymbolSet.project_id == project_id,
        ProjectSymbolSet.symbol_set_id == set_id,
        ProjectSymbolSet.status == "active",
        SymbolSet.status == "active",
    ).one_or_none()


def _resolved_set(session: Session, principal: Stage4Principal, project: Project, *, cleanup_stale: bool = True):
    candidate = session.query(SymbolSet, UserProjectSetSelection, ProjectSymbolSet.is_default).join(
        ProjectSymbolSet,
        ProjectSymbolSet.symbol_set_id == SymbolSet.id,
    ).outerjoin(
        UserProjectSetSelection,
        and_(
            UserProjectSetSelection.user_id == principal.user.id,
            UserProjectSetSelection.project_id == project.id,
        ),
    ).filter(
        ProjectSymbolSet.project_id == project.id,
        ProjectSymbolSet.status == "active",
        SymbolSet.status == "active",
        or_(
            SymbolSet.id == UserProjectSetSelection.active_symbol_set_id,
            ProjectSymbolSet.is_default.is_(True),
            SymbolSet.id == principal.organization.default_symbol_set_id,
        ),
    ).order_by(
        case(
            (SymbolSet.id == UserProjectSetSelection.active_symbol_set_id, 0),
            (ProjectSymbolSet.is_default.is_(True), 1),
            (SymbolSet.id == principal.organization.default_symbol_set_id, 2),
            else_=3,
        ),
        SymbolSet.id,
    ).first()
    if candidate is not None:
        symbol_set, preference, is_project_default = candidate
        if preference is not None and preference.active_symbol_set_id == symbol_set.id:
            return symbol_set, "user_preference"
        if preference is not None and cleanup_stale:
            session.delete(preference)
            session.flush()
        return symbol_set, "project_default" if is_project_default else "organization_default"

    preference = session.query(UserProjectSetSelection).filter(
        UserProjectSetSelection.user_id == principal.user.id,
        UserProjectSetSelection.project_id == project.id,
    ).one_or_none()
    if preference is not None and cleanup_stale:
        session.delete(preference)
        session.flush()
    return None, "none"


def _response(
    session: Session,
    principal: Stage4Principal,
    project: Project | None,
    *,
    explicit: SymbolSet | None = None,
    cleanup_stale: bool = True,
):
    if project is None:
        return {"selectedProject": None, "activeSet": None, "reason": "none"}
    if explicit is not None:
        row, reason = explicit, "explicit"
    else:
        row, reason = _resolved_set(session, principal, project, cleanup_stale=cleanup_stale)
    return {
        "selectedProject": project_summary(project),
        "activeSet": symbol_set_summary(row) if row is not None else None,
        "reason": reason,
    }


def get_context(session: Session, request: Request, settings):
    principal = require_stage4_principal(session, request, settings)
    result = session.query(UserSessionProjectContext, Project).outerjoin(
        Project,
        and_(
            Project.id == UserSessionProjectContext.project_id,
            Project.organization_id == principal.organization.id,
            Project.status == "active",
        ),
    ).filter(
        UserSessionProjectContext.user_session_id == principal.session.id
    ).one_or_none()
    if result is None:
        return _response(session, principal, None)
    context, project = result
    if project is None:
        session.delete(context)
        session.flush()
        return _response(session, principal, None)
    return _response(session, principal, project)


def select_project(session: Session, request: Request, settings, project_id: uuid.UUID):
    principal = require_stage4_principal(session, request, settings)
    project = _active_project(session, principal, project_id, lock=True)
    if project is None:
        raise HTTPException(404, "Not found.")
    context = _context_row(session, principal, lock=True)
    if context is None:
        stamp = now()
        context = UserSessionProjectContext(
            user_session_id=principal.session.id,
            project_id=project.id,
            selected_at=stamp,
            updated_at=stamp,
        )
        session.add(context)
        changed = True
    else:
        changed = context.project_id != project.id
        if changed:
            context.project_id = project.id
            context.selected_at = now()
            context.updated_at = context.selected_at
    if changed:
        audit(session, principal, "project", project.id, "project.selected", {
            "projectId": str(project.id), "reason": "explicit"
        })
        session.flush()
    return _response(session, principal, project, cleanup_stale=False)


def clear_project(session: Session, request: Request, settings) -> None:
    principal = require_stage4_principal(session, request, settings)
    context = _context_row(session, principal, lock=True)
    if context is None:
        return
    project_id = context.project_id
    session.delete(context)
    audit(session, principal, "project", project_id, "project.selection_cleared", {
        "projectId": str(project_id), "reason": "none"
    })
    session.flush()


def _selected_project_for_update(session: Session, principal: Stage4Principal):
    context_snapshot = _context_row(session, principal)
    if context_snapshot is None:
        raise HTTPException(409, "Select a Project before selecting a Symbol Set.")
    project_id = context_snapshot.project_id
    project = _active_project(session, principal, project_id, lock=True)
    context = _context_row(session, principal, lock=True)
    if context is None or context.project_id != project_id:
        raise HTTPException(409, "Selected Project changed; retry the Symbol Set selection.")
    if project is None:
        session.delete(context)
        session.flush()
        raise HTTPException(409, "Select an active Project before selecting a Symbol Set.")
    return project


def select_active_set(session: Session, request: Request, settings, set_code: str):
    principal = require_stage4_principal(session, request, settings)
    project = _selected_project_for_update(session, principal)
    _, normalized_code = normalize_code(set_code)
    symbol_set = session.query(SymbolSet).filter(
        SymbolSet.owner_organization_id == principal.organization.id,
        SymbolSet.normalized_code == normalized_code,
    ).with_for_update().one_or_none()
    if symbol_set is None or symbol_set.status != "active" or _eligible_set(session, project.id, symbol_set.id) is None:
        raise HTTPException(404, "Not found.")
    preference = session.query(UserProjectSetSelection).filter(
        UserProjectSetSelection.user_id == principal.user.id,
        UserProjectSetSelection.project_id == project.id,
    ).with_for_update().one_or_none()
    changed = preference is None or preference.active_symbol_set_id != symbol_set.id
    if preference is None:
        stamp = now()
        preference = UserProjectSetSelection(
            user_id=principal.user.id,
            project_id=project.id,
            active_symbol_set_id=symbol_set.id,
            selected_at=stamp,
            updated_at=stamp,
        )
        session.add(preference)
    elif changed:
        preference.active_symbol_set_id = symbol_set.id
        preference.selected_at = now()
        preference.updated_at = preference.selected_at
    if changed:
        audit(session, principal, "symbol_set", symbol_set.id, "symbol_set.selected", {
            "projectId": str(project.id), "symbolSetId": str(symbol_set.id), "reason": "explicit"
        })
        session.flush()
    return _response(session, principal, project, explicit=symbol_set)


def clear_active_set(session: Session, request: Request, settings):
    principal = require_stage4_principal(session, request, settings)
    project = _selected_project_for_update(session, principal)
    preference = session.query(UserProjectSetSelection).filter(
        UserProjectSetSelection.user_id == principal.user.id,
        UserProjectSetSelection.project_id == project.id,
    ).with_for_update().one_or_none()
    if preference is not None:
        symbol_set_id = preference.active_symbol_set_id
        session.delete(preference)
        session.flush()
        _, reason = _resolved_set(session, principal, project)
        audit(session, principal, "symbol_set", symbol_set_id, "symbol_set.selection_cleared", {
            "projectId": str(project.id), "symbolSetId": str(symbol_set_id), "reason": reason
        })
        session.flush()
    return _response(session, principal, project)
