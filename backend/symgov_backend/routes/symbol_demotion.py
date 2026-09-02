"""Stage 7 WP7.4 -- demotion impact preview and execution API.

Mounted behind `organizations_enabled`, `organization_symbols_enabled`, and
`platform_admin_enabled` (all default off) -- demotion only concerns
organization-symbol-visibility semantics but is exclusively a Platform
Admin action, so it requires all three prerequisite flags active.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import AuthenticatedUser
from ..dependencies import get_db_session, require_platform_admin, require_recent_step_up
from ..schemas import DemotionExecuteRequest, DemotionImpactPreviewResponse, DemotionResponse
from ..settings import SymgovAPISettings, get_settings
from ..symbol_demotion import (
    DemotionError,
    DemotionIneligible,
    DemotionNotVisible,
    execute_demotion,
    preview_demotion,
)

router = APIRouter(prefix="/platform/governed-symbols", tags=["symbol-demotion"])


def symbol_demotion_route_guard(settings: SymgovAPISettings = Depends(get_settings)) -> None:
    if not (settings.organizations_enabled and settings.organization_symbols_enabled and settings.platform_admin_enabled):
        raise HTTPException(status_code=404, detail="Not found.")


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(status_code=404, detail="Governed symbol was not found.") from exc


@router.get(
    "/{symbol_id}/demotion-impact-preview",
    response_model=DemotionImpactPreviewResponse,
)
def get_demotion_impact_preview(
    symbol_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
) -> DemotionImpactPreviewResponse:
    parsed_symbol_id = _parse_uuid(symbol_id)
    try:
        preview = preview_demotion(session, current_user, symbol_id=parsed_symbol_id)
    except DemotionNotVisible as exc:
        raise HTTPException(status_code=404, detail="Governed symbol was not found.") from exc
    except DemotionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DemotionImpactPreviewResponse(
        governedSymbolId=str(preview.symbol.id),
        eligible=preview.eligible,
        reasons=preview.reasons,
        blockingOrganizationIds=[str(org_id) for org_id in preview.blocking_organization_ids],
        favouritesCount=preview.favourites_count,
    )


@router.post(
    "/{symbol_id}/demote",
    response_model=DemotionResponse,
)
def demote_governed_symbol(
    symbol_id: str,
    body: DemotionExecuteRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> DemotionResponse:
    parsed_symbol_id = _parse_uuid(symbol_id)
    try:
        result = execute_demotion(session, current_user, symbol_id=parsed_symbol_id, reason=body.reason)
    except DemotionNotVisible as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Governed symbol was not found.") from exc
    except DemotionIneligible as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DemotionError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return DemotionResponse(
        governedSymbolId=str(result.symbol.id),
        visibility=result.symbol.visibility,
        symbolRevisionIds=[str(revision_id) for revision_id in result.revision_ids],
        publishedPageIds=[str(page_id) for page_id in result.published_page_ids],
        packEntryIds=[str(entry_id) for entry_id in result.pack_entry_ids],
        retiredPackIds=[str(pack_id) for pack_id in result.retired_pack_ids],
    )
