from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import AuthenticatedUser, hash_pin, normalize_display_name, normalize_email, normalize_roles, user_roles, utc_now, validate_pin
from ..dependencies import get_db_session, require_any_role
from ..models import User, UserRole, UserSession, UserSubscription
from ..schemas import (
    APIHealthResponse,
    AdminSubscriptionMonthsRequest,
    AdminUserCreateRequest,
    AdminUserListResponse,
    AdminUserMutationResponse,
    AdminUserResetPinRequest,
    AdminUserResponse,
    AdminUserUpdateRequest,
    SubscriptionResponse,
)
from ..services.external_submissions import iso_now
from ..settings import get_settings
from ..subscriptions import adjust_plus_months, cancel_plus, ensure_subscription, reconcile_subscription, today_utc, upgrade_to_plus

router = APIRouter(tags=["admin"])
legacy_router = APIRouter(tags=["admin"])


def _subscription_response(subscription: UserSubscription) -> SubscriptionResponse:
    return SubscriptionResponse(
        tier=subscription.tier,
        startedOn=subscription.started_on.isoformat(),
        expiresOn=subscription.expires_on.isoformat() if subscription.expires_on else None,
        isActive=subscription.tier == "plus",
        isProtected=bool(subscription.is_protected),
    )


def _admin_user_response(
    session: Session,
    user: User,
    *,
    subscription: UserSubscription | None = None,
    roles: list[str] | None = None,
) -> AdminUserResponse:
    current = subscription or ensure_subscription(session, user)
    resolved_roles = roles if roles is not None else list(user_roles(session, user.id))
    if current.tier != "plus":
        resolved_roles = []
    return AdminUserResponse(
        id=str(user.id), email=user.email, displayName=user.display_name, roles=resolved_roles,
        isActive=bool(user.is_active), isDeleted=user.deleted_at is not None,
        mustChangePin=bool(user.must_change_pin), createdAt=user.created_at.isoformat(),
        updatedAt=user.updated_at.isoformat(), subscription=_subscription_response(current),
    )


def _parse_user_id(user_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="User not found.") from exc


def _get_user(session: Session, user_id: str) -> User:
    user = session.query(User).filter(User.id == _parse_user_id(user_id)).with_for_update().one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.deleted_at is not None:
        raise HTTPException(status_code=410, detail="User has been removed.")
    return user


def _parse_payload(model, request: dict):
    return model.model_validate(request.get("payload") or request)


@router.get("/health", response_model=APIHealthResponse)
@legacy_router.get("/health", response_model=APIHealthResponse, include_in_schema=False)
def health() -> APIHealthResponse:
    settings = get_settings()
    return APIHealthResponse(ok=True, service=settings.service_name, time=iso_now())


@router.get("/admin/users", response_model=AdminUserListResponse)
@legacy_router.get("/admin/users", response_model=AdminUserListResponse, include_in_schema=False)
def list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, alias="pageSize", ge=1, le=200),
    q: str = Query(default="", max_length=200),
    tier: str | None = Query(default=None, pattern="^(free|plus)$"),
    role: str | None = Query(default=None),
    include_deleted: bool = Query(default=False, alias="includeDeleted"),
    sort: str = Query(default="name", pattern="^(name|email|tier|start|expiry|status)$"),
    sort_direction: str = Query(default="asc", alias="sortDirection", pattern="^(asc|desc)$"),
    session: Session = Depends(get_db_session),
    _: AuthenticatedUser = Depends(require_any_role({"admin"})),
) -> AdminUserListResponse:
    expired_rows = (
        session.query(User, UserSubscription)
        .join(UserSubscription, UserSubscription.user_id == User.id)
        .filter(
            UserSubscription.tier == "plus",
            UserSubscription.is_protected.is_(False),
            UserSubscription.expires_on.is_not(None),
            UserSubscription.expires_on <= today_utc(),
        )
        .all()
    )
    for expired_user, expired_subscription in expired_rows:
        reconcile_subscription(session, expired_user, subscription=expired_subscription)
    session.flush()
    query = session.query(User, UserSubscription).join(UserSubscription, UserSubscription.user_id == User.id)
    if not include_deleted:
        query = query.filter(User.deleted_at.is_(None))
    if q.strip():
        needle = f"%{q.strip().lower()}%"
        query = query.filter(or_(func.lower(User.display_name).like(needle), func.lower(User.email).like(needle)))
    if tier:
        query = query.filter(UserSubscription.tier == tier)
    if role:
        query = query.join(UserRole, UserRole.user_id == User.id).filter(
            UserRole.role == role,
            UserSubscription.tier == "plus",
        )
    total = query.count()
    order_by = {
        "name": func.lower(User.display_name),
        "email": func.lower(User.email),
        "tier": UserSubscription.tier,
        "start": UserSubscription.started_on,
        "expiry": UserSubscription.expires_on,
        "status": User.is_active.desc(),
    }[sort]
    if sort != "status":
        order_by = order_by.desc() if sort_direction == "desc" else order_by.asc()
    elif sort_direction == "desc":
        order_by = User.is_active.asc()
    rows = query.order_by(order_by, User.id).offset((page - 1) * page_size).limit(page_size).all()
    user_ids = [user.id for user, _subscription in rows]
    role_map: dict[uuid.UUID, list[str]] = {user_id: [] for user_id in user_ids}
    if user_ids:
        for user_id, role_name in session.query(UserRole.user_id, UserRole.role).filter(UserRole.user_id.in_(user_ids)).order_by(UserRole.role).all():
            role_map[user_id].append(role_name)
    items = [
        _admin_user_response(session, user, subscription=ensure_subscription(session, user), roles=role_map[user.id])
        for user, _subscription in rows
    ]
    session.commit()
    return AdminUserListResponse(items=items, page=page, pageSize=page_size, total=total)


@router.post("/admin/users", response_model=AdminUserMutationResponse, status_code=201)
@legacy_router.post("/admin/users", response_model=AdminUserMutationResponse, status_code=201, include_in_schema=False)
async def create_user(
    http_request: Request,
    session: Session = Depends(get_db_session),
    _: AuthenticatedUser = Depends(require_any_role({"admin"})),
) -> AdminUserMutationResponse:
    payload = _parse_payload(AdminUserCreateRequest, await http_request.json())
    now = utc_now()
    try:
        email = normalize_email(payload.email)
        display_name = normalize_display_name(payload.displayName)
        roles = normalize_roles(payload.roles)
        pin = validate_pin(payload.pin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if roles:
        raise HTTPException(status_code=400, detail="New users start on Free and cannot receive privileged roles until upgraded to Plus.")
    if session.query(User).filter(func.lower(User.email) == email).one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Email address is already in use.")
    if session.query(User).filter(func.lower(User.display_name) == display_name.lower()).one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Display name is already in use.")
    user = User(
        id=uuid.uuid4(), email=email, display_name=display_name, pin_hash=hash_pin(pin), pin_set_at=now,
        must_change_pin=True, is_active=bool(payload.isActive), created_at=now, updated_at=now, deleted_at=None,
    )
    session.add(user)
    try:
        session.flush()
        subscription = ensure_subscription(session, user)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="User could not be created due to a uniqueness conflict.") from exc
    return AdminUserMutationResponse(user=_admin_user_response(session, user, subscription=subscription))


@router.patch("/admin/users/{user_id}", response_model=AdminUserMutationResponse)
@legacy_router.patch("/admin/users/{user_id}", response_model=AdminUserMutationResponse, include_in_schema=False)
async def update_user(
    user_id: str,
    http_request: Request,
    session: Session = Depends(get_db_session),
    _: AuthenticatedUser = Depends(require_any_role({"admin"})),
) -> AdminUserMutationResponse:
    payload = _parse_payload(AdminUserUpdateRequest, await http_request.json())
    user = _get_user(session, user_id)
    subscription = ensure_subscription(session, user)
    if payload.displayName is not None:
        try:
            display_name = normalize_display_name(payload.displayName)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        duplicate = session.query(User).filter(func.lower(User.display_name) == display_name.lower(), User.id != user.id).one_or_none()
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="Display name is already in use.")
        user.display_name = display_name
    if payload.isActive is not None:
        if subscription.is_protected and not payload.isActive:
            raise HTTPException(status_code=400, detail="The protected owner cannot be deactivated.")
        user.is_active = bool(payload.isActive)
    if payload.roles is not None:
        try:
            roles = normalize_roles(payload.roles)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if subscription.tier != "plus" and roles:
            raise HTTPException(status_code=400, detail="Only Plus users can receive privileged roles.")
        if subscription.is_protected and "admin" not in roles:
            raise HTTPException(status_code=400, detail="The protected owner must retain the Admin role.")
        session.query(UserRole).filter(UserRole.user_id == user.id).delete(synchronize_session=False)
        for role_name in roles:
            session.add(UserRole(user_id=user.id, role=role_name, created_at=utc_now()))
    user.updated_at = utc_now()
    session.commit()
    return AdminUserMutationResponse(user=_admin_user_response(session, user, subscription=subscription))


async def _months_request(http_request: Request) -> AdminSubscriptionMonthsRequest:
    return _parse_payload(AdminSubscriptionMonthsRequest, await http_request.json())


@router.post("/admin/users/{user_id}/subscription/upgrade", response_model=AdminUserMutationResponse)
@legacy_router.post("/admin/users/{user_id}/subscription/upgrade", response_model=AdminUserMutationResponse, include_in_schema=False)
async def upgrade_subscription(
    user_id: str, http_request: Request, session: Session = Depends(get_db_session),
    current: AuthenticatedUser = Depends(require_any_role({"admin"})),
) -> AdminUserMutationResponse:
    payload = await _months_request(http_request)
    if payload.months < 1:
        raise HTTPException(status_code=400, detail="Plus duration must be at least one month.")
    user = _get_user(session, user_id)
    try:
        subscription = upgrade_to_plus(session, user, months=payload.months, actor_id=uuid.UUID(current.id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return AdminUserMutationResponse(user=_admin_user_response(session, user, subscription=subscription))


@router.post("/admin/users/{user_id}/subscription/adjust", response_model=AdminUserMutationResponse)
@legacy_router.post("/admin/users/{user_id}/subscription/adjust", response_model=AdminUserMutationResponse, include_in_schema=False)
async def adjust_subscription(
    user_id: str, http_request: Request, session: Session = Depends(get_db_session),
    current: AuthenticatedUser = Depends(require_any_role({"admin"})),
) -> AdminUserMutationResponse:
    payload = await _months_request(http_request)
    user = _get_user(session, user_id)
    try:
        subscription = adjust_plus_months(session, user, months=payload.months, actor_id=uuid.UUID(current.id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return AdminUserMutationResponse(user=_admin_user_response(session, user, subscription=subscription))


@router.post("/admin/users/{user_id}/subscription/cancel", response_model=AdminUserMutationResponse)
@legacy_router.post("/admin/users/{user_id}/subscription/cancel", response_model=AdminUserMutationResponse, include_in_schema=False)
def cancel_subscription(
    user_id: str, session: Session = Depends(get_db_session),
    current: AuthenticatedUser = Depends(require_any_role({"admin"})),
) -> AdminUserMutationResponse:
    user = _get_user(session, user_id)
    try:
        subscription = cancel_plus(session, user, actor_id=uuid.UUID(current.id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return AdminUserMutationResponse(user=_admin_user_response(session, user, subscription=subscription))


@router.delete("/admin/users/{user_id}", response_model=AdminUserMutationResponse)
@legacy_router.delete("/admin/users/{user_id}", response_model=AdminUserMutationResponse, include_in_schema=False)
def delete_user(
    user_id: str, session: Session = Depends(get_db_session),
    current: AuthenticatedUser = Depends(require_any_role({"admin"})),
) -> AdminUserMutationResponse:
    user = _get_user(session, user_id)
    subscription = ensure_subscription(session, user)
    if subscription.is_protected:
        raise HTTPException(status_code=400, detail="The protected owner cannot be removed.")
    subscription = cancel_plus(session, user, actor_id=uuid.UUID(current.id), action="user_removed")
    now = utc_now()
    user.is_active = False
    user.deleted_at = now
    user.updated_at = now
    session.query(UserSession).filter(UserSession.auth_user_id == user.id, UserSession.revoked_at.is_(None)).update({UserSession.revoked_at: now}, synchronize_session=False)
    session.commit()
    return AdminUserMutationResponse(user=_admin_user_response(session, user, subscription=subscription))


@router.post("/admin/users/{user_id}/reset-pin", response_model=AdminUserMutationResponse)
@legacy_router.post("/admin/users/{user_id}/reset-pin", response_model=AdminUserMutationResponse, include_in_schema=False)
async def reset_user_pin(
    user_id: str, http_request: Request, session: Session = Depends(get_db_session),
    _: AuthenticatedUser = Depends(require_any_role({"admin"})),
) -> AdminUserMutationResponse:
    payload = _parse_payload(AdminUserResetPinRequest, await http_request.json())
    user = _get_user(session, user_id)
    try:
        pin = validate_pin(payload.pin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    now = utc_now()
    user.pin_hash = hash_pin(pin)
    user.pin_set_at = now
    user.must_change_pin = True
    user.updated_at = now
    session.commit()
    return AdminUserMutationResponse(user=_admin_user_response(session, user))
