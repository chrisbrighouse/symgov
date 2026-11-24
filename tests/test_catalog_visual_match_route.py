"""`POST /api/v1/catalog/visual-match`: auth, switch, body handling, error mapping, usage, and the response shape."""
from __future__ import annotations

import dataclasses

import pytest

from catalog_key_fakes import switch_off_key_policies
from symgov_backend import catalog_visual_match as service
from symgov_backend.models import CatalogApiUsageEvent
from symgov_backend.routes import catalog as routes
from symgov_backend.settings import get_settings
from symgov_backend.visual_match.errors import VisualMatchInputError, VisualMatchUnavailable
from test_catalog_routes_auth import api_key_row, auth_headers, build_client

URL = "/api/v1/catalog/visual-match"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    switch_off_key_policies(monkeypatch)


def _client(monkeypatch, *, enabled=True, runner=None):
    client, session = build_client(rows=[api_key_row("valid-token")])
    real = get_settings()
    client.app.dependency_overrides[get_settings] = lambda: dataclasses.replace(real, visual_match_enabled=enabled)
    calls = []

    def fake(session_, settings, **kwargs):
        calls.append(kwargs)
        if runner:
            return runner(**kwargs)
        return {"matcherVersion": "vm1", "groups": [{"rank": 1, "band": "possible", "symbols": []}], "degraded": False,
                "warnings": [], "strongerMatchOutsideFilters": False, "symbolsSearched": 3}

    monkeypatch.setattr(routes, "run_visual_match", fake)
    return client, session, calls


def _post(client, body=PNG, content_type="image/png", params=None, headers=None):
    return client.post(URL, content=body, params=params, headers={**auth_headers(), "Content-Type": content_type, **(headers or {})})


def test_the_route_is_off_until_enabled_and_needs_a_catalog_read_key(monkeypatch):
    client, _, calls = _client(monkeypatch, enabled=False)
    assert _post(client).status_code == 404 and not calls
    client, _, calls = _client(monkeypatch)
    assert client.post(URL, content=PNG, headers={"Content-Type": "image/png"}).status_code == 401
    assert _post(client).status_code == 200 and len(calls) == 1


def test_only_png_and_jpeg_bodies_are_accepted_and_the_size_is_capped(monkeypatch):
    client, _, calls = _client(monkeypatch)
    assert _post(client, content_type="application/json").status_code == 415
    assert _post(client, content_type="image/gif").status_code == 415
    assert _post(client, body=b"x" * (service.MAX_IMAGE_BYTES + 1)).status_code == 413
    assert not calls


def test_filters_and_limits_reach_the_matcher_and_the_limit_is_capped(monkeypatch):
    client, _, calls = _client(monkeypatch)
    response = _post(client, params=[("discipline", "Piping"), ("discipline", "Fire"), ("useCase", "P&ID"),
                                     ("includeWeak", "true"), ("limit", "999")])
    assert response.status_code == 200
    assert calls[0]["filters"] == {"catalogDisciplines": ["Piping", "Fire"], "catalogCategories": [],
                                   "useCases": ["P&ID"], "availableFormats": []}
    assert calls[0]["include_weak"] is True and calls[0]["limit"] == 25 and calls[0]["image"] == PNG


@pytest.mark.parametrize("code,status", [("image_too_large", 413), ("unsupported_format", 415), ("invalid_filter", 422),
                                         ("unreadable_image", 400), ("no_line_work", 400)])
def test_input_errors_map_to_client_errors_with_the_message_only(monkeypatch, code, status):
    def boom(**kwargs):
        raise VisualMatchInputError(code, "The image could not be used.")

    client, _, _ = _client(monkeypatch, runner=boom)
    response = _post(client)
    assert response.status_code == status and "could not be used" in response.text


def test_an_index_that_cannot_answer_is_a_503_with_retry_after_never_an_empty_result(monkeypatch):
    def down(**kwargs):
        raise VisualMatchUnavailable("index_incomplete", "internal detail")

    client, _, _ = _client(monkeypatch, runner=down)
    response = _post(client)
    assert response.status_code == 503 and response.headers["retry-after"] == "60"
    assert "internal detail" not in response.text


def test_usage_records_the_call_and_the_count_but_never_the_image(monkeypatch):
    client, session, _ = _client(monkeypatch)
    assert _post(client).status_code == 200
    events = [row for row in session.added if isinstance(row, CatalogApiUsageEvent)]
    assert len(events) == 1 and events[0].route_name == "catalog_visual_match" and events[0].result_count == 1
    assert "PNG" not in repr(events[0].__dict__) and events[0].query_text in (None, "")


def test_busy_server_sheds_load_with_a_503(monkeypatch):
    client, _, calls = _client(monkeypatch)
    held = [routes._VISUAL_MATCH_SLOTS.acquire(blocking=False) for _ in range(4)]
    try:
        response = _post(client)
        assert response.status_code == 503 and response.headers["retry-after"] == "5" and not calls
    finally:
        for ok in held:
            if ok:
                routes._VISUAL_MATCH_SLOTS.release()


def test_a_large_image_passes_the_body_bound_that_applies_to_json(monkeypatch):
    client, _, calls = _client(monkeypatch)
    assert _post(client, body=PNG + b"x" * 2_000_000).status_code == 200 and len(calls) == 1


def test_the_developer_documents_describe_the_route():
    from symgov_backend.catalog_developer import catalog_openapi_document, developer_manifest

    operation = catalog_openapi_document()["paths"][URL]["post"]
    assert set(operation["requestBody"]["content"]) == {"image/png", "image/jpeg"}
    assert {"413", "415", "422", "503"} <= set(operation["responses"])
    assert {p["name"] for p in operation["parameters"]} >= {"discipline", "category", "useCase", "format", "includeWeak"}
    assert any(e["path"] == URL for e in developer_manifest()["endpoints"])
    assert any(g["id"] == "visual-match" for g in developer_manifest()["guides"])
