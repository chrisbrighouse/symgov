from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import AuthenticatedUser
from ..dependencies import get_db_session, require_platform_admin, require_recent_step_up
from ..models import Organization, User
from ..organization_subscriptions import (
    get_organization_subscription,
    is_subscription_active,
    list_subscription_events,
    renew_organization_subscription,
    seats_in_use,
    set_organization_subscription,
)
from ..settings import SymgovAPISettings, get_settings

router = APIRouter(tags=["platform-admin"])


def _require_platform_admin_enabled(settings: SymgovAPISettings = Depends(get_settings)) -> None:
    if not settings.platform_admin_enabled:
        raise HTTPException(status_code=404, detail="Not found.")


class SubscriptionEventItem(BaseModel):
    action: str
    previousSeatLimit: int | None
    newSeatLimit: int
    previousExpiresOn: str | None
    newExpiresOn: str
    reason: str | None
    actorEmail: str | None
    createdAt: str


class OrganizationSubscriptionResponse(BaseModel):
    organizationId: str
    metered: bool
    seatLimit: int | None = None
    seatsInUse: int
    startedOn: str | None = None
    expiresOn: str | None = None
    status: str  # 'active' | 'expired' | 'unmetered'
    version: int | None = None
    events: list[SubscriptionEventItem] = []


class _Reasoned(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=10, max_length=1000)


class SetOrganizationSubscriptionRequest(_Reasoned):
    seatLimit: int = Field(ge=1, le=100_000)
    months: int | None = Field(default=None, ge=1, le=120)
    expiresOn: date | None = None

    @model_validator(mode="after")
    def _exactly_one_term(self):
        if (self.months is None) == (self.expiresOn is None):
            raise ValueError("Give exactly one of months or expiresOn.")
        return self


class RenewOrganizationSubscriptionRequest(_Reasoned):
    months: int = Field(ge=1, le=120)


def _parse_organization_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Organization not found.") from exc


def _require_organization(session: Session, organization_id: uuid.UUID) -> Organization:
    org = session.get(Organization, organization_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found.")
    return org


def _response(session: Session, organization_id: uuid.UUID) -> OrganizationSubscriptionResponse:
    subscription = get_organization_subscription(session, organization_id)
    used = seats_in_use(session, organization_id)
    if subscription is None:
        return OrganizationSubscriptionResponse(
            organizationId=str(organization_id), metered=False, seatsInUse=used, status="unmetered"
        )
    events = list_subscription_events(session, organization_id)
    actors = {
        user.id: user.email
        for user in session.execute(
            select(User).where(User.id.in_({e.actor_id for e in events if e.actor_id}))
        ).scalars()
    } if any(e.actor_id for e in events) else {}
    return OrganizationSubscriptionResponse(
        organizationId=str(organization_id),
        metered=True,
        seatLimit=subscription.seat_limit,
        seatsInUse=used,
        startedOn=subscription.started_on.isoformat(),
        expiresOn=subscription.expires_on.isoformat(),
        status="active" if is_subscription_active(subscription) else "expired",
        version=subscription.version,
        events=[
            SubscriptionEventItem(
                action=e.action,
                previousSeatLimit=e.previous_seat_limit,
                newSeatLimit=e.new_seat_limit,
                previousExpiresOn=e.previous_expires_on.isoformat() if e.previous_expires_on else None,
                newExpiresOn=e.new_expires_on.isoformat(),
                reason=e.reason,
                actorEmail=actors.get(e.actor_id),
                createdAt=e.created_at.isoformat(),
            )
            for e in events
        ],
    )


@router.get(
    "/platform/organizations/{organization_id}/subscription",
    response_model=OrganizationSubscriptionResponse,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def get_subscription_route(
    organization_id: str,
    session: Session = Depends(get_db_session),
    _current_user: AuthenticatedUser = Depends(require_platform_admin),
) -> OrganizationSubscriptionResponse:
    org_id = _parse_organization_id(organization_id)
    _require_organization(session, org_id)
    return _response(session, org_id)


@router.put(
    "/platform/organizations/{organization_id}/subscription",
    response_model=OrganizationSubscriptionResponse,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def set_subscription_route(
    organization_id: str,
    payload: SetOrganizationSubscriptionRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> OrganizationSubscriptionResponse:
    org_id = _parse_organization_id(organization_id)
    _require_organization(session, org_id)
    try:
        set_organization_subscription(
            session, org_id, seat_limit=payload.seatLimit, months=payload.months,
            expires_on=payload.expiresOn, actor_id=uuid.UUID(current_user.id), reason=payload.reason,
        )
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _response(session, org_id)


@router.post(
    "/platform/organizations/{organization_id}/subscription/renew",
    response_model=OrganizationSubscriptionResponse,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def renew_subscription_route(
    organization_id: str,
    payload: RenewOrganizationSubscriptionRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> OrganizationSubscriptionResponse:
    org_id = _parse_organization_id(organization_id)
    _require_organization(session, org_id)
    try:
        renew_organization_subscription(
            session, org_id, months=payload.months, actor_id=uuid.UUID(current_user.id), reason=payload.reason,
        )
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _response(session, org_id)
