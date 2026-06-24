from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..auth import AuthenticatedUser, authenticate_user, create_user_session, current_user_from_token, revoke_session
from ..dependencies import get_db_session
from ..schemas import AuthLoginRequest, AuthLoginResponse, AuthMeResponse, AuthUserResponse


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
