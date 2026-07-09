from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..dependencies import require_any_role, require_user
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
    request_openrouter_completion,
    resolve_model_for_feature,
    save_llm_settings,
)


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
    _=Depends(require_any_role({"admin"})),
) -> LLMChatResponse:
    request_json = await http_request.json()
    payload = LLMChatRequest.model_validate(request_json.get("payload") or request_json)
    selected_model = payload.model.strip() if payload.model else resolve_model_for_feature(payload.feature)

    try:
        result = request_openrouter_completion(
            prompt=payload.prompt,
            model=selected_model,
            temperature=payload.temperature,
            max_tokens=payload.maxTokens,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return LLMChatResponse(**result)


@router.post("/llm/chat", response_model=LLMChatResponse)
@legacy_router.post("/llm/chat", response_model=LLMChatResponse, include_in_schema=False)
async def llm_chat(
    http_request: Request,
    _=Depends(require_user),
) -> LLMChatResponse:
    request_json = await http_request.json()
    payload = LLMChatRequest.model_validate(request_json.get("payload") or request_json)
    selected_model = payload.model.strip() if payload.model else resolve_model_for_feature(payload.feature)

    try:
        result = request_openrouter_completion(
            prompt=payload.prompt,
            model=selected_model,
            temperature=payload.temperature,
            max_tokens=payload.maxTokens,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return LLMChatResponse(**result)
