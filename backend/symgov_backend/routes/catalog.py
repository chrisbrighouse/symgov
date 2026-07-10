from __future__ import annotations

from time import perf_counter

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..catalog_api_auth import IntegrationAuthContext, require_catalog_scope
from ..catalog_taxonomy import (
    CATALOG_CATEGORY_ORDER,
    CATALOG_DISCIPLINE_ORDER,
    CATALOG_USE_CASE_ORDER,
    FORMAT_ORDER,
)
from ..catalog_usage import log_catalog_usage_event_best_effort
from ..dependencies import get_db_session

router = APIRouter(prefix="/catalog", tags=["catalog"])

CATALOG_READ_SCOPE = "catalog.read"
PUBLIC_CATALOG_LINKS = {
    "capabilities": "/api/v1/catalog/capabilities",
    "taxonomy": "/api/v1/catalog/taxonomy",
    "symbols": "/api/v1/catalog/symbols",
    "symbolSearch": "/api/v1/catalog/search",
    "edQuery": "/api/v1/catalog/ed/query",
    "feedback": "/api/v1/catalog/symbols/{symbolRef}/feedback",
}


def _latency_ms(started_at: float) -> int:
    return max(0, int((perf_counter() - started_at) * 1000))


def _log_successful_catalog_read(
    session: Session,
    auth_context: IntegrationAuthContext,
    *,
    request: Request,
    route_name: str,
    started_at: float,
) -> None:
    log_catalog_usage_event_best_effort(
        session,
        auth_context,
        request=request,
        scope_used=CATALOG_READ_SCOPE,
        route_name=route_name,
        status_code=200,
        latency_ms=_latency_ms(started_at),
    )


@router.get("/capabilities")
def get_catalog_capabilities(
    request: Request,
    auth_context: IntegrationAuthContext = Depends(require_catalog_scope(CATALOG_READ_SCOPE)),
    session: Session = Depends(get_db_session),
) -> dict:
    started_at = perf_counter()
    response = {
        "apiVersion": "v1",
        "catalogName": "Symgov Catalog",
        "downloadAvailable": False,
        "auth": {
            "methods": ["api_key"],
            "preferredHeader": "Authorization: Bearer ***",
            "requiredScopes": [CATALOG_READ_SCOPE],
        },
        "supports": {
            "keywordSearch": True,
            "facetSearch": True,
            "contextualSearch": True,
            "taxonomy": True,
            "edQuestions": True,
            "previews": True,
            "feedback": True,
            "usageReporting": True,
            "download": False,
        },
        "currentEndpoints": [
            {
                "method": "GET",
                "path": "/api/v1/catalog/capabilities",
                "scope": CATALOG_READ_SCOPE,
            },
            {
                "method": "GET",
                "path": "/api/v1/catalog/taxonomy",
                "scope": CATALOG_READ_SCOPE,
            },
        ],
        "futureCapabilities": [
            "paginated symbol search",
            "symbol detail and preview aliases",
            "contextual Catalog search",
            "Ed question and symbol-finding support",
            "integration feedback submission",
            "customer usage reporting",
        ],
        "scopes": [
            "catalog.read",
            "catalog.preview",
            "catalog.ed.query",
            "catalog.feedback.write",
            "catalog.usage.read",
        ],
        "links": PUBLIC_CATALOG_LINKS,
    }
    _log_successful_catalog_read(session, auth_context, request=request, route_name="catalog_capabilities", started_at=started_at)
    return response


@router.get("/taxonomy")
def get_catalog_taxonomy(
    request: Request,
    auth_context: IntegrationAuthContext = Depends(require_catalog_scope(CATALOG_READ_SCOPE)),
    session: Session = Depends(get_db_session),
) -> dict:
    started_at = perf_counter()
    response = {
        "apiVersion": "v1",
        "catalogName": "Symgov Catalog",
        "downloadAvailable": False,
        "facets": {
            "disciplines": CATALOG_DISCIPLINE_ORDER,
            "categories": CATALOG_CATEGORY_ORDER,
            "formats": FORMAT_ORDER,
            "useCases": CATALOG_USE_CASE_ORDER,
        },
        "metadata": {
            "source": "symgov_backend.catalog_taxonomy",
            "canonical": True,
        },
        "links": {
            "capabilities": "/api/v1/catalog/capabilities",
            "symbols": "/api/v1/catalog/symbols",
        },
    }
    _log_successful_catalog_read(session, auth_context, request=request, route_name="catalog_taxonomy", started_at=started_at)
    return response
