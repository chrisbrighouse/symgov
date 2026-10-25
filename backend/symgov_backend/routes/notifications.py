"""In-app notifications (unified submission & review WP6, spec draft 2 FR17).

The reader's notices in their active organization session. Absent, not
forbidden, while the organization symbol workflow v2 flag is off.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import AuthenticatedUser
from ..dependencies import get_db_session, require_organization_session
from ..schemas import UserNotificationListResponse, UserNotificationResponse
from ..user_notifications import (
    UserNotificationNotFound,
    list_notifications,
    mark_all_read,
    mark_read,
)
from .organization_symbols import organization_symbols_route_guard, require_workflow_v2

router = APIRouter(prefix="/notifications", tags=["notifications"], dependencies=[Depends(require_workflow_v2)])

# app.py mounts this router behind the same guard as organization symbols.
notifications_route_guard = organization_symbols_route_guard


def _reader(current_user: AuthenticatedUser) -> tuple[uuid.UUID, uuid.UUID]:
    return uuid.UUID(current_user.id), uuid.UUID(current_user.active_organization_id)


def _response(notification) -> UserNotificationResponse:
    payload = notification.payload_json or {}
    return UserNotificationResponse(
        id=str(notification.id),
        kind=notification.kind,
        governedSymbolId=str(notification.governed_symbol_id) if notification.governed_symbol_id else None,
        submissionId=str(notification.submission_id) if notification.submission_id else None,
        symbolName=payload.get("symbolName"),
        actorName=payload.get("actorName"),
        rationale=payload.get("rationale"),
        createdAt=notification.created_at,
        readAt=notification.read_at,
    )


@router.get("", response_model=UserNotificationListResponse)
def get_notifications(
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> UserNotificationListResponse:
    page = list_notifications(session, *_reader(current_user))
    return UserNotificationListResponse(items=[_response(item) for item in page.items], unreadCount=page.unread_count)


@router.post("/read-all", status_code=204)
def read_all_notifications(
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> None:
    mark_all_read(session, *_reader(current_user))
    session.commit()


@router.post("/{notification_id}/read", status_code=204)
def read_notification(
    notification_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> None:
    try:
        parsed = uuid.UUID(notification_id)
        mark_read(session, *_reader(current_user), parsed)
    except (ValueError, UserNotificationNotFound) as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Notification not found.") from exc
    session.commit()
