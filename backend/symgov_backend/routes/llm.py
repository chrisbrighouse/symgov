from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..dependencies import get_db_session, require_any_role, require_user
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
from ..settings import runtime_environment


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


def _utc_text(value):
    return value.isoformat().replace("+00:00", "Z")


def _reconcile_usage(ledger, langfuse):
    if ledger.get("status") != "available" or langfuse.get("status") != "available":
        return {"status": "unavailable"}
    ledger_totals = ledger["totals"]
    langfuse_totals = langfuse["totals"]
    ledger_tokens = ledger_totals.get("inputTokens")
    output_tokens = ledger_totals.get("outputTokens")
    langfuse_tokens = langfuse_totals.get("totalTokens") if langfuse_totals else None
    ledger_cost = ledger_totals.get("effectiveCostUsd")
    langfuse_cost = langfuse_totals.get("totalCostUsd") if langfuse_totals else None
    differences = {}
    tokens_known = not (
        ledger_totals.get("unknownInputTokenAttempts", 0)
        or ledger_totals.get("unknownOutputTokenAttempts", 0)
    )
    if tokens_known and ledger_tokens is not None and output_tokens is not None and langfuse_tokens is not None:
        differences["tokenDifference"] = abs(ledger_tokens + output_tokens - langfuse_tokens)
    if not ledger_totals.get("unknownCostAttempts", 0) and ledger_cost is not None and langfuse_cost is not None:
        differences["costDifferenceUsd"] = round(abs(ledger_cost - langfuse_cost), 9)
    if not differences:
        return {"status": "notComparable"}
    return {"status": "matched" if all(value == 0 for value in differences.values()) else "different", **differences}


@router.get("/admin/llm/usage")
@legacy_router.get("/admin/llm/usage", include_in_schema=False)
def get_llm_usage(
    response: Response,
    period: str = "day",
    anchor: str | None = None,
    session=Depends(get_db_session),
    _=Depends(require_any_role({"admin"})),
):
    from datetime import datetime
    from ..services import langfuse_reporting, llm_usage_ledger

    if period not in ("day", "week", "month", "mtd"):
        raise HTTPException(status_code=422, detail=f"Unsupported period: {period}")
    anchor_dt = None
    if anchor is not None:
        if not 1 <= len(anchor) <= 64:
            raise HTTPException(status_code=422, detail="Invalid ISO anchor timestamp")
        try:
            anchor_dt = datetime.fromisoformat(anchor.removesuffix("Z") + ("+00:00" if anchor.endswith("Z") else ""))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Invalid ISO anchor timestamp") from None
        if not 2000 <= anchor_dt.year <= 2100:
            raise HTTPException(status_code=422, detail="Anchor timestamp is outside the supported range")

    response.headers["Cache-Control"] = "no-store, private"
    try:
        environment = runtime_environment()
    except RuntimeError:
        raise HTTPException(
            status_code=503,
            detail="The server runtime environment is not safely configured.",
        ) from None

    start, end = llm_usage_ledger.calculate_period_utc_bounds(period, anchor=anchor_dt)
    warnings = []
    try:
        ledger_report = llm_usage_ledger.aggregate_llm_usage(
            session,
            start,
            end,
            environment=environment,
        )
        ledger = {"status": "available", **ledger_report}
        warnings.extend(ledger_report.get("warnings", []))
    except Exception:
        message = "The authoritative Symgov ledger is temporarily unavailable."
        ledger = {
            "status": "unavailable",
            "message": message,
            "totals": None,
            "breakdowns": None,
        }
        warnings.append(message)
    langfuse, langfuse_warnings = langfuse_reporting.safe_langfuse_usage(
        langfuse_reporting.LangfuseQueryConfig.from_env(), start, end
    )
    warnings.extend(langfuse_warnings)
    return {
        "period": period,
        "startUtc": _utc_text(start),
        "endUtcExclusive": _utc_text(end),
        "ledger": ledger,
        "langfuse": langfuse,
        "reconciliation": _reconcile_usage(ledger, langfuse),
        "warnings": warnings[:20],
    }
