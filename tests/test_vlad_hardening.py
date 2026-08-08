from __future__ import annotations

import base64
import importlib
import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
VLAD_RUNNER_PATH = REPO_ROOT / "scripts" / "run_vlad_validation.py"
LEGACY_VLAD_RUNNER_PATH = Path("/data/.openclaw/workspaces/vlad/run_vlad_validation.py")

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.external_workspace
def test_legacy_vlad_runner_code_is_retired():
    assert not LEGACY_VLAD_RUNNER_PATH.exists()


def test_vlad_agent_definition_prefers_gemini_when_api_key_available(monkeypatch):
    monkeypatch.setenv("SYMGOV_GEMINI_API_KEY", "test-key")
    runtime = importlib.import_module("symgov_backend.runtime")
    runtime = importlib.reload(runtime)

    vlad_seed = next(seed for seed in runtime.agent_definition_seeds() if seed["slug"] == "vlad")

    assert vlad_seed["model"].startswith("gemini/")


def test_vlad_runner_metadata_prefers_gemini_when_api_key_available(monkeypatch):
    monkeypatch.setenv("SYMGOV_GEMINI_API_KEY", "test-key")
    vlad_runner = load_module("vlad_runner_hardening", VLAD_RUNNER_PATH)

    assert vlad_runner.resolve_vlad_model().startswith("gemini/")


def test_vlad_runner_metadata_falls_back_to_gemma_when_gemini_unavailable(monkeypatch):
    monkeypatch.delenv("SYMGOV_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("SYMGOV_VLAD_MODEL", raising=False)
    vlad_runner = load_module("vlad_runner_hardening_no_key", VLAD_RUNNER_PATH)

    assert vlad_runner.resolve_vlad_model() == "ollama/gemma4:e4b"


def test_vlad_accepts_profile_gemini_key_alias(monkeypatch):
    monkeypatch.delenv("SYMGOV_GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "profile-key")
    vlad_runner = load_module("vlad_runner_hardening_profile_key", VLAD_RUNNER_PATH)

    assert vlad_runner.get_gemini_api_key() == "profile-key"
    assert vlad_runner.resolve_vlad_model().startswith("gemini/")


def test_vlad_image_edit_uses_litellm_router(tmp_path, monkeypatch):
    vlad_runner = load_module("vlad_runner_litellm_image_edit", VLAD_RUNNER_PATH)
    source = tmp_path / "source.png"
    output = tmp_path / "edited.png"
    source.write_bytes(b"synthetic-source-image")
    generated = b"synthetic-generated-image"
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return {
            "outputImages": [
                {
                    "url": "data:image/png;base64," + base64.b64encode(generated).decode("ascii"),
                    "detail": None,
                }
            ]
        }

    monkeypatch.setattr(vlad_runner, "request_llm_completion", fake_completion)

    result = vlad_runner.edit_image_with_router(source, output, "remove the label")

    assert result["status"] == "edited"
    assert result["provider"] == "google"
    assert output.read_bytes() == generated
    assert captured["provider"] == "google"
    assert captured["use_case"] == "vlad_graphic_edit"
    assert captured["service_name"] == "vlad"
    assert captured["request_kind"] == "image_generation"
    assert captured["modalities"] == ["image", "text"]
    content = captured["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "remove the label"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
