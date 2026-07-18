from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..auth import AuthenticatedUser
from ..catalog_api_auth import PLANNED_CATALOG_API_SCOPES, get_catalog_api_key_context
from ..catalog_api_keys import (
    CatalogApiKeyAlreadyActiveError,
    CatalogApiKeyDTO,
    CatalogApiKeyError,
    CatalogApiKeyNotFoundError,
    CatalogApiKeyPrefixMismatchError,
    create_self_service_catalog_api_key,
    get_active_self_service_catalog_api_key,
    revoke_self_service_catalog_api_key,
)
from ..catalog_developer import catalog_openapi_document, developer_manifest
from ..catalog_integration_ed import answer_integration_question, read_integration_ed_body
from ..catalog_sandbox import read_sandbox_body, run_sandbox
from ..dependencies import get_db_session, require_user
from ..schemas import (
    CatalogSelfServiceApiKeyCreateEnvelope,
    CatalogSelfServiceApiKeyCreateRequest,
    CatalogSelfServiceApiKeyRevokeEnvelope,
    CatalogSelfServiceApiKeyRevokeRequest,
)

router = APIRouter(prefix="/catalog/developer", tags=["catalog-developer"])


def _rollback_without_diagnostics(session: Session) -> None:
    try:
        session.rollback()
    except Exception:
        pass


def _key_payload(key: CatalogApiKeyDTO) -> dict[str, object]:
    return {
        "keyId": str(key.id),
        "keyPrefix": key.key_prefix,
        "customerName": key.customer_name,
        "integrationName": key.integration_name,
        "scopes": list(key.scopes),
        "status": key.status,
        "expiresAt": key.expires_at.isoformat() if key.expires_at else None,
        "lastUsedAt": key.last_used_at.isoformat() if key.last_used_at else None,
        "createdAt": key.created_at.isoformat(),
        "revokedAt": key.revoked_at.isoformat() if key.revoked_at else None,
    }


@router.get("/api-key")
def get_self_service_api_key_status(
    current_user: AuthenticatedUser = Depends(require_user),
    session: Session = Depends(get_db_session),
) -> dict:
    key = get_active_self_service_catalog_api_key(session, current_user.id)
    return {
        "activeKey": _key_payload(key) if key else None,
        "availableScopes": sorted(PLANNED_CATALOG_API_SCOPES),
        "access": {"mode": "free", "subscriptionRequired": False},
    }


@router.post("/api-key", status_code=status.HTTP_201_CREATED)
async def create_self_service_api_key(
    http_request: Request,
    current_user: AuthenticatedUser = Depends(require_user),
    session: Session = Depends(get_db_session),
) -> dict:
    try:
        body = await http_request.json()
        payload = (
            CatalogSelfServiceApiKeyCreateEnvelope.model_validate(body).request
            if isinstance(body, dict) and "request" in body
            else CatalogSelfServiceApiKeyCreateRequest.model_validate(body)
        )
    except (ValueError, ValidationError, AttributeError):
        raise HTTPException(status_code=422, detail="Catalog API key request was invalid.") from None
    try:
        created = create_self_service_catalog_api_key(
            session,
            user_id=current_user.id,
            customer_name=payload.customerName,
            integration_name=payload.integrationName,
            scopes=payload.scopes,
            expires_at=payload.expiresAt,
        )
        session.commit()
    except CatalogApiKeyAlreadyActiveError:
        _rollback_without_diagnostics(session)
        raise HTTPException(status_code=409, detail="An active Catalog API key already exists for this account.") from None
    except CatalogApiKeyError:
        _rollback_without_diagnostics(session)
        raise HTTPException(status_code=400, detail="Catalog API key request was invalid.") from None
    except Exception:
        _rollback_without_diagnostics(session)
        raise HTTPException(status_code=500, detail="Catalog API key could not be created.") from None
    return {
        "activeKey": _key_payload(created.key),
        "rawKey": created.raw_key,
        "warning": "This generated key must be saved now; it won't be accessible again.",
    }


@router.delete("/api-key")
async def revoke_self_service_api_key(
    http_request: Request,
    current_user: AuthenticatedUser = Depends(require_user),
    session: Session = Depends(get_db_session),
) -> dict:
    try:
        body = await http_request.json()
        payload = (
            CatalogSelfServiceApiKeyRevokeEnvelope.model_validate(body).request
            if isinstance(body, dict) and "request" in body
            else CatalogSelfServiceApiKeyRevokeRequest.model_validate(body)
        )
    except (ValueError, ValidationError, AttributeError):
        raise HTTPException(status_code=422, detail="Catalog API key request was invalid.") from None
    try:
        revoked = revoke_self_service_catalog_api_key(
            session,
            user_id=current_user.id,
            api_key_id=payload.keyId,
            key_prefix=payload.keyPrefix,
        )
        session.commit()
    except CatalogApiKeyNotFoundError:
        _rollback_without_diagnostics(session)
        raise HTTPException(status_code=404, detail="Catalog API key was not found.") from None
    except CatalogApiKeyPrefixMismatchError:
        _rollback_without_diagnostics(session)
        raise HTTPException(status_code=400, detail="Catalog API key confirmation did not match.") from None
    except CatalogApiKeyError:
        _rollback_without_diagnostics(session)
        raise HTTPException(status_code=400, detail="Catalog API key request was invalid.") from None
    except Exception:
        _rollback_without_diagnostics(session)
        raise HTTPException(status_code=500, detail="Catalog API key could not be revoked.") from None
    return {"activeKey": None, "revokedKey": _key_payload(revoked)}


@router.get("")
def get_developer_manifest() -> dict:
    return developer_manifest()


@router.get("/openapi.json")
def get_catalog_openapi() -> dict:
    return catalog_openapi_document()


@router.post("/sandbox", dependencies=[Depends(get_catalog_api_key_context)])
async def execute_catalog_sandbox(request: Request) -> dict:
    return run_sandbox(await read_sandbox_body(request))


@router.post("/ed", dependencies=[Depends(get_catalog_api_key_context)])
async def ask_catalog_integration_ed(request: Request) -> dict:
    return answer_integration_question(await read_integration_ed_body(request))
