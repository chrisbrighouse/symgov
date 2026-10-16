"""Organization Catalog API keys: the admin's issue/list/revoke routes and the
holder's list/claim routes.

The admin routes are organization-bound and admin-only, like the other
`/org/me/...` administration (`require_stage4_principal`, with the PIN step-up that
member changes need, because issuing a credential is as sensitive; which also needs
Symbol Sets switched on: organization administration of this kind is only
offered alongside them). The holder routes belong to the signed-in user, not to
an organization session: a holder may be signed in anywhere when they collect a
key. Both are behind `SYMGOV_ORGANIZATION_API_KEYS_ENABLED`.
"""

from __future__ import annotations

from datetime import datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..auth import AuthenticatedUser
from ..catalog_api_keys import CatalogApiKeyError, CatalogApiKeyNotFoundError
from ..catalog_org_api_keys import (
    CatalogOrganizationKeyAlreadyClaimedError,
    CatalogOrganizationKeyNotAllowedError,
    claim_organization_catalog_api_key,
    issue_organization_catalog_api_key,
    list_holder_catalog_api_keys,
    list_organization_catalog_api_keys,
    revoke_organization_catalog_api_key,
)
from ..dependencies import get_db_session, require_recent_step_up, require_user
from ..settings import SymgovAPISettings, get_settings
from ..stage4_authorization import require_stage4_principal

router = APIRouter(prefix="/org/me/api-keys", tags=["organization-api-keys"])
holder_router = APIRouter(prefix="/profile/api-keys", tags=["organization-api-keys"])


def organization_api_keys_route_guard(settings: SymgovAPISettings = Depends(get_settings)) -> None:
    if not (settings.organizations_enabled and settings.organization_api_keys_enabled):
        raise HTTPException(status_code=404, detail="Not found.")


class IssueOrganizationApiKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    holderUserId: uuid.UUID
    integrationName: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=lambda: ["catalog.read", "catalog.preview"], max_length=10)
    expiresAt: datetime | None = None
    rateLimitPerMinute: int | None = Field(default=None, ge=1)
    allowedOrigins: list[str] = Field(default_factory=list, max_length=20)


def _rollback(session: Session) -> None:
    try:
        session.rollback()
    except Exception:
        pass


@router.get("")
def list_organization_keys(
    request: Request,
    session: Session = Depends(get_db_session),
    settings: SymgovAPISettings = Depends(get_settings),
) -> dict:
    principal = require_stage4_principal(session, request, settings, admin=True)
    return {
        "items": list_organization_catalog_api_keys(session, principal.organization.id),
        "defaults": {
            "rateLimitPerMinute": settings.catalog_api_organization_rate_limit_per_minute,
            "maxRateLimitPerMinute": settings.catalog_api_max_rate_limit_per_minute,
        },
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def issue_organization_key(
    body: IssueOrganizationApiKeyRequest,
    request: Request,
    session: Session = Depends(get_db_session),
    settings: SymgovAPISettings = Depends(get_settings),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> dict:
    principal = require_stage4_principal(session, request, settings, admin=True)
    try:
        key = issue_organization_catalog_api_key(
            session,
            organization_id=principal.organization.id,
            issuer_user_id=principal.user.id,
            holder_user_id=body.holderUserId,
            integration_name=body.integrationName,
            scopes=body.scopes,
            expires_at=body.expiresAt,
            rate_limit_per_minute=body.rateLimitPerMinute,
            allowed_origins=body.allowedOrigins,
            max_rate_limit_per_minute=settings.catalog_api_max_rate_limit_per_minute,
        )
        session.commit()
    except CatalogOrganizationKeyNotAllowedError as error:
        _rollback(session)
        raise HTTPException(status_code=409, detail=str(error)) from None
    except CatalogApiKeyError as error:
        _rollback(session)
        raise HTTPException(status_code=422, detail=str(error)) from None
    return {
        "key": key,
        "notice": "The key holder collects the secret from their profile; it is shown to them once.",
    }


@router.delete("/{key_id}")
def revoke_organization_key(
    key_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_db_session),
    settings: SymgovAPISettings = Depends(get_settings),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> dict:
    principal = require_stage4_principal(session, request, settings, admin=True)
    try:
        key = revoke_organization_catalog_api_key(
            session, organization_id=principal.organization.id, api_key_id=key_id, actor_id=principal.user.id,
        )
        session.commit()
    except CatalogApiKeyNotFoundError:
        _rollback(session)
        raise HTTPException(status_code=404, detail="Not found.") from None
    return {"key": key}


@holder_router.get("")
def list_my_organization_keys(
    current_user: AuthenticatedUser = Depends(require_user),
    session: Session = Depends(get_db_session),
) -> dict:
    return {"items": list_holder_catalog_api_keys(session, uuid.UUID(str(current_user.id)))}


@holder_router.post("/{key_id}/claim")
def claim_my_organization_key(
    key_id: uuid.UUID,
    current_user: AuthenticatedUser = Depends(require_user),
    session: Session = Depends(get_db_session),
) -> dict:
    try:
        key, raw_key = claim_organization_catalog_api_key(
            session, api_key_id=key_id, user_id=uuid.UUID(str(current_user.id)),
        )
        session.commit()
    except CatalogApiKeyNotFoundError:
        _rollback(session)
        raise HTTPException(status_code=404, detail="Not found.") from None
    except CatalogOrganizationKeyAlreadyClaimedError as error:
        _rollback(session)
        raise HTTPException(status_code=409, detail=str(error)) from None
    except CatalogOrganizationKeyNotAllowedError as error:
        _rollback(session)
        raise HTTPException(status_code=409, detail=str(error)) from None
    return {"key": key, "rawKey": raw_key, "warning": "This key is shown once. Save it now; it cannot be shown again."}
