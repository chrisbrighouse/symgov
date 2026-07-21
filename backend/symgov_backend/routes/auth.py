from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..auth import (
    AuthenticatedUser,
    authenticate_user,
    create_user_session,
    current_user_from_token,
    hash_pin,
    revoke_session,
    utc_now,
    verify_pin,
)
from ..dependencies import get_db_session
from ..models import User
from ..schemas import AuthChangePinRequest, AuthChangePinResponse, AuthLoginRequest, AuthLoginResponse, AuthMeResponse, AuthUserResponse, SubscriptionResponse


SESSION_COOKIE_NAME = "symgov_session"

router = APIRouter(prefix="/auth", tags=["auth"])
legacy_router = APIRouter(tags=["auth"])


def auth_user_response(user: AuthenticatedUser) -> AuthUserResponse:
    return AuthUserResponse(
        id=user.id,
        email=user.email,
        displayName=user.display_name,
        roles=list(user.roles),
        mustChangePin=user.must_change_pin,
        subscription=SubscriptionResponse(
            tier=user.subscription_tier,
            startedOn=user.subscription_started_on.isoformat(),
            expiresOn=user.subscription_expires_on.isoformat() if user.subscription_expires_on else None,
            isActive=user.subscription_tier == "plus",
            isProtected=user.subscription_is_protected,
        ),
    )


def cookie_secure(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    return request.url.scheme == "https" or forwarded_proto == "https"


@router.post("/login", response_model=AuthLoginResponse)
@legacy_router.post("/auth/login", response_model=AuthLoginResponse, include_in_schema=False)
async def login(
    http_request: Request,
    response: Response,
    session: Session = Depends(get_db_session),
) -> AuthLoginResponse:
    payload = await http_request.json()
    login_request = AuthLoginRequest.model_validate(payload.get("payload") or payload)
    user = authenticate_user(session, email=login_request.email, pin=login_request.pin)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or PIN.")
    token = create_user_session(session, user=user)
    session.commit()
    current = current_user_from_token(session, token)
    if current is None:
        raise HTTPException(status_code=500, detail="Login session could not be created.")
    session.commit()
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        secure=cookie_secure(http_request),
        samesite="lax",
        path="/",
        max_age=14 * 24 * 60 * 60,
    )
    return AuthLoginResponse(user=auth_user_response(current))


@router.get("/me", response_model=AuthMeResponse)
@legacy_router.get("/auth/me", response_model=AuthMeResponse, include_in_schema=False)
def me(request: Request, session: Session = Depends(get_db_session)) -> AuthMeResponse:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    current = current_user_from_token(session, token)
    if current is None:
        return AuthMeResponse(user=None)
    session.commit()
    return AuthMeResponse(user=auth_user_response(current))


@router.post("/logout")
@legacy_router.post("/auth/logout", include_in_schema=False)
def logout(request: Request, response: Response, session: Session = Depends(get_db_session)) -> dict[str, bool]:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    revoked = revoke_session(session, token)
    session.commit()
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True, "revoked": revoked}


@router.post("/change-pin", response_model=AuthChangePinResponse)
@legacy_router.post("/auth/change-pin", response_model=AuthChangePinResponse, include_in_schema=False)
async def change_pin(request: Request, session: Session = Depends(get_db_session)) -> AuthChangePinResponse:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    current = current_user_from_token(session, token)
    if current is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    payload = await request.json()
    pin_request = AuthChangePinRequest.model_validate(payload.get("payload") or payload)
    user = session.get(User, uuid.UUID(current.id))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if not verify_pin(pin_request.currentPin, user.pin_hash):
        raise HTTPException(status_code=400, detail="Current PIN is incorrect.")
    user.pin_hash = hash_pin(pin_request.newPin)
    user.pin_set_at = utc_now()
    user.must_change_pin = False
    user.updated_at = user.pin_set_at
    session.commit()
    refreshed = current_user_from_token(session, token)
    if refreshed is None:
        raise HTTPException(status_code=500, detail="Updated user session could not be loaded.")
    return AuthChangePinResponse(user=auth_user_response(refreshed))
