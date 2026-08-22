from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..auth import (
    AuthenticatedUser,
    complete_credential_change,
    create_user_session,
    current_user_from_token,
    revoke_outstanding_selection_challenges,
    revoke_session,
    utc_now,
)
from ..auth_security import authenticate_login, login_throttle_policy, reauthenticate_session
from ..dependencies import (
    get_db_session,
    require_i25_protected_mutation,
    require_recent_step_up,
    require_user,
    resolve_client_ip,
)
from ..models import AuthOrganizationSelectionChallenge, User
from ..organization_authorization import resolve_eligible_organization_memberships
from ..schemas import (
    AuthChangePinRequest,
    AuthChangePinResponse,
    AuthLoginRequest,
    AuthLoginResponse,
    AuthMeResponse,
    AuthReauthenticateRequest,
    AuthSelectionChallengeResponse,
    AuthSelectOrganizationRequest,
    AuthUserResponse,
    SubscriptionResponse,
)
from ..settings import SymgovAPISettings, get_settings


SESSION_COOKIE_NAME = "symgov_session"
SELECTION_CHALLENGE_ERROR = "Organization selection challenge is invalid or unavailable."

router = APIRouter(prefix="/auth", tags=["auth"])
legacy_router = APIRouter(tags=["auth"])


def auth_user_response(user: AuthenticatedUser, settings: SymgovAPISettings | None = None) -> AuthUserResponse:
    effective = settings or SymgovAPISettings()
    pilot_codes = {
        str(code).strip().lower()
        for code in effective.organization_pilot_codes
        if str(code).strip()
    }
    organization_icon_upload_enabled = bool(
        effective.organizations_enabled
        and effective.organization_admin_enabled
        and effective.organization_custom_icons_enabled
        and effective.organization_icon_upload_enabled
        and user.session_purpose == "application"
        and user.session_mode == "organization"
        and user.active_organization_id is not None
        and user.organization_base_role == "admin"
        and user.organization_code
        and user.organization_code.strip().lower() in pilot_codes
    )
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
        session={"purpose": user.session_purpose, "mode": user.session_mode, "activeOrganizationId": user.active_organization_id},
        organization=(
            {"id": user.active_organization_id, "code": user.organization_code, "displayName": user.organization_display_name,
             "baseRole": user.organization_base_role, "capabilities": list(user.organization_capabilities)}
            if user.active_organization_id else None
        ),
        isPlatformAdmin=user.is_platform_admin,
        capabilities={
            "organizationsEnabled": effective.organizations_enabled,
            "organizationAdminEnabled": effective.organizations_enabled and effective.organization_admin_enabled,
            "platformAdminEnabled": (
                effective.organizations_enabled
                and effective.organization_admin_enabled
                and effective.platform_admin_enabled
            ),
            "symbolSetsEnabled": effective.organizations_enabled and effective.symbol_sets_enabled,
            "organizationSymbolsEnabled": effective.organizations_enabled and effective.organization_symbols_enabled,
            "organizationAgentsEnabled": effective.organizations_enabled and effective.organization_agents_enabled,
            "organizationIconUploadEnabled": organization_icon_upload_enabled,
        },
        recentStepUpAt=user.recent_step_up_at.isoformat() if user.recent_step_up_at else None,
    )


def cookie_secure(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    return request.url.scheme == "https" or forwarded_proto == "https"


def _aware_utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("Expected a datetime value.")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _selection_challenge_error() -> HTTPException:
    return HTTPException(status_code=401, detail=SELECTION_CHALLENGE_ERROR)


def _selection_snapshot(challenge: AuthOrganizationSelectionChallenge) -> list[dict[str, str]] | None:
    serialized = challenge.eligible_organizations_json
    actual_hash = hashlib.sha256(serialized.encode()).hexdigest()
    if not secrets.compare_digest(actual_hash, challenge.eligible_organizations_hash):
        return None
    try:
        snapshot = json.loads(serialized)
        if not isinstance(snapshot, list):
            return None
        normalized = []
        for item in snapshot:
            if not isinstance(item, dict) or set(item) != {"organizationId", "code", "displayName"}:
                return None
            organization_id = str(uuid.UUID(item["organizationId"]))
            code = item["code"]
            display_name = item["displayName"]
            if not isinstance(code, str) or not code or not isinstance(display_name, str) or not display_name:
                return None
            normalized.append({
                "organizationId": organization_id,
                "code": code,
                "displayName": display_name,
            })
        return normalized
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _challenge_is_usable(challenge: AuthOrganizationSelectionChallenge, now: datetime) -> bool:
    return (
        challenge.consumed_at is None
        and challenge.revoked_at is None
        and challenge.attempt_count < challenge.max_attempts
        and _aware_utc(challenge.expires_at) > _aware_utc(now)
    )


def issue_application_context(
    session: Session,
    *,
    user: User,
    settings: SymgovAPISettings,
) -> tuple[str | None, AuthSelectionChallengeResponse | None]:
    now = utc_now()
    revoke_outstanding_selection_challenges(session, user.id, now=now)
    eligible = resolve_eligible_organization_memberships(session, user, settings)
    if len(eligible) > 1:
        raw_token = secrets.token_urlsafe(32)
        snapshot = [
            {
                "organizationId": str(item.organization_id),
                "code": item.code,
                "displayName": item.display_name,
            }
            for item in eligible
        ]
        serialized = json.dumps(snapshot, separators=(",", ":"), sort_keys=True)
        expires_at = now + timedelta(minutes=10)
        session.add(
            AuthOrganizationSelectionChallenge(
                id=uuid.uuid4(),
                user_id=user.id,
                token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                eligible_organizations_hash=hashlib.sha256(serialized.encode()).hexdigest(),
                eligible_organizations_json=serialized,
                expires_at=expires_at,
                max_attempts=5,
                attempt_count=0,
                created_at=now,
                updated_at=now,
            )
        )
        return None, AuthSelectionChallengeResponse(
            token=raw_token,
            expiresAt=expires_at.isoformat(),
            choices=snapshot[:5],
            page=1,
            pageSize=5,
            total=len(snapshot),
            hasMore=len(snapshot) > 5,
        )
    selected = eligible[0] if eligible else None
    return (
        create_user_session(
            session,
            user=user,
            purpose="application",
            session_mode="organization" if selected else "personal",
            active_organization_id=selected.organization_id if selected else None,
            recent_step_up_at=None,
        ),
        None,
    )


@router.post("/select-organization", response_model=AuthLoginResponse)
async def select_organization(
    http_request: Request,
    response: Response,
    session: Session = Depends(get_db_session),
    settings: SymgovAPISettings = Depends(get_settings),
) -> AuthLoginResponse:
    payload = await http_request.json()
    selection_request = AuthSelectOrganizationRequest.model_validate(payload.get("payload") or payload)
    token_hash = hashlib.sha256(selection_request.token.encode()).hexdigest()
    initial = session.query(AuthOrganizationSelectionChallenge).filter(
        AuthOrganizationSelectionChallenge.token_hash == token_hash
    ).one_or_none()
    if initial is None:
        raise _selection_challenge_error()

    now = utc_now()
    if selection_request.organizationId is None:
        if not _challenge_is_usable(initial, now):
            raise _selection_challenge_error()
        snapshot = _selection_snapshot(initial)
        if snapshot is None:
            initial.revoked_at = now
            initial.updated_at = now
            session.commit()
            raise _selection_challenge_error()
        start = (selection_request.page - 1) * selection_request.pageSize
        end = start + selection_request.pageSize
        return AuthLoginResponse(
            user=None,
            selectionChallenge=AuthSelectionChallengeResponse(
                token=selection_request.token,
                expiresAt=_aware_utc(initial.expires_at).isoformat(),
                choices=snapshot[start:end],
                page=selection_request.page,
                pageSize=selection_request.pageSize,
                total=len(snapshot),
                hasMore=end < len(snapshot),
            ),
        )

    user = session.query(User).filter(User.id == initial.user_id).with_for_update().populate_existing().one_or_none()
    challenge = session.query(AuthOrganizationSelectionChallenge).filter(
        AuthOrganizationSelectionChallenge.id == initial.id,
        AuthOrganizationSelectionChallenge.token_hash == token_hash,
    ).with_for_update().populate_existing().one_or_none()
    if user is None or challenge is None or not _challenge_is_usable(challenge, now):
        raise _selection_challenge_error()
    snapshot = _selection_snapshot(challenge)
    if snapshot is None:
        challenge.revoked_at = now
        challenge.updated_at = now
        session.commit()
        raise _selection_challenge_error()

    selected_id = selection_request.organizationId
    if not any(item["organizationId"] == str(selected_id) for item in snapshot):
        challenge.attempt_count += 1
        challenge.updated_at = now
        if challenge.attempt_count >= challenge.max_attempts:
            challenge.revoked_at = now
        session.commit()
        raise _selection_challenge_error()

    eligible = resolve_eligible_organization_memberships(session, user, settings)
    selected = next((item for item in eligible if item.organization_id == selected_id), None)
    if selected is None:
        challenge.revoked_at = now
        challenge.updated_at = now
        session.commit()
        raise _selection_challenge_error()

    challenge.consumed_at = now
    challenge.updated_at = now
    token = create_user_session(
        session,
        user=user,
        purpose="application",
        session_mode="organization",
        active_organization_id=selected.organization_id,
        recent_step_up_at=None,
    )
    current = current_user_from_token(session, token, settings=settings)
    if current is None:
        session.rollback()
        raise HTTPException(status_code=500, detail="Organization session could not be created.")
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
    return AuthLoginResponse(user=auth_user_response(current, settings), selectionChallenge=None)


@router.post("/login", response_model=AuthLoginResponse)
@legacy_router.post("/auth/login", response_model=AuthLoginResponse, include_in_schema=False)
async def login(
    http_request: Request,
    response: Response,
    session: Session = Depends(get_db_session),
    settings: SymgovAPISettings = Depends(get_settings),
) -> AuthLoginResponse:
    payload = await http_request.json()
    login_request = AuthLoginRequest.model_validate(payload.get("payload") or payload)
    result = authenticate_login(
        session,
        email=login_request.email,
        pin=login_request.pin,
        client_ip=resolve_client_ip(
            http_request.client.host if http_request.client else None,
            http_request.headers.get("x-forwarded-for"),
            settings,
        ),
        policy=login_throttle_policy(settings),
    )
    if result.throttled_scope is not None:
        session.commit()
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Try again later.",
            headers={"Retry-After": str(result.retry_after_seconds or 1)},
        )
    user = result.user
    if user is None:
        session.commit()
        raise HTTPException(status_code=401, detail="Invalid email or PIN.")
    revoke_outstanding_selection_challenges(session, user.id)
    now = utc_now()
    if user.must_change_pin:
        token = create_user_session(session, user=user, ttl_hours=0.5, purpose="credential_change", recent_step_up_at=None)
    else:
        token, selection_challenge = issue_application_context(session, user=user, settings=settings)
        if selection_challenge is not None:
            session.commit()
            response.delete_cookie(SESSION_COOKIE_NAME, path="/")
            return AuthLoginResponse(user=None, selectionChallenge=selection_challenge)
    session.commit()
    if token is None:
        raise HTTPException(status_code=500, detail="Login session could not be created.")
    current = current_user_from_token(session, token, settings=settings)
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
    return AuthLoginResponse(user=auth_user_response(current, settings))


@router.post("/reauthenticate")
async def reauthenticate(
    http_request: Request,
    session: Session = Depends(get_db_session),
    settings: SymgovAPISettings = Depends(get_settings),
    current_user: AuthenticatedUser = Depends(require_user),
) -> dict[str, bool]:
    token = http_request.cookies.get(SESSION_COOKIE_NAME, "")
    payload = await http_request.json()
    auth_request = AuthReauthenticateRequest.model_validate(payload.get("payload") or payload)

    result = reauthenticate_session(
        session,
        email=current_user.email,
        token=token,
        pin=auth_request.pin,
        client_ip=resolve_client_ip(
            http_request.client.host if http_request.client else None,
            http_request.headers.get("x-forwarded-for"),
            settings,
        ),
        policy=login_throttle_policy(settings),
    )

    if result.throttled_scope is not None:
        session.commit()
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Try again later.",
            headers={"Retry-After": str(result.retry_after_seconds or 1)},
        )

    if result.user is None:
        session.commit()
        raise HTTPException(status_code=401, detail="Invalid PIN.")

    session.commit()
    return {"ok": True}


@router.get("/me", response_model=AuthMeResponse)
@legacy_router.get("/auth/me", response_model=AuthMeResponse, include_in_schema=False)
def me(request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)) -> AuthMeResponse:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    current = current_user_from_token(session, token, settings=settings)
    if current is None:
        return AuthMeResponse(user=None)
    session.commit()
    return AuthMeResponse(user=auth_user_response(current, settings))


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
async def change_pin(
    request: Request,
    response: Response,
    session: Session = Depends(get_db_session),
    settings: SymgovAPISettings = Depends(get_settings),
    _: AuthenticatedUser = Depends(require_i25_protected_mutation),
) -> AuthChangePinResponse:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    payload = await request.json()
    pin_request = AuthChangePinRequest.model_validate(payload.get("payload") or payload)
    try:
        user, token = complete_credential_change(
            session,
            token=token,
            current_pin=pin_request.currentPin,
            new_pin=pin_request.newPin,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 400 if detail in {
            "Current PIN is incorrect.",
            "New PIN must be different from the current PIN.",
        } else 401
        raise HTTPException(status_code=status_code, detail=detail) from exc
    if token is None:
        token, selection_challenge = issue_application_context(session, user=user, settings=settings)
        if selection_challenge is not None:
            session.commit()
            response.delete_cookie(SESSION_COOKIE_NAME, path="/")
            return AuthChangePinResponse(user=None, selectionChallenge=selection_challenge)
    session.commit()
    if token is None:
        raise HTTPException(status_code=500, detail="Updated user session could not be created.")
    refreshed = current_user_from_token(session, token, settings=settings)
    if refreshed is None:
        raise HTTPException(status_code=500, detail="Updated user session could not be loaded.")
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        secure=cookie_secure(request),
        samesite="lax",
        path="/",
        max_age=14 * 24 * 60 * 60,
    )
    return AuthChangePinResponse(user=auth_user_response(refreshed, settings))
