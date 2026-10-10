"""The embedding call: validation, normalisation, no provider detail, telemetry."""

from __future__ import annotations

import json
import math

import httpx2
import pytest

from symgov_backend.services import llm_embeddings as module
from symgov_backend.services.llm_telemetry import validate_event


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(module, "provider_api_key", lambda provider: "test-key-not-real")


@pytest.fixture
def events(monkeypatch):
    captured: list[tuple[dict, str]] = []
    monkeypatch.setattr(
        "symgov_backend.services.llm_telemetry.export_llm_event_best_effort",
        lambda event, trace_seed=None: captured.append((dict(event), trace_seed)) or True,
    )
    return captured


def _transport(body, status=200, seen=None):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(request)
        return httpx2.Response(status, json=body)

    return httpx2.MockTransport(handler)


def _ok(vectors, **extra):
    return {
        "data": [{"index": index, "embedding": vector} for index, vector in enumerate(vectors)],
        "model": "baai/bge-m3",
        "usage": {"prompt_tokens": 7, "total_tokens": 7, "cost": 0.0000001},
        **extra,
    }


def _call(transport, texts=("gate valve",), **kwargs):
    return module.request_llm_embeddings(texts=list(texts), model="baai/bge-m3", transport=transport, **kwargs)


def test_vectors_come_back_unit_length_and_in_request_order():
    # The provider may answer out of order; `index` says which text each is.
    body = _ok([[3.0, 4.0], [0.0, 2.0]])
    body["data"].reverse()

    result = _call(_transport(body), texts=["a", "b"])

    assert result["dimensions"] == 2
    assert result["vectors"][0] == pytest.approx([0.6, 0.8])
    assert result["vectors"][1] == pytest.approx([0.0, 1.0])
    assert all(math.isclose(sum(v * v for v in vector), 1.0) for vector in result["vectors"])
    assert result["resolvedModel"] == "baai/bge-m3" and result["usage"]["prompt_tokens"] == 7


def test_the_request_is_one_fixed_authenticated_call():
    seen: list[httpx2.Request] = []

    _call(_transport(_ok([[1.0, 0.0]]), seen=seen))

    [request] = seen
    assert str(request.url) == module.EMBEDDINGS_URL
    assert request.headers["authorization"] == "Bearer test-key-not-real"
    assert json.loads(request.content) == {"model": "baai/bge-m3", "input": ["gate valve"], "encoding_format": "float"}


@pytest.mark.parametrize(
    "body",
    [
        {"data": []},
        {"data": [{"index": 0, "embedding": []}]},
        {"data": [{"index": 0, "embedding": [0.0, 0.0]}]},
        {"data": [{"index": 0, "embedding": ["x", 1.0]}]},
        {"data": [{"index": 0, "embedding": [float("nan"), 1.0]}]},
        {"data": [{"index": 0, "embedding": [True, 1.0]}]},
        "not an object",
    ],
)
def test_an_unusable_response_is_an_error_without_provider_detail(body):
    with pytest.raises(module.EmbeddingError) as error:
        _call(_transport(body))

    assert str(error.value) == "The embedding request failed."


def test_vectors_of_different_sizes_are_refused():
    with pytest.raises(module.EmbeddingError):
        _call(_transport(_ok([[1.0, 0.0], [1.0, 0.0, 0.0]])), texts=["a", "b"])


def test_a_provider_refusal_is_an_error_that_leaks_nothing():
    with pytest.raises(module.EmbeddingError) as error:
        _call(_transport({"error": {"message": "secret provider detail"}}, status=401))

    assert "secret" not in str(error.value) and "401" not in str(error.value)


@pytest.mark.parametrize(
    "arguments",
    [
        {"texts": []},
        {"texts": ["x"] * 65},
        {"texts": [""]},
        {"texts": ["   "]},
        {"texts": ["x" * 8001]},
        {"texts": ["ok"], "model": "../etc"},
    ],
)
def test_bad_input_is_refused_before_any_call(arguments):
    seen: list[httpx2.Request] = []
    arguments = {"model": "baai/bge-m3", **arguments}

    with pytest.raises(ValueError):
        module.request_llm_embeddings(transport=_transport(_ok([[1.0]]), seen=seen), **arguments)

    assert seen == []


def test_without_an_api_key_nothing_is_sent(monkeypatch):
    monkeypatch.setattr(module, "provider_api_key", lambda provider: "")
    seen: list[httpx2.Request] = []

    with pytest.raises(module.EmbeddingError):
        _call(_transport(_ok([[1.0]]), seen=seen))

    assert seen == []


def test_a_call_records_one_valid_usage_event_with_no_input_text(events):
    _call(_transport(_ok([[1.0, 0.0]])), texts=["a very distinctive private-looking phrase"], feature="ed_catalog_search")

    [(event, seed)] = events
    validate_event(event, trace_seed=seed)
    assert event["use_case"] == "catalog_embedding" and event["request_kind"] == "embedding"
    assert event["status"] == "succeeded" and event["input_tokens"] == 7
    assert event["cost_basis"] == "provider_reported" and event["feature"] == "ed_catalog_search"
    assert "distinctive" not in json.dumps(event)


def test_a_failed_call_is_recorded_as_failed(events):
    with pytest.raises(module.EmbeddingError):
        _call(_transport({"error": "x"}, status=500))

    [(event, seed)] = events
    validate_event(event, trace_seed=seed)
    assert event["status"] == "failed" and event["error_class"] == "EmbeddingError"
    assert event["error_code"] == "http_500"


def test_a_ledger_session_provider_receives_the_event(monkeypatch, events):
    recorded = []
    monkeypatch.setattr(
        "symgov_backend.services.llm_usage_ledger.record_llm_usage_event_best_effort",
        lambda factory, event, trace_seed=None: recorded.append((factory, event["use_case"])),
    )

    _call(_transport(_ok([[1.0, 0.0]])), session_factory_provider=lambda: "factory")

    assert recorded == [("factory", "catalog_embedding")]
