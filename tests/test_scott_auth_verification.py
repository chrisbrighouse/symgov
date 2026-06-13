from __future__ import annotations

import base64
import importlib.util
from pathlib import Path


SCOTT_RUNNER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_scott_intake.py"


spec = importlib.util.spec_from_file_location("scott_runner", SCOTT_RUNNER_PATH)
assert spec is not None and spec.loader is not None
scott_runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scott_runner)


class _DummyResponse:
    def __init__(self, url: str, body: bytes, content_type: str = "text/html; charset=utf-8", status: int = 200):
        self._url = url
        self._body = body
        self.headers = {"content-type": content_type}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def geturl(self):
        return self._url

    def read(self, max_bytes: int):
        return self._body[:max_bytes]


def test_fetch_text_url_uses_basic_auth_from_env(monkeypatch):
    captured = {}
    monkeypatch.setenv("SCOTT_TEST_BASIC", "user:pass")

    def fake_urlopen(request, timeout=15):
        captured["authorization"] = request.headers.get("Authorization")
        return _DummyResponse("https://example.test", b"<html><title>ok</title></html>")

    monkeypatch.setattr(scott_runner.urllib.request, "urlopen", fake_urlopen)

    result = scott_runner.fetch_text_url("https://example.test", auth_secret_key="SCOTT_TEST_BASIC")

    expected = f"Basic {base64.b64encode(b'user:pass').decode('utf-8')}"
    assert captured["authorization"] == expected
    assert result["status_code"] == 200
    assert "ok" in result["text"]


def test_fetch_text_url_uses_api_key_fallback_when_literal_secret(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=15):
        captured["x_api_key"] = request.headers.get("X-api-key")
        return _DummyResponse("https://example.test", b"<html><title>ok</title></html>")

    monkeypatch.setattr(scott_runner.urllib.request, "urlopen", fake_urlopen)

    scott_runner.fetch_text_url("https://example.test", auth_secret_key="LITERAL_SECRET")

    assert captured["x_api_key"] == "LITERAL_SECRET"


def test_detect_auth_wall_ignores_auth_marker_when_final_url_unchanged():
    result = scott_runner.detect_auth_wall(
        "https://example.test/basic-auth/user/pass",
        "https://example.test/basic-auth/user/pass",
        200,
        "<html><title>Authenticated resource</title><body>ok</body></html>",
    )

    assert result["requires_auth"] is False
    assert result["reason"] == "none"


def test_detect_auth_wall_marks_login_redirect():
    result = scott_runner.detect_auth_wall(
        "https://example.test/private",
        "https://example.test/login?next=/private",
        200,
        "<html><title>Sign in</title></html>",
    )

    assert result["requires_auth"] is True
    assert result["reason"] == "auth_redirect"


def test_inspect_candidate_site_marks_auth_verified_when_secret_succeeds(monkeypatch):
    monkeypatch.setattr(
        scott_runner,
        "fetch_text_url",
        lambda *args, **kwargs: {
            "url": "https://example.test",
            "final_url": "https://example.test/private",
            "status_code": 200,
            "content_type": "text/html",
            "text": "<html><title>Private Area</title><body>download symbols</body></html>",
        },
    )
    monkeypatch.setattr(
        scott_runner,
        "detect_auth_wall",
        lambda *args, **kwargs: {
            "requires_auth": False,
            "reason": "none",
            "status_code": 200,
            "keyword_hits": 0,
        },
    )

    site = scott_runner.inspect_candidate_site(
        {"url": "https://example.test/private", "domain": "example.test", "search_title": "Private"},
        "query",
        auth_secret_key="SCOTT_TEST_SECRET",
    )

    assert site["auth_status"] == "auth_verified"
    assert site["requires_auth"] is True
    assert site["auth_secret_key"] == "SCOTT_TEST_SECRET"


def test_inspect_candidate_site_marks_auth_failed_when_still_gated(monkeypatch):
    monkeypatch.setattr(
        scott_runner,
        "fetch_text_url",
        lambda *args, **kwargs: {
            "url": "https://example.test",
            "final_url": "https://example.test/login",
            "status_code": 403,
            "content_type": "text/html",
            "text": "<html><title>Login</title><body>member login required</body></html>",
        },
    )
    monkeypatch.setattr(
        scott_runner,
        "detect_auth_wall",
        lambda *args, **kwargs: {
            "requires_auth": True,
            "reason": "http_403",
            "status_code": 403,
            "keyword_hits": 1,
        },
    )

    site = scott_runner.inspect_candidate_site(
        {"url": "https://example.test/private", "domain": "example.test", "search_title": "Private"},
        "query",
        auth_secret_key="SCOTT_TEST_SECRET",
    )

    assert site["auth_status"] == "auth_failed"
    assert site["requires_auth"] is True
