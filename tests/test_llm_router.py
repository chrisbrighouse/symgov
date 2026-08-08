from __future__ import annotations

import base64
import json

import pytest


class FakeRouter:
    def __init__(self, *, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def build_service(monkeypatch, response):
    from symgov_backend.services.llm_router import LiteLLMRouterService

    created = []
    fake_router = FakeRouter(response=response)

    def factory(**kwargs):
        created.append(kwargs)
        return fake_router

    monkeypatch.setenv("SYMGOV_OPENROUTER_API_KEY", "synthetic-openrouter-key")
    service = LiteLLMRouterService(router_factory=factory)
    return service, fake_router, created


def test_router_maps_openrouter_alias_without_registering_callbacks(monkeypatch):
    service, router, created = build_service(
        monkeypatch,
        {
            "model": "openai/gpt-4o-mini",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        },
    )

    result = service.completion(
        model="openai/gpt-4o-mini",
        provider="openrouter",
        messages=[{"role": "user", "content": "synthetic prompt marker"}],
        use_case="workspace_chat",
        service_name="symgov-api",
    )

    assert created == [
        {
            "model_list": [
                {
                    "model_name": "openai/gpt-4o-mini",
                    "litellm_params": {
                        "model": "openrouter/openai/gpt-4o-mini",
                        "api_key": "synthetic-openrouter-key",
                    },
                }
            ],
            "num_retries": 0,
        }
    ]
    assert "callbacks" not in created[0]
    assert "success_callback" not in created[0]
    assert "failure_callback" not in created[0]
    assert router.calls[0]["model"] == "openai/gpt-4o-mini"
    assert result["provider"] == "openrouter"
    assert result["outputText"] == "ok"
    assert isinstance(result["latencyMs"], int)
    assert result["latencyMs"] >= 0
    assert result["usage"]["prompt_tokens"] == 3


def test_router_supports_vision_and_standardized_image_outputs(monkeypatch):
    image_payload = base64.b64encode(b"synthetic-image").decode("ascii")
    service, router, _ = build_service(
        monkeypatch,
        {
            "model": "gemini-2.5-flash-image",
            "choices": [
                {
                    "message": {
                        "content": '{"name":"Gate valve"}',
                        "images": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{image_payload}"},
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 9, "completion_tokens": 4},
        },
    )
    monkeypatch.setenv("SYMGOV_GEMINI_API_KEY", "synthetic-gemini-key")

    result = service.completion(
        model="gemini-2.5-flash-image",
        provider="google",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "edit this image"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,c291cmNl"}},
                ],
            }
        ],
        use_case="vlad_graphic_edit",
        service_name="vlad",
        agent_slug="vlad",
        request_kind="image_generation",
    )

    assert router.calls[0]["messages"][0]["content"][1]["type"] == "image_url"
    assert result["outputImages"] == [
        {"url": f"data:image/png;base64,{image_payload}", "detail": None}
    ]


def test_native_telemetry_owns_trace_and_excludes_prompt_response_content(monkeypatch):
    from symgov_backend.services.llm_telemetry import validate_event

    service, _, _ = build_service(
        monkeypatch,
        {
            "model": "openai/gpt-4o-mini",
            "choices": [{"message": {"content": "synthetic response marker"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        },
    )
    exported = []
    persisted = []

    monkeypatch.setattr(
        "symgov_backend.services.llm_telemetry.export_llm_event_best_effort",
        lambda event, **kwargs: exported.append((event, kwargs)),
    )
    monkeypatch.setattr(
        "symgov_backend.services.llm_usage_ledger.record_llm_usage_event_best_effort",
        lambda session_factory, event, **kwargs: persisted.append((session_factory, event, kwargs)),
    )
    session_factory = object()

    service.completion(
        model="openai/gpt-4o-mini",
        provider="openrouter",
        messages=[{"role": "user", "content": "synthetic prompt marker"}],
        use_case="admin_llm_test",
        service_name="symgov-api",
        session_factory=session_factory,
    )

    assert len(exported) == 1
    assert len(persisted) == 1
    event = exported[0][0]
    assert persisted[0][1] == event
    assert exported[0][1]["trace_seed"].startswith("request:")
    validate_event(event, trace_seed=exported[0][1]["trace_seed"])
    assert event["provider"] == "openrouter"
    assert event["input_tokens"] == 3
    assert event["output_tokens"] == 2
    serialized = repr(event)
    assert "synthetic prompt marker" not in serialized
    assert "synthetic response marker" not in serialized
    assert "synthetic-openrouter-key" not in serialized
    assert "prompt" not in event
    assert "response" not in event


def test_provider_reported_cost_is_read_from_litellm_hidden_params(monkeypatch):
    class LiteLLMResponse:
        _hidden_params = {"response_cost": 0.00125}

        def model_dump(self):
            return {
                "model": "openai/gpt-4o-mini",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            }

    service, _, _ = build_service(monkeypatch, LiteLLMResponse())
    exported = []
    monkeypatch.setattr(
        "symgov_backend.services.llm_telemetry.export_llm_event_best_effort",
        lambda event, **_: exported.append(event),
    )

    service.completion(
        model="openai/gpt-4o-mini",
        provider="openrouter",
        messages=[{"role": "user", "content": "hello"}],
        use_case="workspace_chat",
        service_name="symgov-api",
    )

    assert exported[0]["cost_basis"] == "provider_reported"
    assert exported[0]["provider_reported_cost_usd"] == "0.00125"


def test_zero_provider_cost_remains_authoritative(monkeypatch):
    class LiteLLMResponse:
        _hidden_params = {"response_cost": 0.5}

        def model_dump(self):
            return {
                "model": "openai/gpt-4o-mini",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"cost": 0, "total_cost": 0.25},
            }

    service, _, _ = build_service(monkeypatch, LiteLLMResponse())
    exported = []
    monkeypatch.setattr(
        "symgov_backend.services.llm_telemetry.export_llm_event_best_effort",
        lambda event, **_: exported.append(event),
    )

    service.completion(
        model="openai/gpt-4o-mini",
        provider="openrouter",
        messages=[{"role": "user", "content": "hello"}],
        use_case="workspace_chat",
        service_name="symgov-api",
    )

    assert exported[0]["cost_basis"] == "provider_reported"
    assert exported[0]["provider_reported_cost_usd"] == "0"


@pytest.mark.parametrize("image_count", [0, 1, 3])
def test_image_input_units_match_normalized_message_images(monkeypatch, image_count):
    service, _, _ = build_service(
        monkeypatch,
        {"model": "gemini/test", "choices": [{"message": {"content": "ok"}}]},
    )
    exported = []
    monkeypatch.setattr(
        "symgov_backend.services.llm_telemetry.export_llm_event_best_effort",
        lambda event, **_: exported.append(event),
    )
    content: list[dict[str, object]] = [{"type": "text", "text": "make an image"}]
    content.extend(
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,image-{index}"}}
        for index in range(image_count)
    )

    service.completion(
        model="gemini-test",
        provider="google",
        messages=[{"role": "user", "content": content}],
        use_case="vlad_graphic_edit",
        service_name="vlad",
        request_kind="image_generation",
    )

    assert exported[0]["image_input_units"] == image_count
    assert exported[0]["image_output_units"] == 0


def test_image_input_count_does_not_depend_on_request_kind(monkeypatch):
    service, _, _ = build_service(
        monkeypatch,
        {"model": "gemini/test", "choices": [{"message": {"content": "ok"}}]},
    )
    exported = []
    monkeypatch.setattr(
        "symgov_backend.services.llm_telemetry.export_llm_event_best_effort",
        lambda event, **_: exported.append(event),
    )

    service.completion(
        model="gemini-test",
        provider="google",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,image"}}
                ],
            }
        ],
        use_case="symbol_property_vision",
        service_name="libby",
        request_kind="text",
    )

    assert exported[0]["image_input_units"] == 1


def test_usage_session_factory_provider_is_lazy_and_best_effort(monkeypatch):
    service, _, _ = build_service(
        monkeypatch,
        {"model": "openai/gpt-4o-mini", "choices": [{"message": {"content": "ok"}}]},
    )

    def unavailable_session_factory():
        raise RuntimeError("synthetic ledger configuration failure")

    result = service.completion(
        model="openai/gpt-4o-mini",
        provider="openrouter",
        messages=[{"role": "user", "content": "hello"}],
        use_case="workspace_chat",
        service_name="symgov-api",
        session_factory_provider=unavailable_session_factory,
    )

    assert result["outputText"] == "ok"


def test_api_latency_excludes_best_effort_telemetry_time(monkeypatch):
    service, _, _ = build_service(
        monkeypatch,
        {"model": "openai/gpt-4o-mini", "choices": [{"message": {"content": "ok"}}]},
    )
    times = iter([10.0, 10.25, 99.0])
    monkeypatch.setattr("symgov_backend.services.llm_router.time.monotonic", lambda: next(times))

    result = service.completion(
        model="openai/gpt-4o-mini",
        provider="openrouter",
        messages=[{"role": "user", "content": "hello"}],
        use_case="workspace_chat",
        service_name="symgov-api",
    )

    assert result["latencyMs"] == 250


def test_router_failure_uses_content_free_telemetry_error(monkeypatch):
    from symgov_backend.services.llm_router import LiteLLMRouterService

    exported = []
    fake_router = FakeRouter(error=RuntimeError("provider leaked response marker"))
    monkeypatch.setenv("SYMGOV_OPENROUTER_API_KEY", "synthetic-openrouter-key")
    monkeypatch.setattr(
        "symgov_backend.services.llm_telemetry.export_llm_event_best_effort",
        lambda event, **_: exported.append(event),
    )
    service = LiteLLMRouterService(router_factory=lambda **_: fake_router)

    with pytest.raises(RuntimeError, match="LiteLLM Router request failed") as exc_info:
        service.completion(
            model="openai/gpt-4o-mini",
            provider="openrouter",
            messages=[{"role": "user", "content": "synthetic prompt marker"}],
            use_case="workspace_chat",
            service_name="symgov-api",
        )

    assert "provider leaked response marker" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exported[0]["status"] == "failed"
    assert exported[0]["error_class"] == "RuntimeError"
    assert exported[0]["error_code"] is None
    assert exported[0]["image_output_units"] is None
    assert "provider leaked response marker" not in repr(exported[0])


def test_configured_model_list_supports_multiple_deployments_and_injects_provider_key(monkeypatch):
    from symgov_backend.services.llm_router import LiteLLMRouterService

    monkeypatch.setenv("SYMGOV_OPENROUTER_API_KEY", "synthetic-openrouter-key")
    monkeypatch.setenv(
        "SYMGOV_LITELLM_MODEL_LIST",
        json.dumps(
            [
                {"model_name": "workspace", "litellm_params": {"model": "openrouter/openai/gpt-4o-mini"}},
                {"model_name": "workspace", "litellm_params": {"model": "openrouter/anthropic/claude-3.5-sonnet"}},
                {"model_name": "other", "litellm_params": {"model": "openrouter/google/gemini-flash-1.5"}},
            ]
        ),
    )
    created = []
    router = FakeRouter(
        response={"model": "openai/gpt-4o-mini", "choices": [{"message": {"content": "ok"}}]}
    )
    service = LiteLLMRouterService(router_factory=lambda **kwargs: created.append(kwargs) or router)

    service.completion(
        model="workspace",
        provider="openrouter",
        messages=[{"role": "user", "content": "hello"}],
        use_case="workspace_chat",
        service_name="symgov-api",
    )

    assert len(created[0]["model_list"]) == 2
    assert {row["litellm_params"]["api_key"] for row in created[0]["model_list"]} == {
        "synthetic-openrouter-key"
    }


@pytest.mark.parametrize(
    ("location", "control", "value"),
    [
        ("params", "num_retries", 2),
        ("params", "fallbacks", ["backup-model"]),
        ("row", "retry_policy", {"RateLimitErrorRetries": 2}),
    ],
)
def test_configured_model_list_rejects_retry_and_fallback_controls(
    monkeypatch,
    location,
    control,
    value,
):
    from symgov_backend.services.llm_router import LiteLLMRouterService

    row = {
        "model_name": "workspace",
        "litellm_params": {"model": "openrouter/openai/gpt-4o-mini"},
    }
    target = row if location == "row" else row["litellm_params"]
    target[control] = value
    monkeypatch.setenv("SYMGOV_OPENROUTER_API_KEY", "synthetic-openrouter-key")
    monkeypatch.setenv("SYMGOV_LITELLM_MODEL_LIST", json.dumps([row]))
    service = LiteLLMRouterService(router_factory=lambda **_: pytest.fail("Router must not be created"))

    with pytest.raises(RuntimeError, match="LiteLLM Router request failed"):
        service.completion(
            model="workspace",
            provider="openrouter",
            messages=[{"role": "user", "content": "hello"}],
            use_case="workspace_chat",
            service_name="symgov-api",
        )


def test_configured_model_list_rejects_litellm_callbacks(monkeypatch):
    from symgov_backend.services.llm_router import LiteLLMRouterService

    monkeypatch.setenv(
        "SYMGOV_LITELLM_MODEL_LIST",
        json.dumps(
            [
                {
                    "model_name": "workspace",
                    "litellm_params": {
                        "model": "openrouter/openai/gpt-4o-mini",
                        "success_callback": ["langfuse"],
                    },
                }
            ]
        ),
    )
    service = LiteLLMRouterService(router_factory=lambda **_: pytest.fail("Router must not be created"))

    with pytest.raises(RuntimeError, match="LiteLLM Router request failed") as exc_info:
        service.completion(
            model="workspace",
            provider="openrouter",
            messages=[{"role": "user", "content": "hello"}],
            use_case="workspace_chat",
            service_name="symgov-api",
        )
    assert exc_info.value.__cause__ is None


def test_configured_model_list_rejects_provider_mismatch(monkeypatch):
    from symgov_backend.services.llm_router import LiteLLMRouterService

    monkeypatch.setenv("SYMGOV_OPENROUTER_API_KEY", "synthetic-openrouter-key")
    monkeypatch.setenv(
        "SYMGOV_LITELLM_MODEL_LIST",
        json.dumps(
            [
                {
                    "model_name": "workspace",
                    "litellm_params": {"model": "openrouter/openai/gpt-4o-mini"},
                },
                {
                    "model_name": "workspace",
                    "litellm_params": {"model": "gemini/gemini-2.5-flash"},
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "symgov_backend.services.llm_router.provider_api_key",
        lambda _provider: pytest.fail("Credentials must not be resolved before every deployment validates"),
    )
    service = LiteLLMRouterService(router_factory=lambda **_: pytest.fail("Router must not be created"))

    with pytest.raises(RuntimeError, match="LiteLLM Router request failed"):
        service.completion(
            model="workspace",
            provider="openrouter",
            messages=[{"role": "user", "content": "hello"}],
            use_case="workspace_chat",
            service_name="symgov-api",
        )


@pytest.mark.parametrize(
    "deployment_model",
    [
        "openrouter/https://models.example/unsafe",
        "openrouter/data:model-payload",
        "openrouter/bearer-token",
    ],
)
def test_configured_model_list_rejects_unsafe_deployment_model_before_credentials(
    monkeypatch,
    deployment_model,
):
    from symgov_backend.services.llm_router import LiteLLMRouterService

    monkeypatch.setenv(
        "SYMGOV_LITELLM_MODEL_LIST",
        json.dumps(
            [
                {
                    "model_name": "workspace",
                    "litellm_params": {"model": deployment_model},
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "symgov_backend.services.llm_router.provider_api_key",
        lambda _provider: pytest.fail("Credentials must not be resolved for an unsafe deployment model"),
    )
    service = LiteLLMRouterService(router_factory=lambda **_: pytest.fail("Router must not be created"))

    with pytest.raises(RuntimeError, match="LiteLLM Router request failed"):
        service.completion(
            model="workspace",
            provider="openrouter",
            messages=[{"role": "user", "content": "hello"}],
            use_case="workspace_chat",
            service_name="symgov-api",
        )


def test_scientific_notation_provider_cost_is_normalized_for_telemetry(monkeypatch):
    from symgov_backend.services.llm_telemetry import validate_event

    service, _, _ = build_service(
        monkeypatch,
        {
            "model": "openai/gpt-4o-mini",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"cost": 1e-6},
        },
    )
    exported = []
    monkeypatch.setattr(
        "symgov_backend.services.llm_telemetry.export_llm_event_best_effort",
        lambda event, **kwargs: exported.append((event, kwargs)),
    )

    service.completion(
        model="openai/gpt-4o-mini",
        provider="openrouter",
        messages=[{"role": "user", "content": "hello"}],
        use_case="workspace_chat",
        service_name="symgov-api",
    )

    event, export_kwargs = exported[0]
    assert event["provider_reported_cost_usd"] == "0.000001"
    validate_event(event, trace_seed=export_kwargs["trace_seed"])


@pytest.mark.parametrize(
    "provider_cost",
    [-0.01, float("nan"), float("inf"), "1000000.01", "0.0000000001", "not-a-cost"],
)
def test_invalid_provider_cost_becomes_schema_valid_unknown_cost(monkeypatch, provider_cost):
    from symgov_backend.services.llm_telemetry import validate_event

    service, _, _ = build_service(
        monkeypatch,
        {
            "model": "openai/gpt-4o-mini",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"cost": provider_cost},
        },
    )
    exported = []
    monkeypatch.setattr(
        "symgov_backend.services.llm_telemetry.export_llm_event_best_effort",
        lambda event, **kwargs: exported.append((event, kwargs)),
    )

    service.completion(
        model="openai/gpt-4o-mini",
        provider="openrouter",
        messages=[{"role": "user", "content": "hello"}],
        use_case="workspace_chat",
        service_name="symgov-api",
    )

    event, export_kwargs = exported[0]
    assert event["cost_basis"] == "unknown"
    assert event["provider_reported_cost_usd"] is None
    validate_event(event, trace_seed=export_kwargs["trace_seed"])


@pytest.mark.parametrize(
    "provider_model",
    ["https://models.example/unsafe", "data:model-payload", "provider/bearer-token"],
)
def test_unsafe_provider_resolved_model_falls_back_before_result_and_telemetry(
    monkeypatch,
    provider_model,
):
    from symgov_backend.services.llm_telemetry import validate_event

    requested_model = "openai/gpt-4o-mini"
    service, _, _ = build_service(
        monkeypatch,
        {
            "model": provider_model,
            "choices": [{"message": {"content": "ok"}}],
        },
    )
    exported = []
    monkeypatch.setattr(
        "symgov_backend.services.llm_telemetry.export_llm_event_best_effort",
        lambda event, **kwargs: exported.append((event, kwargs)),
    )

    result = service.completion(
        model=requested_model,
        provider="openrouter",
        messages=[{"role": "user", "content": "hello"}],
        use_case="workspace_chat",
        service_name="symgov-api",
    )

    event, export_kwargs = exported[0]
    assert result["resolvedModel"] == requested_model
    assert event["resolved_model"] == requested_model
    assert event["metadata"]["model"] == requested_model
    validate_event(event, trace_seed=export_kwargs["trace_seed"])


@pytest.mark.parametrize(
    "model",
    [
        "invalid model with spaces",
        "openai/https://models.example/unsafe",
        "data:model-payload",
        "openai/bearer-token",
    ],
)
def test_model_identifier_is_bounded_before_router_creation(monkeypatch, model):
    from symgov_backend.services.llm_router import LiteLLMRouterService

    created = []
    service = LiteLLMRouterService(router_factory=lambda **kwargs: created.append(kwargs))

    with pytest.raises(ValueError, match="model"):
        service.completion(
            model=model,
            provider="openrouter",
            messages=[{"role": "user", "content": "hello"}],
            use_case="workspace_chat",
            service_name="symgov-api",
        )

    assert created == []


def test_router_cache_evicts_old_models_when_capacity_is_reached(monkeypatch):
    from symgov_backend.services.llm_router import LiteLLMRouterService

    created = []

    def factory(**kwargs):
        created.append(kwargs)
        return FakeRouter(
            response={"model": "openai/test", "choices": [{"message": {"content": "ok"}}]}
        )

    monkeypatch.setenv("SYMGOV_OPENROUTER_API_KEY", "synthetic-openrouter-key")
    service = LiteLLMRouterService(router_factory=factory)
    request = {
        "provider": "openrouter",
        "messages": [{"role": "user", "content": "hello"}],
        "use_case": "workspace_chat",
        "service_name": "symgov-api",
    }

    for index in range(33):
        service.completion(model=f"openai/model-{index}", **request)
    service.completion(model="openai/model-0", **request)

    assert len(created) == 34


def test_real_litellm_router_sdk_smoke_uses_mock_response(monkeypatch):
    pytest.importorskip("litellm")
    from symgov_backend.services.llm_router import LiteLLMRouterService

    monkeypatch.setenv(
        "SYMGOV_LITELLM_MODEL_LIST",
        json.dumps(
            [
                {
                    "model_name": "sdk-smoke",
                    "litellm_params": {
                        "model": "openrouter/openai/gpt-4o-mini",
                        "api_key": "synthetic-sdk-smoke-key",
                        "mock_response": "LiteLLM Router SDK smoke passed",
                    },
                }
            ]
        ),
    )

    result = LiteLLMRouterService().completion(
        model="sdk-smoke",
        provider="openrouter",
        messages=[{"role": "user", "content": "offline smoke"}],
        use_case="workspace_chat",
        service_name="symgov-api",
    )

    assert result["outputText"] == "LiteLLM Router SDK smoke passed"
