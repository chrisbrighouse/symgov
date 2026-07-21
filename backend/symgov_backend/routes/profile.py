from __future__ import annotations

import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..auth import AuthenticatedUser, user_roles
from ..dependencies import get_db_session, require_user
from ..email_outbox import queue_subscription_change_emails
from ..models import SubscriptionEvent, User, UserSubscription
from ..routes.auth import auth_user_response
from ..schemas import (
    ProfilePlanResponse,
    ProfileResponse,
    ProfileSubscriptionMutationResponse,
    ProfileUpgradeOptionResponse,
    SelfServiceDowngradeRequest,
    SelfServiceUpgradeRequest,
)
from ..settings import SymgovAPISettings, get_settings
from ..subscriptions import add_calendar_months, cancel_plus, ensure_subscription, today_utc, upgrade_to_plus

router = APIRouter(prefix="/profile", tags=["profile"])


def _plan() -> ProfilePlanResponse:
    starts_on = today_utc()
    return ProfilePlanResponse(
        upgradeOptions=[
            ProfileUpgradeOptionResponse(
                years=years,
                totalPricePence=5000 * years,
                expiresOn=add_calendar_months(starts_on, years * 12, anchor_day=starts_on.day).isoformat(),
            )
            for years in range(1, 6)
        ]
    )


async def _payload(model, request: Request):
    if not request.headers.get("content-type", "").lower().startswith("application/json"):
        raise HTTPException(status_code=415, detail="Content-Type must be application/json.")
    source = request.headers.get("origin") or request.headers.get("referer")
    if source and urlsplit(source).netloc.lower() != request.headers.get("host", "").lower():
        raise HTTPException(status_code=403, detail="Cross-origin subscription changes are not permitted.")
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object.")
    try:
        return model.model_validate(body.get("payload") or body)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


def _current_user_response(session: Session, user: User, subscription: UserSubscription):
    current = AuthenticatedUser(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        roles=user_roles(session, user.id) if subscription.tier == "plus" else (),
        must_change_pin=bool(user.must_change_pin),
        subscription_tier=subscription.tier,
        subscription_started_on=subscription.started_on,
        subscription_expires_on=subscription.expires_on,
        subscription_is_protected=bool(subscription.is_protected),
    )
    return auth_user_response(current)


def _load_user(session: Session, current: AuthenticatedUser) -> User:
    user = session.get(User, uuid.UUID(current.id))
    if user is None or not user.is_active or user.deleted_at is not None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


def _latest_event(session: Session, user: User, action: str) -> SubscriptionEvent:
    event = (
        session.query(SubscriptionEvent)
        .filter(
            SubscriptionEvent.user_id == user.id,
            SubscriptionEvent.actor_id == user.id,
            SubscriptionEvent.action == action,
            SubscriptionEvent.origin == "self_service",
        )
        .order_by(SubscriptionEvent.created_at.desc(), SubscriptionEvent.id.desc())
        .first()
    )
    if event is None:
        raise RuntimeError("Subscription audit event was not recorded.")
    return event


@router.get("", response_model=ProfileResponse)
def get_profile(
    session: Session = Depends(get_db_session),
    current: AuthenticatedUser = Depends(require_user),
) -> ProfileResponse:
    user = _load_user(session, current)
    subscription = ensure_subscription(session, user)
    session.commit()
    return ProfileResponse(user=_current_user_response(session, user, subscription), plan=_plan())


@router.post("/subscription/upgrade", response_model=ProfileSubscriptionMutationResponse)
async def upgrade_subscription(
    http_request: Request,
    session: Session = Depends(get_db_session),
    current: AuthenticatedUser = Depends(require_user),
    settings: SymgovAPISettings = Depends(get_settings),
) -> ProfileSubscriptionMutationResponse:
    payload = await _payload(SelfServiceUpgradeRequest, http_request)
    if not payload.confirmed:
        raise HTTPException(status_code=400, detail="Upgrade confirmation is required.")
    user = _load_user(session, current)
    try:
        subscription = upgrade_to_plus(
            session, user, months=payload.years * 12, actor_id=user.id, origin="self_service"
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    event = _latest_event(session, user, "upgraded")
    queue_subscription_change_emails(
        session,
        event=event,
        user=user,
        subscription=subscription,
        admin_email=settings.subscription_admin_email,
        years=payload.years,
    )
    session.commit()
    return ProfileSubscriptionMutationResponse(
        user=_current_user_response(session, user, subscription), plan=_plan()
    )


@router.post("/subscription/downgrade", response_model=ProfileSubscriptionMutationResponse)
async def downgrade_subscription(
    http_request: Request,
    session: Session = Depends(get_db_session),
    current: AuthenticatedUser = Depends(require_user),
    settings: SymgovAPISettings = Depends(get_settings),
) -> ProfileSubscriptionMutationResponse:
    payload = await _payload(SelfServiceDowngradeRequest, http_request)
    if not payload.confirmed:
        raise HTTPException(status_code=400, detail="Immediate downgrade confirmation is required.")
    user = _load_user(session, current)
    before = ensure_subscription(session, user)
    previous_expiry = before.expires_on
    try:
        subscription = cancel_plus(
            session,
            user,
            actor_id=user.id,
            origin="self_service",
            require_active=True,
        )
    except ValueError as exc:
        status_code = 403 if "protected" in str(exc).lower() else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    event = _latest_event(session, user, "cancelled")
    queue_subscription_change_emails(
        session,
        event=event,
        user=user,
        subscription=subscription,
        admin_email=settings.subscription_admin_email,
        previous_expires_on=previous_expiry,
    )
    session.commit()
    return ProfileSubscriptionMutationResponse(
        user=_current_user_response(session, user, subscription), plan=_plan()
    )