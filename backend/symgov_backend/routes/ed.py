from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..auth import AuthenticatedUser
from ..dependencies import get_db_session, require_user
from ..schemas import EdChatRequest, EdChatResponse
from ..services.ed_orchestration import orchestrate_ed_chat
from ..settings import SymgovAPISettings, get_settings


router = APIRouter(tags=["ed"])


@router.post("/ed/chat", response_model=EdChatResponse)
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
