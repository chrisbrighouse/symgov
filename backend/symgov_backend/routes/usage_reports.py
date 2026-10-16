"""Usage and audit reading routes (WP-B): an organization admin's own, and the platform admin's.

Behind `SYMGOV_SYMBOL_USAGE_ANALYTICS_ENABLED`; off, they do not exist (404).
The organization routes are the organization admin's alone and show only that
organization. The platform routes show roll-up totals with no user identity,
and the platform audit trail, which includes entity types an organization never
sees.
"""

from __future__ import annotations

from datetime import date
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..audit_reader import AuditReaderError, audit_csv, read_audit
from ..auth import AuthenticatedUser
from ..dependencies import get_db_session, require_organization_admin, require_platform_admin
from ..settings import SymgovAPISettings, get_settings
from ..symbol_usage_reports import organization_usage, platform_usage_by_organization, platform_usage_by_pack

router = APIRouter(tags=["usage-reports"])


def usage_reports_route_guard(settings: SymgovAPISettings = Depends(get_settings)) -> None:
    if not (settings.organizations_enabled and settings.symbol_usage_analytics_enabled):
        raise HTTPException(status_code=404, detail="Not found.")


def _active_organization(current_user: AuthenticatedUser) -> uuid.UUID:
    if not current_user.active_organization_id:
        raise HTTPException(status_code=403, detail="An organization-bound session is required.")
    return uuid.UUID(str(current_user.active_organization_id))


def _csv_response(body: str, name: str) -> Response:
    return Response(
        content=body, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff"},
    )


@router.get("/org/me/usage")
def get_organization_usage(
    days: int = Query(default=30, ge=1, le=60),
    current_user: AuthenticatedUser = Depends(require_organization_admin),
    session: Session = Depends(get_db_session),
) -> dict:
    return organization_usage(session, _active_organization(current_user), days=days)


@router.get("/org/me/audit")
def get_organization_audit(
    entity_type: str | None = Query(default=None, alias="entityType", max_length=80),
    action: str | None = Query(default=None, max_length=120),
    actor_id: uuid.UUID | None = Query(default=None, alias="actorId"),
    since: date | None = Query(default=None),
    until: date | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, alias="pageSize", ge=1, le=200),
    current_user: AuthenticatedUser = Depends(require_organization_admin),
    session: Session = Depends(get_db_session),
) -> dict:
    return read_audit(
        session, organization_id=_active_organization(current_user), entity_type=entity_type, action=action,
        actor_id=actor_id, since=since, until=until, page=page, page_size=page_size,
    )


@router.get("/org/me/audit.csv")
def get_organization_audit_csv(
    since: date = Query(),
    until: date = Query(),
    entity_type: str | None = Query(default=None, alias="entityType", max_length=80),
    action: str | None = Query(default=None, max_length=120),
    current_user: AuthenticatedUser = Depends(require_organization_admin),
    session: Session = Depends(get_db_session),
) -> Response:
    try:
        body = audit_csv(
            session, organization_id=_active_organization(current_user), since=since, until=until, entity_type=entity_type, action=action,
        )
    except AuditReaderError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return _csv_response(body, f"audit-{since}-{until}.csv")


@router.get("/platform/usage/organizations")
def get_platform_usage_by_organization(
    days: int = Query(default=30, ge=1, le=60),
    _admin: AuthenticatedUser = Depends(require_platform_admin),
    session: Session = Depends(get_db_session),
) -> dict:
    return platform_usage_by_organization(session, days=days)


@router.get("/platform/usage/packs")
def get_platform_usage_by_pack(
    days: int = Query(default=30, ge=1, le=60),
    _admin: AuthenticatedUser = Depends(require_platform_admin),
    session: Session = Depends(get_db_session),
) -> dict:
    return platform_usage_by_pack(session, days=days)


@router.get("/platform/audit")
def get_platform_audit(
    organization_id: uuid.UUID | None = Query(default=None, alias="organizationId"),
    entity_type: str | None = Query(default=None, alias="entityType", max_length=80),
    action: str | None = Query(default=None, max_length=120),
    since: date | None = Query(default=None),
    until: date | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, alias="pageSize", ge=1, le=200),
    _admin: AuthenticatedUser = Depends(require_platform_admin),
    session: Session = Depends(get_db_session),
) -> dict:
    return read_audit(
        session, organization_id=organization_id, entity_type=entity_type, action=action, since=since, until=until,
        page=page, page_size=page_size,
    )


@router.get("/platform/audit.csv")
def get_platform_audit_csv(
    since: date = Query(),
    until: date = Query(),
    organization_id: uuid.UUID | None = Query(default=None, alias="organizationId"),
    entity_type: str | None = Query(default=None, alias="entityType", max_length=80),
    action: str | None = Query(default=None, max_length=120),
    _admin: AuthenticatedUser = Depends(require_platform_admin),
    session: Session = Depends(get_db_session),
) -> Response:
    try:
        body = audit_csv(session, organization_id=organization_id, since=since, until=until, entity_type=entity_type, action=action)
    except AuditReaderError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return _csv_response(body, f"platform-audit-{since}-{until}.csv")
