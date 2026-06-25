from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import hash_pin, normalize_display_name, normalize_email, normalize_roles, user_roles, utc_now, validate_pin
from ..dependencies import get_db_session, require_any_role
from ..models import User, UserRole
from ..schemas import (
    APIHealthResponse,
    AdminUserCreateRequest,
    AdminUserListResponse,
    AdminUserMutationResponse,
    AdminUserResetPinRequest,
    AdminUserResponse,
    AdminUserUpdateRequest,
)
from ..services.external_submissions import iso_now
from ..settings import get_settings


router = APIRouter(tags=["admin"])
legacy_router = APIRouter(tags=["admin"])


def _admin_user_response(session: Session, user: User) -> AdminUserResponse:
    return AdminUserResponse(
        id=str(user.id),
        email=user.email,
        displayName=user.display_name,
        roles=list(user_roles(session, user.id)),
        isActive=bool(user.is_active),
        mustChangePin=bool(user.must_change_pin),
        createdAt=user.created_at.isoformat(),
        updatedAt=user.updated_at.isoformat(),
    )


@router.get("/health", response_model=APIHealthResponse)
@legacy_router.get("/health", response_model=APIHealthResponse, include_in_schema=False)
def health() -> APIHealthResponse:
    settings = get_settings()
    return APIHealthResponse(ok=True, service=settings.service_name, time=iso_now())


@router.get("/admin/users", response_model=AdminUserListResponse)
@legacy_router.get("/admin/users", response_model=AdminUserListResponse, include_in_schema=False)
def list_users(
    session: Session = Depends(get_db_session),
    _=Depends(require_any_role({"admin"})),
) -> AdminUserListResponse:
    users = session.query(User).order_by(func.lower(User.display_name)).all()
    return AdminUserListResponse(items=[_admin_user_response(session, user) for user in users])


@router.post("/admin/users", response_model=AdminUserMutationResponse, status_code=201)
@legacy_router.post("/admin/users", response_model=AdminUserMutationResponse, status_code=201, include_in_schema=False)
async def create_user(
    http_request: Request,
    session: Session = Depends(get_db_session),
    _=Depends(require_any_role({"admin"})),
) -> AdminUserMutationResponse:
    request = await http_request.json()
    payload = AdminUserCreateRequest.model_validate(request.get("payload") or request)
    now = utc_now()
    try:
        email = normalize_email(payload.email)
        display_name = normalize_display_name(payload.displayName)
        roles = normalize_roles(payload.roles)
        pin = validate_pin(payload.pin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if session.query(User).filter(func.lower(User.email) == email).one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Email address is already in use.")

    if session.query(User).filter(func.lower(User.display_name) == display_name.lower()).one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Display name is already in use.")

    user = User(
        id=uuid.uuid4(),
        email=email,
        display_name=display_name,
        pin_hash=hash_pin(pin),
        pin_set_at=now,
        must_change_pin=True,
        is_active=bool(payload.isActive),
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    for role in roles:
        session.add(UserRole(user_id=user.id, role=role, created_at=now))
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="User could not be created due to a uniqueness conflict.") from exc
    return AdminUserMutationResponse(user=_admin_user_response(session, user))


@router.patch("/admin/users/{user_id}", response_model=AdminUserMutationResponse)
@legacy_router.patch("/admin/users/{user_id}", response_model=AdminUserMutationResponse, include_in_schema=False)
async def update_user(
    user_id: str,
    http_request: Request,
    session: Session = Depends(get_db_session),
    _=Depends(require_any_role({"admin"})),
) -> AdminUserMutationResponse:
    request = await http_request.json()
    payload = AdminUserUpdateRequest.model_validate(request.get("payload") or request)
    try:
        parsed_user_id = uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="User not found.") from exc

    user = session.get(User, parsed_user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    if payload.displayName is not None:
        try:
            display_name = normalize_display_name(payload.displayName)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        duplicate = (
            session.query(User)
            .filter(func.lower(User.display_name) == display_name.lower(), User.id != user.id)
            .one_or_none()
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="Display name is already in use.")
        user.display_name = display_name

    if payload.isActive is not None:
        user.is_active = bool(payload.isActive)

    if payload.roles is not None:
        try:
            roles = normalize_roles(payload.roles)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.query(UserRole).filter(UserRole.user_id == user.id).delete(synchronize_session=False)
        for role in roles:
            session.add(UserRole(user_id=user.id, role=role, created_at=utc_now()))

    user.updated_at = utc_now()
    session.commit()
    return AdminUserMutationResponse(user=_admin_user_response(session, user))


@router.post("/admin/users/{user_id}/reset-pin", response_model=AdminUserMutationResponse)
@legacy_router.post("/admin/users/{user_id}/reset-pin", response_model=AdminUserMutationResponse, include_in_schema=False)
async def reset_user_pin(
    user_id: str,
    http_request: Request,
    session: Session = Depends(get_db_session),
    _=Depends(require_any_role({"admin"})),
) -> AdminUserMutationResponse:
    request = await http_request.json()
    payload = AdminUserResetPinRequest.model_validate(request.get("payload") or request)
    try:
        parsed_user_id = uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="User not found.") from exc

    user = session.get(User, parsed_user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

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
