"""`POST /api/v1/published/symbols/visual-match`: the in-app twin of the API-key route."""
from __future__ import annotations

import base64
import dataclasses
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from symgov_backend.app import create_app
from symgov_backend.catalog_visual_match import MAX_IMAGE_BYTES, SLOTS
from symgov_backend.dependencies import get_db_session, require_user
from symgov_backend.routes import published as routes
from symgov_backend.settings import get_settings
from symgov_backend.visual_match.errors import VisualMatchInputError, VisualMatchUnavailable

URL = "/api/v1/published/symbols/visual-match"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64


def _b64(data=PNG):
    return base64.b64encode(data).decode()


def _client(monkeypatch, *, enabled=True, runner=None, authenticated=True):
    app = create_app()
    real = get_settings()
    app.dependency_overrides[get_settings] = lambda: dataclasses.replace(real, visual_match_enabled=enabled)
    app.dependency_overrides[get_db_session] = lambda: SimpleNamespace(rollback=lambda: None)
    if authenticated:
        app.dependency_overrides[require_user] = lambda: SimpleNamespace(id="u1")
    calls = []

    def fake(session, settings, **kwargs):
        calls.append(kwargs)
        if runner:
            return runner(**kwargs)
        return {"matcherVersion": "vm1", "groups": [], "degraded": False, "warnings": [],
                "strongerMatchOutsideFilters": False, "symbolsSearched": 1, "notice": "x"}

    monkeypatch.setattr(routes, "run_visual_match", fake)
    return TestClient(app), calls


def test_off_until_enabled_and_needs_a_signed_in_user(monkeypatch):
    client, calls = _client(monkeypatch, enabled=False)
    assert client.post(URL, json={"imageBase64": _b64()}).status_code == 404 and not calls
    client, calls = _client(monkeypatch, authenticated=False)
    assert client.post(URL, json={"imageBase64": _b64()}).status_code in {401, 403} and not calls
    client, calls = _client(monkeypatch)
    assert client.post(URL, json={"imageBase64": _b64()}).status_code == 200 and len(calls) == 1


def test_the_image_filters_and_limit_reach_the_matcher(monkeypatch):
    client, calls = _client(monkeypatch)
    body = {"imageBase64": _b64(), "filters": {"catalogDisciplines": ["Piping"]}, "includeWeak": True, "limit": 99}
    assert client.post(URL, json=body).status_code == 200
    assert calls[0] == {"image": PNG, "filters": {"catalogDisciplines": ["Piping"]}, "include_weak": True, "limit": 25}


@pytest.mark.parametrize("body", [
    {}, {"imageBase64": 5}, {"imageBase64": "not base64!!"},
    {"imageBase64": "AAAA", "includeWeak": "yes"}, {"imageBase64": "AAAA", "limit": 0}, {"imageBase64": "AAAA", "limit": True},
])
def test_malformed_bodies_are_400s_and_never_reach_the_matcher(monkeypatch, body):
    client, calls = _client(monkeypatch)
    assert client.post(URL, json=body).status_code == 400 and not calls


def test_unknown_filters_and_oversized_images_are_refused(monkeypatch):
    client, calls = _client(monkeypatch)
    assert client.post(URL, json={"imageBase64": _b64(), "filters": {"pack": ["x"]}}).status_code == 422
    assert client.post(URL, json={"imageBase64": _b64(b"x" * (MAX_IMAGE_BYTES + 1))}).status_code == 413
    assert not calls


def test_a_json_body_larger_than_the_usual_mutation_bound_is_accepted(monkeypatch):
    client, calls = _client(monkeypatch)
    assert client.post(URL, json={"imageBase64": _b64(PNG + b"x" * 1_000_000)}).status_code == 200 and calls


@pytest.mark.parametrize("error,status", [(VisualMatchInputError("unreadable_image", "The image could not be read."), 400),
                                          (VisualMatchInputError("invalid_filter", "bad"), 422)])
def test_input_errors_map_to_client_errors(monkeypatch, error, status):
    def boom(**kwargs):
        raise error

    client, _ = _client(monkeypatch, runner=boom)
    assert client.post(URL, json={"imageBase64": _b64()}).status_code == status


def test_an_unready_index_is_a_503_with_retry_after_and_a_busy_server_sheds_load(monkeypatch):
    def down(**kwargs):
        raise VisualMatchUnavailable("index_empty", "internal")

    client, _ = _client(monkeypatch, runner=down)
    response = client.post(URL, json={"imageBase64": _b64()})
    assert response.status_code == 503 and response.headers["retry-after"] == "60" and "internal" not in response.text

    client, calls = _client(monkeypatch)
    held = [SLOTS.acquire(blocking=False) for _ in range(4)]
    try:
        busy = client.post(URL, json={"imageBase64": _b64()})
        assert busy.status_code == 503 and busy.headers["retry-after"] == "5" and not calls
    finally:
        for ok in held:
            if ok:
                SLOTS.release()


def test_symbols_are_served_with_the_session_preview_route_not_the_api_key_links(monkeypatch):
    def canned(**kwargs):
        return {"matcherVersion": "vm1", "degraded": False, "warnings": [], "strongerMatchOutsideFilters": False,
                "symbolsSearched": 2, "groups": [{"rank": 1, "band": "likely", "symbols": [
                    {"displayId": "0003-12", "name": "Gate valve", "preview": {"previewUrl": "/api/v1/catalog/x"}, "links": {"api": "/api/v1/catalog/x"}},
                    {"displayId": "0003-13", "name": "No picture", "preview": None, "links": {}}]}]}

    client, _ = _client(monkeypatch, runner=canned)
    symbols = client.post(URL, json={"imageBase64": _b64()}).json()["groups"][0]["symbols"]
    assert symbols[0]["previewUrl"] == "/api/v1/published/symbols/0003-12/preview" and symbols[1]["previewUrl"] is None
    assert all("links" not in s and "preview" not in s for s in symbols)


def test_the_api_key_route_and_the_in_app_route_return_the_same_groups(monkeypatch):
    """Parity: both routes serve one service result; the in-app one only swaps in its own preview links."""
    from test_catalog_routes_auth import api_key_row, auth_headers, build_client
    from symgov_backend.routes import catalog as catalog_routes
    from catalog_key_fakes import switch_off_key_policies

    switch_off_key_policies(monkeypatch)
    canned = {"matcherVersion": "vm1", "degraded": False, "warnings": [], "strongerMatchOutsideFilters": False,
              "symbolsSearched": 2, "groups": [{"rank": 1, "band": "likely", "symbols": [
                  {"displayId": "0003-12", "name": "Gate valve", "symbolId": "s1", "preview": {"x": 1}, "links": {"a": 1}}]}]}
    import copy
    monkeypatch.setattr(catalog_routes, "run_visual_match", lambda *a, **k: copy.deepcopy(canned))
    api, _ = build_client(rows=[api_key_row("valid-token")])
    real = get_settings()
    api.app.dependency_overrides[get_settings] = lambda: dataclasses.replace(real, visual_match_enabled=True)
    via_api = api.post("/api/v1/catalog/visual-match", content=PNG,
                       headers={**auth_headers(), "Content-Type": "image/png"}).json()

    ui, _ = _client(monkeypatch, runner=lambda **k: copy.deepcopy(canned))
    via_ui = ui.post(URL, json={"imageBase64": _b64()}).json()

    def core(body):
        return [(g["rank"], g["band"], [s["displayId"] for s in g["symbols"]]) for g in body["groups"]]

    assert core(via_api) == core(via_ui)
    assert {k: v for k, v in via_api.items() if k != "groups"} == {k: v for k, v in via_ui.items() if k != "groups"}
