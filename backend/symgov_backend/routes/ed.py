from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..auth import AuthenticatedUser
from ..dependencies import get_db_session, require_user
from ..schemas import EdChatRequest, EdChatResponse
from ..services.ed_orchestration import orchestrate_ed_chat
from ..settings import SymgovAPISettings, get_settings


router = APIRouter(tags=["ed"])


def ed_pilot_allows(user: AuthenticatedUser, settings: SymgovAPISettings) -> bool:
    """Whether the session's active organization is a named Ed pilot.

    The organization comes from the authenticated session, never the
    request. A personal session belongs to no organization, so it is outside
    every pilot.
    """
    if user.session_mode != "organization" or not user.active_organization_id:
        return False
    code = (user.organization_code or "").strip().lower()
    return bool(code) and code in settings.ed_pilot_organization_codes


def require_ed_pilot(
    user: AuthenticatedUser = Depends(require_user),
    settings: SymgovAPISettings = Depends(get_settings),
) -> None:
    """A closed pilot is absent, not forbidden.

    404 rather than 403, matching `semantic_review_route_guard`. This runs
    as a route dependency, so it fires before body validation, the rate
    limiter and any provider call.
    """
    if not ed_pilot_allows(user, settings):
        raise HTTPException(status_code=404, detail="Not found.")


@router.post(
    "/ed/chat",
    response_model=EdChatResponse,
    dependencies=[Depends(require_ed_pilot)],
)
async def ed_chat(
    http_request: Request,
    response: Response,
    payload: EdChatRequest,
    session: Session = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_user),
    settings: SymgovAPISettings = Depends(get_settings),
) -> EdChatResponse:
    response.headers["Cache-Control"] = "no-store, private"
    user_context = {
        "user_id": user.id,
        "organization_id": user.active_organization_id,
        "organization_display_name": user.organization_display_name,
        "organization_base_role": user.organization_base_role,
        "roles": user.roles,
    }
    return await run_in_threadpool(
        orchestrate_ed_chat,
        session,
        http_request,
        payload,
        user_context,
        settings,
    )
