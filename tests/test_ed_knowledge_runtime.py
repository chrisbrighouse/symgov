"""Slice D: Ed serves approved knowledge only from a steward-signed bundle.

Each test builds a real bundle and signs a real receipt with a throwaway
SSH key, so the path under test is the one production uses.
"""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from symgov_backend.settings import SymgovAPISettings


# Contract review 14: only the signing tests need ssh-keygen; the committed
# bundle check below must never be skipped.
requires_ssh_keygen = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen is required")
NAMESPACE = "symgov-ed-knowledge"


def _runtime():
    return importlib.import_module("symgov_backend.ed_knowledge_runtime")


def _cli_helpers():
    return importlib.import_module("test_ed_corpus_cli")


def _signed_release(tmp_path: Path, *, sign: bool = True, signer_listed: bool = True):
    """A release tree holding one bundle under the decision 7.4 layout."""
    cli = importlib.import_module("symgov_backend.ed_corpus_cli")
    helpers = _cli_helpers()
    repository = tmp_path / "release"
    staging = tmp_path / "staging"
    repository.mkdir()
    staging.mkdir()
    built = helpers._build(repository, staging)
    index = json.loads((built / "manifest.json").read_bytes())["indexDigest"]
    name = index.removeprefix("sha256:")[:12]
    home = repository / "backend/symgov_backend/data/ed_knowledge/bundles" / name
    (home / "bundle").mkdir(parents=True)
    for item in built.iterdir():
        shutil.copyfile(item, home / "bundle" / item.name)
    cli.prepare_receipt(
        bundle=home / "bundle",
        repository=repository,
        steward="Ed Knowledge Steward",
        approved_at="2026-09-29T20:00:00Z",
        output=home / "approval.json",
    )
    key, public = helpers._steward_key(tmp_path / "steward-machine")
    signers = helpers._allowed_signers(
        tmp_path / "server-config" / "allowed_signers",
        ("steward@example.invalid", public if signer_listed else helpers._steward_key(tmp_path / "other", "other")[1]),
    )
    if sign:
        subprocess.run(
            ["ssh-keygen", "-q", "-Y", "sign", "-f", str(key), "-n", NAMESPACE, str(home / "approval.json")],
            check=True,
            capture_output=True,
        )
    settings = SymgovAPISettings(ed_knowledge_bundle=name, ed_allowed_signers=str(signers))
    return repository, settings, home, index


@pytest.fixture(autouse=True)
def _fresh_cache():
    _runtime().clear_approved_knowledge_cache()
    yield
    _runtime().clear_approved_knowledge_cache()


@requires_ssh_keygen
def test_nothing_is_loaded_unless_both_settings_are_present(tmp_path: Path, monkeypatch):
    runtime = _runtime()
    monkeypatch.delenv("SYMGOV_ED_KNOWLEDGE_BUNDLE", raising=False)
    monkeypatch.delenv("SYMGOV_ED_ALLOWED_SIGNERS", raising=False)

    assert SymgovAPISettings().ed_knowledge_bundle == ""
    assert runtime.load_approved_knowledge(SymgovAPISettings(), repository_root=tmp_path) is None
    assert runtime.load_approved_knowledge(
        SymgovAPISettings(ed_knowledge_bundle="198fca6e359e"), repository_root=tmp_path
    ) is None


@requires_ssh_keygen
def test_a_steward_signed_bundle_loads_and_answers(tmp_path: Path):
    runtime = _runtime()
    repository, settings, _home, index = _signed_release(tmp_path)

    knowledge = runtime.load_approved_knowledge(settings, repository_root=repository)

    assert knowledge is not None
    assert knowledge.index_digest == index
    assert knowledge.principal == "steward@example.invalid"
    result = runtime.retrieve_approved(knowledge, "What is the first phase?")
    assert result.status == "answered"
    assert result.items[0].citation.reference == f"knowledge:{knowledge.version}:claim:guide-read-only:v1"


@requires_ssh_keygen
@pytest.mark.parametrize("problem", ["unsigned", "unlisted", "tampered", "bad_name"])
def test_an_unapproved_bundle_is_never_served(tmp_path: Path, problem: str):
    runtime = _runtime()
    repository, settings, home, _index = _signed_release(
        tmp_path,
        sign=problem != "unsigned",
        signer_listed=problem != "unlisted",
    )
    if problem == "tampered":
        chunks = home / "bundle" / "chunks.jsonl"
        chunks.write_bytes(chunks.read_bytes().replace(b"read-only", b"read-write"))
    if problem == "bad_name":
        settings = SymgovAPISettings(ed_knowledge_bundle="../../etc", ed_allowed_signers=settings.ed_allowed_signers)

    assert runtime.load_approved_knowledge(settings, repository_root=repository) is None


@requires_ssh_keygen
def test_a_source_changed_after_approval_stops_answers(tmp_path: Path):
    """The signature still holds, but the cited bytes moved on: fail closed."""
    runtime = _runtime()
    repository, settings, _home, _index = _signed_release(tmp_path)
    knowledge = runtime.load_approved_knowledge(settings, repository_root=repository)
    (repository / "docs/guide.md").write_text("# Guide\nEd is read-write.\n", encoding="utf-8")

    assert runtime.retrieve_approved(knowledge, "What is the first phase?").status == "unavailable"


def test_the_committed_initial_bundle_matches_its_unsigned_receipt():
    """The repository's own bundle still verifies against the current sources.

    This fails whenever a cited source file, the tokenizer or the bundle
    changes. That is the point: the steward's approval no longer covers the
    bytes, so the bundle must be rebuilt and approved again.
    """
    cli = importlib.import_module("symgov_backend.ed_corpus_cli")
    root = Path(__file__).resolve().parents[1]
    [home] = sorted((root / "backend/symgov_backend/data/ed_knowledge/bundles").iterdir())
    assert home.name == json.loads((home / "approval.json").read_bytes())["indexDigest"].removeprefix("sha256:")[:12]

    result = cli.verify_bundle(bundle=home / "bundle", repository=root)
    receipt = json.loads((home / "approval.json").read_bytes())

    assert receipt["indexDigest"] == result.index_digest
    assert receipt["manifestDigest"] == result.manifest_digest


@requires_ssh_keygen
def test_a_load_in_progress_never_blocks_another_request(tmp_path: Path):
    """Security review L4: a load held the global lock while ssh-keygen ran."""
    runtime = _runtime()
    repository, settings, _home, _index = _signed_release(tmp_path)
    key = (settings.ed_knowledge_bundle, settings.ed_allowed_signers, str(repository.resolve()))
    runtime._loading.add(key)
    try:
        assert runtime.load_approved_knowledge(settings, repository_root=repository) is None
    finally:
        runtime._loading.discard(key)

    assert runtime.load_approved_knowledge(settings, repository_root=repository) is not None


@requires_ssh_keygen
def test_a_loaded_bundle_is_verified_again_after_its_ttl(tmp_path: Path, monkeypatch):
    """Contract review 10: removing a steward key used to need a restart."""
    runtime = _runtime()
    repository, settings, _home, _index = _signed_release(tmp_path)
    assert runtime.load_approved_knowledge(settings, repository_root=repository) is not None
    Path(settings.ed_allowed_signers).write_text("")
    monkeypatch.setattr(runtime, "_SUCCESS_REVERIFY_SECONDS", 0.0)

    assert runtime.load_approved_knowledge(settings, repository_root=repository) is None
