from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import pytest


def _module():
    return importlib.import_module("symgov_backend.ed_corpus_cli")


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _inputs(root: Path, *, content: str = "# Guide\nEd is read-only.\n") -> tuple[Path, Path]:
    source = root / "docs/guide.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(content, encoding="utf-8")
    source_version = f"sha256:{hashlib.sha256(source.read_bytes()).hexdigest()}"
    inventory = {
        "schemaVersion": "1.0",
        "inventory": {
            "schema_version": "1.0",
            "inventory_version": "draft:test",
            "sources": [
                {
                    "id": "source:guide:v1",
                    "title": "Guide",
                    "topic": "application",
                    "source_path": "docs/guide.md",
                    "source_version": source_version,
                    "source_kind": "markdown",
                    "source_line_start": 1,
                    "source_line_end": 2,
                    "source_heading": "guide",
                }
            ],
        },
        "sourceApprovals": [
            {
                "source_id": "source:guide:v1",
                "rights_disposition": "repository_owned",
                "approved_line_start": 1,
                "approved_line_end": 2,
                "approved_symbol": "guide",
            }
        ],
    }
    record = {
        "id": "document:guide:v1",
        "title": "Guide",
        "topic": "application",
        "source_path": "docs/guide.md",
        "source_version": source_version,
        "extracted_at": "2026-09-29T12:00:00Z",
        "source_line_start": 1,
        "source_line_end": 2,
        "source_symbol": "guide",
        "visibility": "authenticated",
        "approval_state": "draft",
        "supersedes": [],
        "superseded_by": [],
    }
    claims = {
        "schemaVersion": "1.0",
        "manifest": {
            "schema_version": "1.0",
            "knowledge_version": "draft:test",
            "generated_at": "2026-09-29T12:00:00Z",
            "documents": [
                {
                    **record,
                    "claims": [
                        {
                            **record,
                            "id": "claim:guide-read-only:v1",
                            "text": "Ed's first phase is read-only.",
                        }
                    ],
                }
            ],
        },
        "claimMetadata": [
            {
                "claim_id": "claim:guide-read-only:v1",
                "fact_key": "application.ed.read-only",
                "scope": "product",
            }
        ],
    }
    inventory_path = root / "reviewed-inventory.json"
    claims_path = root / "reviewed-claims.json"
    inventory_path.write_bytes(_canonical(inventory))
    claims_path.write_bytes(_canonical(claims))
    return inventory_path, claims_path


def _build(root: Path, external: Path, *, name: str = "bundle", content: str | None = None) -> Path:
    module = _module()
    inventory, claims = _inputs(root) if content is None else _inputs(root, content=content)
    output = external / name
    result = module.build_bundle(
        repository=root,
        inventory_path=inventory,
        claims_path=claims,
        output=output,
        source_commit="a" * 40,
        extracted_at="2026-09-29T12:00:00Z",
    )
    assert result.manifest_digest.startswith("sha256:")
    return output


def test_build_writes_draft_bundle_with_separate_manifest_and_index_digests(
    tmp_path: Path,
):
    module = _module()
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()

    bundle = _build(repository, external)

    assert {path.name for path in bundle.iterdir()} == {
        "build-report.json",
        "chunks.jsonl",
        "manifest.json",
        "postings.json",
    }
    manifest_bytes = (bundle / "manifest.json").read_bytes()
    chunks_bytes = (bundle / "chunks.jsonl").read_bytes()
    postings_bytes = (bundle / "postings.json").read_bytes()
    report = json.loads((bundle / "build-report.json").read_bytes())
    manifest = json.loads(manifest_bytes)
    assert manifest["status"] == "draft"
    assert manifest["indexDigest"] == module.index_digest(chunks_bytes, postings_bytes)
    assert report["manifestDigest"] == f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"
    assert report["indexDigest"] == manifest["indexDigest"]
    assert postings_bytes == importlib.import_module("symgov_backend.ed_retrieval").build_postings(
        chunks_bytes
    )
    assert module.verify_bundle(bundle=bundle, repository=repository).valid is True


def test_repeated_builds_with_pinned_inputs_are_byte_identical(tmp_path: Path):
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()

    first = _build(repository, external, name="first")
    second = _build(repository, external, name="second")

    assert {
        path.name: path.read_bytes() for path in first.iterdir()
    } == {path.name: path.read_bytes() for path in second.iterdir()}


def test_cli_build_and_verify_are_explicit_and_verify_is_read_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    module = _module()
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    inventory, claims = _inputs(repository)
    bundle = external / "bundle"

    assert (
        module.main(
            [
                "build",
                "--repository",
                str(repository),
                "--inventory",
                str(inventory),
                "--claims",
                str(claims),
                "--output",
                str(bundle),
                "--source-commit",
                "a" * 40,
                "--extracted-at",
                "2026-09-29T12:00:00Z",
            ]
        )
        == 0
    )
    before = {path.name: path.stat().st_mtime_ns for path in bundle.iterdir()}
    assert module.main(["verify", "--bundle", str(bundle), "--repository", str(repository)]) == 0
    after = {path.name: path.stat().st_mtime_ns for path in bundle.iterdir()}

    assert before == after
    assert "draft" in capsys.readouterr().out


@pytest.mark.parametrize("problem", ["nonempty", "inside_repository", "symlink", "live_name"])
def test_build_refuses_nonempty_unsafe_symlink_and_live_targets(
    tmp_path: Path, problem: str
):
    module = _module()
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    inventory, claims = _inputs(repository)
    output = external / "bundle"
    if problem == "nonempty":
        output.mkdir()
        (output / "prior.txt").write_text("prior", encoding="utf-8")
    elif problem == "inside_repository":
        output = repository / "bundle"
    elif problem == "symlink":
        real = external / "real"
        real.mkdir()
        output.symlink_to(real, target_is_directory=True)
    else:
        output = external / "live"

    with pytest.raises(module.BundleError) as exc:
        module.build_bundle(
            repository=repository,
            inventory_path=inventory,
            claims_path=claims,
            output=output,
            source_commit="a" * 40,
            extracted_at="2026-09-29T12:00:00Z",
        )

    assert exc.value.code == "unsafe_output"
    if problem == "nonempty":
        assert (output / "prior.txt").read_text(encoding="utf-8") == "prior"


@pytest.mark.parametrize("problem", ["extra_field", "unsupported_rights", "oversized"])
def test_build_rejects_strict_invalid_or_oversized_inputs(tmp_path: Path, problem: str):
    module = _module()
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    inventory, claims = _inputs(repository)
    payload = json.loads(inventory.read_bytes())
    if problem == "extra_field":
        payload["unexpected"] = True
        inventory.write_bytes(_canonical(payload))
    elif problem == "unsupported_rights":
        payload["sourceApprovals"][0]["rights_disposition"] = "unknown"
        inventory.write_bytes(_canonical(payload))
    else:
        inventory.write_bytes(b" " * (module.MAX_INPUT_BYTES + 1))

    with pytest.raises(module.BundleError) as exc:
        module.build_bundle(
            repository=repository,
            inventory_path=inventory,
            claims_path=claims,
            output=external / "bundle",
            source_commit="a" * 40,
            extracted_at="2026-09-29T12:00:00Z",
        )

    assert exc.value.code in {"invalid_input", "input_too_large"}
    assert not (external / "bundle").exists()


def test_failed_partial_build_is_isolated_and_does_not_replace_prior_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _module()
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    prior = _build(repository, external, name="prior")
    inventory, claims = _inputs(repository)

    def fail_postings(_chunks: bytes) -> bytes:
        raise RuntimeError("simulated index failure")

    monkeypatch.setattr(module, "build_postings", fail_postings)
    with pytest.raises(module.BundleError, match="build failed"):
        module.build_bundle(
            repository=repository,
            inventory_path=inventory,
            claims_path=claims,
            output=external / "failed",
            source_commit="a" * 40,
            extracted_at="2026-09-29T12:00:00Z",
        )

    monkeypatch.undo()
    assert not (external / "failed").exists()
    assert module.verify_bundle(bundle=prior, repository=repository).valid is True
    assert not [path for path in external.iterdir() if path.name.startswith(".ed-corpus-build-")]


def test_atomic_publish_does_not_replace_target_created_during_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _module()
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    inventory, claims = _inputs(repository)
    output = external / "raced-target"
    original_verify = module.verify_bundle

    def create_competing_target(*, bundle: Path, repository: Path):
        result = original_verify(bundle=bundle, repository=repository)
        output.mkdir()
        return result

    monkeypatch.setattr(module, "verify_bundle", create_competing_target)
    with pytest.raises(module.BundleError) as exc:
        module.build_bundle(
            repository=repository,
            inventory_path=inventory,
            claims_path=claims,
            output=output,
            source_commit="a" * 40,
            extracted_at="2026-09-29T12:00:00Z",
        )

    assert exc.value.code == "unsafe_output"
    assert output.is_dir()
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("filename", ["postings.json", "chunks.jsonl", "manifest.json", "build-report.json"])
def test_verify_fails_closed_for_tampered_bundle_bytes(tmp_path: Path, filename: str):
    module = _module()
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    bundle = _build(repository, external)
    path = bundle / filename
    path.write_bytes(path.read_bytes() + b" ")

    with pytest.raises(module.BundleError) as exc:
        module.verify_bundle(bundle=bundle, repository=repository)

    assert exc.value.code == "invalid_bundle"


def test_verify_rejects_unexpected_files_symlinks_and_source_version_drift(tmp_path: Path):
    module = _module()
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()

    extra_bundle = _build(repository, external, name="extra")
    (extra_bundle / "unexpected").write_text("x", encoding="utf-8")
    with pytest.raises(module.BundleError, match="unexpected"):
        module.verify_bundle(bundle=extra_bundle, repository=repository)

    symlink_bundle = _build(repository, external, name="symlink")
    (symlink_bundle / "postings.json").unlink()
    (symlink_bundle / "postings.json").symlink_to(extra_bundle / "postings.json")
    with pytest.raises(module.BundleError, match="symbolic"):
        module.verify_bundle(bundle=symlink_bundle, repository=repository)

    drift_bundle = _build(repository, external, name="drift")
    (repository / "docs/guide.md").write_text("changed\n", encoding="utf-8")
    with pytest.raises(module.BundleError) as exc:
        module.verify_bundle(bundle=drift_bundle, repository=repository)
    assert exc.value.code == "source_drift"


def test_verify_rejects_self_consistent_bundle_with_invalid_source_range(tmp_path: Path):
    module = _module()
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    bundle = _build(repository, external)
    chunk = json.loads((bundle / "chunks.jsonl").read_bytes())
    chunk["sourceLineEnd"] = 99
    chunks_bytes = _canonical(chunk)
    postings_bytes = importlib.import_module("symgov_backend.ed_retrieval").build_postings(
        chunks_bytes
    )
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    manifest["chunkDigest"] = f"sha256:{hashlib.sha256(chunks_bytes).hexdigest()}"
    manifest["postingsDigest"] = f"sha256:{hashlib.sha256(postings_bytes).hexdigest()}"
    manifest["indexDigest"] = module.index_digest(chunks_bytes, postings_bytes)
    manifest_bytes = _canonical(manifest)
    report = json.loads((bundle / "build-report.json").read_bytes())
    report["indexDigest"] = manifest["indexDigest"]
    report["manifestDigest"] = f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"
    (bundle / "chunks.jsonl").write_bytes(chunks_bytes)
    (bundle / "postings.json").write_bytes(postings_bytes)
    (bundle / "manifest.json").write_bytes(manifest_bytes)
    (bundle / "build-report.json").write_bytes(_canonical(report))

    with pytest.raises(module.BundleError) as exc:
        module.verify_bundle(bundle=bundle, repository=repository)

    assert exc.value.code == "source_drift"


def test_verify_rejects_self_consistent_manifest_with_invalid_build_identity(tmp_path: Path):
    module = _module()
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    bundle = _build(repository, external)
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    manifest["sourceCommit"] = "not-a-commit"
    manifest_bytes = _canonical(manifest)
    report = json.loads((bundle / "build-report.json").read_bytes())
    report["manifestDigest"] = f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"
    (bundle / "manifest.json").write_bytes(manifest_bytes)
    (bundle / "build-report.json").write_bytes(_canonical(report))

    with pytest.raises(module.BundleError) as exc:
        module.verify_bundle(bundle=bundle, repository=repository)

    assert exc.value.code == "invalid_bundle"


def test_verify_reports_malformed_nested_manifest_without_traceback(tmp_path: Path):
    module = _module()
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    bundle = _build(repository, external)
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    manifest["sources"].append(
        {
            "id": {},
            "path": "docs/guide.md",
            "rightsDisposition": "repository_owned",
            "version": manifest["sources"][0]["version"],
        }
    )
    manifest_bytes = _canonical(manifest)
    report = json.loads((bundle / "build-report.json").read_bytes())
    report["manifestDigest"] = f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"
    (bundle / "manifest.json").write_bytes(manifest_bytes)
    (bundle / "build-report.json").write_bytes(_canonical(report))

    with pytest.raises(module.BundleError) as exc:
        module.verify_bundle(bundle=bundle, repository=repository)

    assert exc.value.code == "invalid_bundle"


def test_fixture_contract_remains_synthetic_complete_and_compatible_with_index_format(
    tmp_path: Path,
):
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    bundle = _build(repository, external)
    fixture_path = (
        Path(__file__).parents[1]
        / "backend/symgov_backend/data/ed_knowledge/query_fixtures.stage4.json"
    )
    fixture = json.loads(fixture_path.read_bytes())

    assert fixture["status"] == "draft"
    assert fixture["purpose"] == "synthetic retrieval mechanics only; not factual approval"
    assert len(fixture["queries"]) == 20
    assert {entry["expectedStatus"] for entry in fixture["queries"]} == {
        "answered",
        "cannot_answer",
    }
    assert (bundle / "postings.json").read_bytes() == importlib.import_module(
        "symgov_backend.ed_retrieval"
    ).build_postings((bundle / "chunks.jsonl").read_bytes())
    assert json.loads((bundle / "build-report.json").read_bytes())["queryFixtureStatus"] == (
        "not_run_synthetic_fixture_only"
    )


def test_parser_has_no_approval_or_rollback_mutators():
    module = _module()

    with pytest.raises(SystemExit):
        module.main(["approve"])
    with pytest.raises(SystemExit):
        module.main(["rollback"])


def _rewrite_chunk(bundle: Path, change) -> None:
    """Edit the single chunk and recompute every digest, as a tamperer would."""
    module = _module()
    chunk = json.loads((bundle / "chunks.jsonl").read_bytes())
    change(chunk)
    chunks_bytes = _canonical(chunk)
    postings_bytes = importlib.import_module("symgov_backend.ed_retrieval").build_postings(
        chunks_bytes
    ) if "approvalState" not in chunk else b"{}\n"
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    manifest["chunkDigest"] = f"sha256:{hashlib.sha256(chunks_bytes).hexdigest()}"
    manifest["postingsDigest"] = f"sha256:{hashlib.sha256(postings_bytes).hexdigest()}"
    manifest["indexDigest"] = module.index_digest(chunks_bytes, postings_bytes)
    manifest_bytes = _canonical(manifest)
    report = json.loads((bundle / "build-report.json").read_bytes())
    report["indexDigest"] = manifest["indexDigest"]
    report["manifestDigest"] = f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"
    (bundle / "chunks.jsonl").write_bytes(chunks_bytes)
    (bundle / "postings.json").write_bytes(postings_bytes)
    (bundle / "manifest.json").write_bytes(manifest_bytes)
    (bundle / "build-report.json").write_bytes(_canonical(report))


def test_a_built_bundle_answers_once_its_exact_index_digest_is_approved(tmp_path: Path):
    """End to end: the builder's output is what retrieval serves. Before this
    test, a heading-located chunk built here was unretrievable, and nothing
    noticed because retrieval was only tested on hand-written chunks."""
    retrieval = importlib.import_module("symgov_backend.ed_retrieval")
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    bundle = _build(repository, external)
    chunks_bytes = (bundle / "chunks.jsonl").read_bytes()
    postings_bytes = (bundle / "postings.json").read_bytes()
    manifest = json.loads((bundle / "manifest.json").read_bytes())

    def ask(approved: str | None):
        return retrieval.query_index(
            postings_bytes,
            chunks_bytes,
            "What is the first phase?",
            knowledge_version="fixture-v1",
            approved_index_digest=approved,
            repository_root=repository,
            offline_fixture=True,
        )

    assert ask(None).status == "unavailable"
    answered = ask(manifest["indexDigest"])
    assert answered.status == "answered"
    assert [item.chunk_id for item in answered.items] == ["claim:guide-read-only:v1"]
    assert answered.items[0].citation.source_locator == "symbol guide"


def test_verify_rejects_a_chunk_that_claims_its_own_approval(tmp_path: Path):
    module = _module()
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    bundle = _build(repository, external)
    _rewrite_chunk(bundle, lambda chunk: chunk.update(approvalState="published"))

    with pytest.raises(module.BundleError) as exc:
        module.verify_bundle(bundle=bundle, repository=repository)

    assert exc.value.code == "invalid_bundle"


def test_verify_accepts_a_heading_slug_that_is_not_verbatim_in_the_source(tmp_path: Path):
    """`guide` names `# Guide`. The resolved range, not the slug, is checked."""
    module = _module()
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    bundle = _build(repository, external)
    _rewrite_chunk(bundle, lambda chunk: chunk.update(sourceSymbol="guide"))

    assert module.verify_bundle(bundle=bundle, repository=repository).valid is True


def test_an_unexpected_build_error_is_bounded_and_leaves_no_partial_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _module()
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    inventory, claims = _inputs(repository)

    def fail_build(*_args, **_kwargs):
        raise ValueError("unexpected validation failure with private detail")

    monkeypatch.setattr(module, "build_draft_corpus", fail_build)
    with pytest.raises(module.BundleError) as exc:
        module.build_bundle(
            repository=repository,
            inventory_path=inventory,
            claims_path=claims,
            output=external / "failed",
            source_commit="a" * 40,
            extracted_at="2026-09-29T12:00:00Z",
        )

    assert exc.value.code == "build_failed"
    assert "private detail" not in str(exc.value)
    assert not (external / "failed").exists()
    assert not [path for path in external.iterdir() if path.name.startswith(".ed-corpus-build-")]


# --- Decision 7.2 (2026-09-29): the steward signs a receipt with an SSH key
# kept off the server. These tests make a throwaway key per test.

import shutil  # noqa: E402
import subprocess  # noqa: E402

requires_ssh_keygen = pytest.mark.skipif(
    shutil.which("ssh-keygen") is None, reason="ssh-keygen is required"
)
NAMESPACE = "symgov-ed-knowledge"


def _steward_key(directory: Path, name: str = "steward") -> tuple[Path, str]:
    directory.mkdir(parents=True, exist_ok=True)
    key = directory / name
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(key)],
        check=True,
    )
    return key, (directory / f"{name}.pub").read_text().strip()


def _allowed_signers(path: Path, *entries: tuple[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f'{principal} namespaces="{NAMESPACE}" {public}\n' for principal, public in entries)
    )
    return path


def _sign(key: Path, receipt: Path, *, namespace: str = NAMESPACE) -> Path:
    subprocess.run(
        ["ssh-keygen", "-q", "-Y", "sign", "-f", str(key), "-n", namespace, str(receipt)],
        check=True,
        capture_output=True,
    )
    return receipt.with_name(receipt.name + ".sig")


def _approval_setup(tmp_path: Path):
    module = _module()
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    bundle = _build(repository, external)
    keys = tmp_path / "steward-machine"
    key, public = _steward_key(keys)
    signers = _allowed_signers(tmp_path / "server-config" / "allowed_signers", ("steward@example.invalid", public))
    receipt = tmp_path / "receipt" / "approval.json"
    receipt.parent.mkdir()
    module.prepare_receipt(
        bundle=bundle,
        repository=repository,
        steward="Ed Knowledge Steward",
        approved_at="2026-09-29T18:00:00Z",
        output=receipt,
    )
    return module, repository, bundle, key, signers, receipt


@requires_ssh_keygen
def test_a_receipt_signed_by_a_listed_steward_verifies_against_the_exact_bundle(tmp_path: Path):
    module, repository, bundle, key, signers, receipt = _approval_setup(tmp_path)
    signature = _sign(key, receipt)
    manifest = json.loads((bundle / "manifest.json").read_bytes())

    result = module.verify_approval(
        bundle=bundle,
        repository=repository,
        receipt=receipt,
        signature=signature,
        allowed_signers=signers,
    )

    assert result.principal == "steward@example.invalid"
    assert result.index_digest == manifest["indexDigest"]
    body = json.loads(receipt.read_bytes())
    assert body["decision"] == "approved"
    assert body["indexDigest"] == manifest["indexDigest"]
    assert body["sourceCommit"] == manifest["sourceCommit"]
    assert sorted(path.name for path in bundle.iterdir()) == sorted(module.EXPECTED_FILES)


@requires_ssh_keygen
@pytest.mark.parametrize("problem", ["tampered", "unlisted_key", "wrong_namespace"])
def test_a_receipt_that_is_not_the_stewards_signed_text_is_refused(tmp_path: Path, problem: str):
    module, repository, bundle, key, signers, receipt = _approval_setup(tmp_path)
    if problem == "unlisted_key":
        key, _public = _steward_key(tmp_path / "agent-box", "agent")
    signature = _sign(key, receipt, namespace="file" if problem == "wrong_namespace" else NAMESPACE)
    if problem == "tampered":
        body = json.loads(receipt.read_bytes())
        body["approvedAt"] = "2026-09-30T18:00:00Z"
        receipt.write_bytes(_canonical(body))

    with pytest.raises(module.BundleError) as exc:
        module.verify_approval(
            bundle=bundle,
            repository=repository,
            receipt=receipt,
            signature=signature,
            allowed_signers=signers,
        )

    assert exc.value.code == "approval_invalid"


@requires_ssh_keygen
def test_a_valid_signature_over_another_bundles_digest_is_refused(tmp_path: Path):
    module, repository, bundle, key, signers, receipt = _approval_setup(tmp_path)
    body = json.loads(receipt.read_bytes())
    body["indexDigest"] = "sha256:" + "0" * 64
    receipt.write_bytes(_canonical(body))
    signature = _sign(key, receipt)

    with pytest.raises(module.BundleError) as exc:
        module.verify_approval(
            bundle=bundle,
            repository=repository,
            receipt=receipt,
            signature=signature,
            allowed_signers=signers,
        )

    assert exc.value.code == "approval_mismatch"


@requires_ssh_keygen
def test_a_signer_list_inside_the_repository_is_refused(tmp_path: Path):
    """Agents can write to the repository, so a key list there proves nothing."""
    module, repository, bundle, key, signers, receipt = _approval_setup(tmp_path)
    signature = _sign(key, receipt)
    inside = repository / "allowed_signers"
    inside.write_bytes(signers.read_bytes())

    with pytest.raises(module.BundleError) as exc:
        module.verify_approval(
            bundle=bundle,
            repository=repository,
            receipt=receipt,
            signature=signature,
            allowed_signers=inside,
        )

    assert exc.value.code == "unsafe_signers"


def test_prepare_receipt_never_writes_into_the_bundle_or_over_a_file(tmp_path: Path):
    module = _module()
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    bundle = _build(repository, external)
    existing = tmp_path / "existing.json"
    existing.write_text("{}")

    for output in (bundle / "approval.json", existing):
        with pytest.raises(module.BundleError) as exc:
            module.prepare_receipt(
                bundle=bundle,
                repository=repository,
                steward="Ed Knowledge Steward",
                approved_at="2026-09-29T18:00:00Z",
                output=output,
            )
        assert exc.value.code == "unsafe_output"
    assert existing.read_text() == "{}"


@requires_ssh_keygen
def test_cli_prepare_and_verify_approval_are_read_only_commands(tmp_path: Path, capsys):
    module, repository, bundle, key, signers, _receipt = _approval_setup(tmp_path)
    receipt = tmp_path / "receipt" / "cli.json"

    assert module.main([
        "prepare-receipt", "--bundle", str(bundle), "--repository", str(repository),
        "--steward", "Ed Knowledge Steward", "--approved-at", "2026-09-29T18:00:00Z",
        "--output", str(receipt),
    ]) == 0
    signature = _sign(key, receipt)
    assert module.main([
        "verify-approval", "--bundle", str(bundle), "--repository", str(repository),
        "--receipt", str(receipt), "--signature", str(signature),
        "--allowed-signers", str(signers),
    ]) == 0

    out = capsys.readouterr().out
    assert "approval verified; steward=steward@example.invalid" in out
    with pytest.raises(SystemExit):
        module.main(["approve"])


def test_a_source_over_the_old_64_kb_limit_builds_and_is_retrievable(tmp_path: Path):
    """Decision 7.4 (2026-09-29): 256 KB per source file, one limit shared by
    the builder, verify and retrieval, so a file one accepts the others do too."""
    retrieval = importlib.import_module("symgov_backend.ed_retrieval")
    corpus = importlib.import_module("symgov_backend.ed_corpus")
    assert corpus.MAX_SOURCE_BYTES == retrieval.MAX_SOURCE_BYTES == 262_144
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    padding = "".join(f"Padding line {index}.\n" for index in range(6_000))
    content = "# Guide\nEd is read-only.\n" + padding
    assert 65_536 < len(content.encode()) < 262_144

    bundle = _build(repository, external, content=content)
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    result = retrieval.query_index(
        (bundle / "postings.json").read_bytes(),
        (bundle / "chunks.jsonl").read_bytes(),
        "What is the first phase?",
        knowledge_version="fixture-v1",
        approved_index_digest=manifest["indexDigest"],
        repository_root=repository,
        offline_fixture=True,
    )

    assert result.status == "answered"
