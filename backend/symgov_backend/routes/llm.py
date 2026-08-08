from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from ..dependencies import get_db_session, require_any_role, require_user
from ..db import create_session_factory
from ..settings import SymgovAPISettings, get_settings
from ..schemas import (
    LLMChatRequest,
    LLMChatResponse,
    LLMSettingsResponse,
    LLMSettingsUpdateRequest,
    OpenRouterModelListResponse,
)
from ..services.llm import (
    configured_openrouter_models_from_profile,
    fetch_openrouter_models,
    load_llm_settings,
    openrouter_api_key,
    resolve_model_for_feature,
    save_llm_settings,
)
from ..services.llm_router import request_llm_completion


router = APIRouter(tags=["llm"])
legacy_router = APIRouter(tags=["llm"])


def _build_settings_response() -> LLMSettingsResponse:
    settings = load_llm_settings()
    return LLMSettingsResponse(
        provider=settings["provider"],
        defaultModel=settings["defaultModel"],
        featureModels=settings["featureModels"],
        configuredModels=configured_openrouter_models_from_profile(),
        openrouterApiKeyConfigured=bool(openrouter_api_key()),
        updatedAt=settings.get("updatedAt"),
    )


@router.get("/admin/llm/settings", response_model=LLMSettingsResponse)
@legacy_router.get("/admin/llm/settings", response_model=LLMSettingsResponse, include_in_schema=False)
def get_llm_settings(
    _=Depends(require_any_role({"admin"})),
) -> LLMSettingsResponse:
    return _build_settings_response()


@router.patch("/admin/llm/settings", response_model=LLMSettingsResponse)
@legacy_router.patch("/admin/llm/settings", response_model=LLMSettingsResponse, include_in_schema=False)
async def update_llm_settings(
    http_request: Request,
    _=Depends(require_any_role({"admin"})),
) -> LLMSettingsResponse:
    request_json = await http_request.json()
    payload = LLMSettingsUpdateRequest.model_validate(request_json.get("payload") or request_json)
    saved = save_llm_settings(
        {
            "provider": payload.provider,
            "defaultModel": payload.defaultModel,
            "featureModels": payload.featureModels,
        }
    )
    return LLMSettingsResponse(
        provider=saved["provider"],
        defaultModel=saved["defaultModel"],
        featureModels=saved["featureModels"],
        configuredModels=configured_openrouter_models_from_profile(),
        openrouterApiKeyConfigured=bool(openrouter_api_key()),
        updatedAt=saved.get("updatedAt"),
    )


@router.get("/admin/llm/openrouter-models", response_model=OpenRouterModelListResponse)
@legacy_router.get("/admin/llm/openrouter-models", response_model=OpenRouterModelListResponse, include_in_schema=False)
def list_openrouter_models(
    _=Depends(require_any_role({"admin"})),
) -> OpenRouterModelListResponse:
    try:
        models = fetch_openrouter_models()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return OpenRouterModelListResponse(items=models)


@router.post("/admin/llm/test", response_model=LLMChatResponse)
@legacy_router.post("/admin/llm/test", response_model=LLMChatResponse, include_in_schema=False)
async def test_llm(
    http_request: Request,
    settings: SymgovAPISettings = Depends(get_settings),
    _=Depends(require_any_role({"admin"})),
) -> LLMChatResponse:
    request_json = await http_request.json()
    payload = LLMChatRequest.model_validate(request_json.get("payload") or request_json)
    selected_model = (payload.model or "").strip() or resolve_model_for_feature(payload.feature)

    try:
        result = await run_in_threadpool(
            request_llm_completion,
            model=selected_model,
            provider="openrouter",
            messages=[
                {"role": "system", "content": "You are Symgov's assistant. Be concise, practical, and safe for engineering governance workflows."},
                {"role": "user", "content": payload.prompt},
            ],
            temperature=payload.temperature,
            max_tokens=payload.maxTokens,
            timeout=45,
            use_case="admin_llm_test",
            service_name="symgov-api",
            feature="admin_llm_test",
            initiator_kind="admin",
            session_factory_provider=lambda: create_session_factory(
                env_file=settings.db_env_file,
                nopool=True,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return LLMChatResponse(**result)


@router.post("/llm/chat", response_model=LLMChatResponse)
@legacy_router.post("/llm/chat", response_model=LLMChatResponse, include_in_schema=False)
async def llm_chat(
    http_request: Request,
    settings: SymgovAPISettings = Depends(get_settings),
    _=Depends(require_user),
) -> LLMChatResponse:
    request_json = await http_request.json()
    payload = LLMChatRequest.model_validate(request_json.get("payload") or request_json)
    selected_model = (payload.model or "").strip() or resolve_model_for_feature(payload.feature)

    try:
        result = await run_in_threadpool(
            request_llm_completion,
            model=selected_model,
            provider="openrouter",
            messages=[
                {"role": "system", "content": "You are Symgov's assistant. Be concise, practical, and safe for engineering governance workflows."},
                {"role": "user", "content": payload.prompt},
            ],
            temperature=payload.temperature,
            max_tokens=payload.maxTokens,
            timeout=45,
            use_case="workspace_chat",
            service_name="symgov-api",
            feature="workspace_chat",
            session_factory_provider=lambda: create_session_factory(
                env_file=settings.db_env_file,
                nopool=True,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return LLMChatResponse(**result)


@router.get("/admin/llm/usage")
@legacy_router.get("/admin/llm/usage", include_in_schema=False)
def get_llm_usage(
    period: str = "day",
    anchor: str | None = None,
    session=Depends(get_db_session),
    _=Depends(require_any_role({"admin"})),
):
    from datetime import datetime, timezone
    from ..services.llm_usage_ledger import calculate_period_utc_bounds, reconcile_invoice_summary

    if period not in ("day", "week", "month", "mtd"):
        raise HTTPException(status_code=422, detail=f"Unsupported period: {period}")

    anchor_dt = None
    if anchor:
        try:
            anchor_dt = datetime.fromisoformat(anchor.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid ISO anchor timestamp")

    start, end = calculate_period_utc_bounds(period, anchor=anchor_dt)

    return {
        "period": period,
        "startUtc": start.isoformat(),
        "endUtc": end.isoformat(),
        "totals": {
            "totalAttempts": 0,
            "totalSuccessful": 0,
            "totalFailed": 0,
            "totalLatencyMs": 0,
            "totalPromptTokens": 0,
            "totalCompletionTokens": 0,
            "totalCostUsd": 0.0,
            "unknownCostAttempts": 0,
        },
        "breakdowns": {
            "byProvider": [],
            "byUseCase": [],
            "byAgent": [],
        },
        "warnings": [],
        "reconciliation": reconcile_invoice_summary(0.0, 0.0),
    }
