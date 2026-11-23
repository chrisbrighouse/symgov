"""The image embedding call: routing, provider allow-list, validation, no detail leaked, telemetry."""
from __future__ import annotations

import io
import json
import math

import httpx2
import pytest
from PIL import Image

from symgov_backend.services import llm_image_embeddings as module
from symgov_backend.services.llm_embeddings import EmbeddingError
from symgov_backend.services.llm_telemetry import validate_event


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(module, "provider_api_key", lambda provider: "test-key-not-real")


@pytest.fixture
def events(monkeypatch):
    captured = []
    monkeypatch.setattr("symgov_backend.services.llm_telemetry.export_llm_event_best_effort",
                        lambda event, trace_seed=None: captured.append((dict(event), trace_seed)) or True)
    return captured


def png() -> bytes:
    b = io.BytesIO(); Image.new("L", (32, 32), 255).save(b, format="PNG"); return b.getvalue()


def transport(body, status=200, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        return httpx2.Response(status, json=body)
    return httpx2.MockTransport(handler)


def ok(n=3072, provider="Google Vertex", **extra):
    return {"data": [{"index": 0, "embedding": [float(i % 7 + 1) for i in range(n)]}], "model": "google/gemini-embedding-2",
            "provider": provider, "usage": {"prompt_tokens": 258, "cost": 0.0001}, **extra}


def test_request_carries_zdr_routing_and_one_image():
    seen = []
    module.request_image_embedding(png=png(), transport=transport(ok(provider="google-vertex"), seen=seen))
    body = json.loads(seen[0].content)
    assert body["provider"] == {"zdr": True, "data_collection": "deny", "only": ["google-vertex"], "allow_fallbacks": False}
    assert body["model"] == "google/gemini-embedding-2" and len(body["input"]) == 1
    assert body["input"][0]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_vector_is_a_renormalised_prefix():
    r = module.request_image_embedding(png=png(), dimensions=768, transport=transport(ok(provider="google-vertex")))
    assert len(r["vector"]) == 768 and abs(math.sqrt(sum(v * v for v in r["vector"])) - 1) < 1e-9


def test_unapproved_provider_is_a_failure():
    with pytest.raises(EmbeddingError):
        module.request_image_embedding(png=png(), transport=transport(ok(provider="google-ai-studio")))
    with pytest.raises(EmbeddingError):
        module.request_image_embedding(png=png(), transport=transport(ok(provider="")))


def test_provider_error_detail_never_leaks():
    with pytest.raises(EmbeddingError) as e:
        module.request_image_embedding(png=png(), transport=transport({"error": "secret-detail"}, status=502))
    assert "secret-detail" not in str(e.value) and e.value.__cause__ is None


def test_short_or_bad_vectors_fail():
    with pytest.raises(EmbeddingError):
        module.request_image_embedding(png=png(), dimensions=768, transport=transport(ok(n=100, provider="google-vertex")))
    bad = ok(provider="google-vertex"); bad["data"][0]["embedding"] = [0.0] * 3072
    with pytest.raises(EmbeddingError):
        module.request_image_embedding(png=png(), transport=transport(bad))


@pytest.mark.parametrize("kwargs", [{"png": b"not a png"}, {"png": b""}, {"dimensions": 0}, {"providers": ()}, {"model": "bad model!"}])
def test_invalid_inputs_are_rejected_before_any_request(kwargs):
    args = {"png": png(), "transport": transport(ok())} | kwargs
    with pytest.raises(ValueError):
        module.request_image_embedding(**args)


def test_telemetry_event_is_valid_and_has_no_image(events):
    module.request_image_embedding(png=png(), transport=transport(ok(provider="google-vertex")))
    assert len(events) == 1
    event, seed = events[0]
    validate_event(event, trace_seed=seed)
    assert event["feature"] == "visual_match" and "base64" not in json.dumps(event)


def test_no_key_is_an_error(monkeypatch):
    monkeypatch.setattr(module, "provider_api_key", lambda provider: "")
    with pytest.raises(EmbeddingError):
        module.request_image_embedding(png=png(), transport=transport(ok()))
