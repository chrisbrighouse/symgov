from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from symgov_backend.ed_corpus import (
    ClaimMetadata,
    CorpusValidationError,
    DraftCorpusRequest,
    SourceApproval,
    build_draft_corpus,
)
from symgov_backend.ed_knowledge import KnowledgeManifest, REQUIRED_TOPICS, source_path_issues
from symgov_backend.ed_knowledge_sources import SourceInventory
from symgov_backend.ed_retrieval import (
    MAX_CHUNKS_BYTES,
    MAX_INDEX_BYTES,
    MAX_SOURCE_BYTES,
    RetrievalIndexError,
    build_postings,
    index_digest,
)

MAX_INPUT_BYTES = 1_000_000
MAX_MANIFEST_BYTES = 256_000
MAX_REPORT_BYTES = 256_000
MAX_DIAGNOSTICS = 20
MAX_DIAGNOSTIC_CHARS = 200
EXPECTED_FILES = frozenset(
    {"manifest.json", "chunks.jsonl", "postings.json", "build-report.json"}
)
_FORBIDDEN_OUTPUT_NAMES = frozenset(
    {"approved", "current", "live", "production", "release", "releases"}
)
_SHA256 = "sha256:"
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
# Decision 7.2 (2026-09-29): the Ed Knowledge Steward approves a bundle by
# signing a receipt with an SSH key kept off the server. The signature
# namespace stops a signature made for any other purpose being reused here.
APPROVAL_NAMESPACE = "symgov-ed-knowledge"
MAX_RECEIPT_BYTES = 4_096
MAX_SIGNATURE_BYTES = 16_384
MAX_SIGNERS_BYTES = 65_536
_SSH_KEYGEN_TIMEOUT_SECONDS = 10


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class _InventoryInput(_StrictModel):
    schema_version: Literal["1.0"] = Field(alias="schemaVersion")
    inventory: SourceInventory
    source_approvals: tuple[SourceApproval, ...] = Field(
        alias="sourceApprovals", min_length=1
    )


class _ClaimsInput(_StrictModel):
    schema_version: Literal["1.0"] = Field(alias="schemaVersion")
    manifest: KnowledgeManifest
    claim_metadata: tuple[ClaimMetadata, ...] = Field(alias="claimMetadata", min_length=1)


class ApprovalReceipt(_StrictModel):
    """The exact text a steward signs. Its canonical bytes are what is verified."""

    schemaVersion: Literal["1.0"]
    kind: Literal["symgov-ed-knowledge-approval"]
    decision: Literal["approved"]
    indexDigest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    manifestDigest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    sourceCommit: str = Field(pattern=r"^[0-9a-f]{40}$")
    # A label for people reading the receipt. Identity comes from the key.
    steward: str = Field(min_length=1, max_length=120)
    approvedAt: str = Field(min_length=20, max_length=40)

    @field_validator("steward")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("steward must be plain text")
        return value

    @field_validator("approvedAt")
    @classmethod
    def require_utc(cls, value: str) -> str:
        if not _is_utc_timestamp(value):
            raise ValueError("approvedAt must be a UTC timestamp ending in Z")
        return value


@dataclass(frozen=True)
class ApprovalResult:
    principal: str
    index_digest: str
    manifest_digest: str
    drifted_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class BuildResult:
    status: Literal["draft"]
    output: Path
    manifest_digest: str
    index_digest: str


@dataclass(frozen=True)
class VerificationResult:
    valid: Literal[True]
    status: Literal["draft"]
    manifest_digest: str
    index_digest: str
    drifted_sources: tuple[str, ...] = ()


class BundleError(ValueError):
    def __init__(self, code: str, message: str, diagnostics: tuple[str, ...] = ()):
        self.code = code
        self.diagnostics = tuple(
            item[:MAX_DIAGNOSTIC_CHARS] for item in diagnostics[:MAX_DIAGNOSTICS]
        )
        super().__init__(message[:MAX_DIAGNOSTIC_CHARS])


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _digest(data: bytes) -> str:
    return f"{_SHA256}{hashlib.sha256(data).hexdigest()}"


def _is_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    offset = parsed.utcoffset()
    return offset is not None and offset.total_seconds() == 0


def _reject_symlink_chain(path: Path, *, code: str, label: str) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise BundleError(code, f"{label} must not traverse symbolic links")
        if not current.exists():
            break


def _repository_root(repository: str | Path) -> Path:
    path = Path(repository).absolute()
    _reject_symlink_chain(path, code="invalid_repository", label="repository")
    if not path.is_dir():
        raise BundleError("invalid_repository", "repository must be an existing directory")
    return path.resolve()


def _read_regular(
    path: Path, *, maximum: int, code: str, label: str, root: Path | None = None
) -> bytes:
    _reject_symlink_chain(path, code=code, label=label)
    if root is not None:
        try:
            path.resolve().relative_to(root)
        except ValueError as exc:
            raise BundleError(code, f"{label} escapes its required directory") from exc
    if not path.is_file():
        raise BundleError(code, f"{label} must be a regular file")
    try:
        with path.open("rb") as handle:
            data = handle.read(maximum + 1)
    except OSError as exc:
        raise BundleError(code, f"{label} could not be read") from exc
    if len(data) > maximum:
        raise BundleError(
            "input_too_large" if code == "invalid_input" else code,
            f"{label} exceeds its byte limit",
        )
    return data


def _load_input(path: str | Path, model: type[_StrictModel], label: str) -> _StrictModel:
    raw = _read_regular(
        Path(path).absolute(), maximum=MAX_INPUT_BYTES, code="invalid_input", label=label
    )
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        diagnostics = tuple(
            ".".join(str(part) for part in error.get("loc", ())) or label
            for error in exc.errors(include_url=False, include_input=False)
        )
        raise BundleError("invalid_input", f"{label} does not match the strict schema", diagnostics) from exc


def _validate_output(repository: Path, output: str | Path) -> tuple[Path, Path]:
    target = Path(output).absolute()
    _reject_symlink_chain(target, code="unsafe_output", label="output")
    if target.exists():
        raise BundleError("unsafe_output", "output must be a new, non-existing staging path")
    if any(part.casefold() in _FORBIDDEN_OUTPUT_NAMES for part in target.parts):
        raise BundleError("unsafe_output", "output name is reserved for live or release data")
    parent = target.parent
    _reject_symlink_chain(parent, code="unsafe_output", label="output parent")
    if not parent.is_dir():
        raise BundleError("unsafe_output", "output parent must be an existing directory")
    target_resolved = target.resolve(strict=False)
    try:
        target_resolved.relative_to(repository)
    except ValueError:
        pass
    else:
        raise BundleError("unsafe_output", "output must be external to the repository")
    if target_resolved in {Path(target_resolved.anchor), Path.home().resolve(), repository}:
        raise BundleError("unsafe_output", "output target is unsafe")
    return target, parent


def _write_file(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _publish_directory_no_replace(stage: Path, target: Path) -> None:
    """Atomically publish on Linux without replacing any concurrently created target."""
    library = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(library, "renameat2", None)
    if renameat2 is None:
        raise BundleError(
            "build_failed", "atomic no-replace directory publication is unavailable"
        )
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,
        os.fsencode(stage),
        -100,
        os.fsencode(target),
        1,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY, errno.ENOTDIR}:
        raise BundleError(
            "unsafe_output", "output appeared during build and was not replaced"
        )
    raise OSError(error_number, os.strerror(error_number), str(target))


def _canonical_chunks(chunks_bytes: bytes) -> None:
    try:
        text = chunks_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleError("invalid_bundle", "chunks must be UTF-8") from exc
    if text and not text.endswith("\n"):
        raise BundleError("invalid_bundle", "chunks must end with a newline")
    rebuilt = bytearray()
    for line in text.splitlines():
        if not line:
            raise BundleError("invalid_bundle", "chunks must not contain blank records")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BundleError("invalid_bundle", "chunks contain malformed JSON") from exc
        rebuilt.extend(_canonical_json(value))
    if bytes(rebuilt) != chunks_bytes:
        raise BundleError("invalid_bundle", "chunks are not canonical")


def _load_canonical_object(data: bytes, *, label: str) -> dict[str, object]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError("invalid_bundle", f"{label} is malformed") from exc
    if not isinstance(value, dict) or _canonical_json(value) != data:
        raise BundleError("invalid_bundle", f"{label} is not canonical")
    return value


def _source_identity_issues(
    manifest: dict[str, object], chunks_bytes: bytes, repository: Path
) -> tuple[str, ...]:
    raw_sources = manifest.get("sources")
    if not isinstance(raw_sources, list):
        return ("manifest.sources",)
    sources: dict[str, str] = {}
    issues: list[str] = []
    for item in raw_sources:
        if not isinstance(item, dict) or set(item) != {
            "id",
            "path",
            "rightsDisposition",
            "version",
        }:
            issues.append("manifest.sources")
            continue
        path = item.get("path")
        version = item.get("version")
        # One file may back several reviewed ranges, but only at one version.
        if not isinstance(path, str) or not isinstance(version, str) or sources.get(path, version) != version:
            issues.append("manifest.sources")
            continue
        sources[path] = version

    chunks: list[dict[str, object]] = []
    chunk_sources: dict[str, str] = {}
    for line in chunks_bytes.decode("utf-8").splitlines():
        item = json.loads(line)
        path = item.get("sourcePath") if isinstance(item, dict) else None
        version = item.get("sourceVersion") if isinstance(item, dict) else None
        if not isinstance(path, str) or not isinstance(version, str):
            issues.append("chunks.sourceIdentity")
            continue
        chunks.append(item)
        if path in chunk_sources and chunk_sources[path] != version:
            issues.append("chunks.sourceIdentity")
        chunk_sources[path] = version
    if chunk_sources != {path: sources[path] for path in chunk_sources if path in sources}:
        issues.append("chunks.sourceIdentity")

    source_text: dict[str, str] = {}
    for path_text, version in sources.items():
        path_issues = source_path_issues(path_text, path_text, repository)
        if path_issues:
            issues.append(f"source:{path_text}")
            continue
        path = repository / path_text
        try:
            with path.open("rb") as handle:
                data = handle.read(MAX_SOURCE_BYTES + 1)
        except OSError:
            issues.append(f"source:{path_text}")
            continue
        if len(data) > MAX_SOURCE_BYTES or version != _digest(data):
            issues.append(f"source:{path_text}")
            continue
        try:
            source_text[path_text] = data.decode("utf-8")
        except UnicodeDecodeError:
            issues.append(f"source:{path_text}")

    for chunk in chunks:
        path_text = chunk["sourcePath"]
        assert isinstance(path_text, str)
        text = source_text.get(path_text)
        if text is None:
            continue
        # The builder resolved every heading or symbol to these lines, and the
        # source bytes matched their pinned hash above, so the range is the
        # whole locator check. The symbol is only a display label.
        start = chunk.get("sourceLineStart")
        end = chunk.get("sourceLineEnd")
        valid_range = (
            isinstance(start, int)
            and not isinstance(start, bool)
            and isinstance(end, int)
            and not isinstance(end, bool)
            and 1 <= start <= end <= len(text.splitlines())
        )
        if not valid_range:
            issues.append(f"locator:{chunk.get('id', 'unknown')}")
    return tuple(sorted(set(issues))[:MAX_DIAGNOSTICS])


def build_bundle(
    *,
    repository: str | Path,
    inventory_path: str | Path,
    claims_path: str | Path,
    output: str | Path,
    source_commit: str,
    extracted_at: str,
) -> BuildResult:
    root = _repository_root(repository)
    target, parent = _validate_output(root, output)
    inventory_input = _load_input(inventory_path, _InventoryInput, "inventory input")
    claims_input = _load_input(claims_path, _ClaimsInput, "claims input")
    assert isinstance(inventory_input, _InventoryInput)
    assert isinstance(claims_input, _ClaimsInput)
    try:
        request = DraftCorpusRequest.model_validate(
            {
                "source_commit": source_commit,
                "extracted_at": extracted_at,
                "inventory": inventory_input.inventory,
                "manifest": claims_input.manifest,
                "source_approvals": inventory_input.source_approvals,
                "claim_metadata": claims_input.claim_metadata,
            }
        )
    except ValidationError as exc:
        diagnostics = tuple(
            ".".join(str(part) for part in error.get("loc", ())) or "request"
            for error in exc.errors(include_url=False, include_input=False)
        )
        raise BundleError("invalid_input", "build arguments do not match the strict schema", diagnostics) from exc

    stage = Path(tempfile.mkdtemp(prefix=".ed-corpus-build-", dir=parent))
    try:
        try:
            draft = build_draft_corpus(request, repository_root=root)
            postings_bytes = build_postings(draft.chunks_bytes)
        except CorpusValidationError as exc:
            diagnostics = tuple(f"{issue.code}:{issue.reference}" for issue in exc.issues)
            raise BundleError("invalid_input", "reviewed corpus input failed validation", diagnostics) from exc
        except (RetrievalIndexError, OSError, RuntimeError, ValueError) as exc:
            # ValueError covers a model validation failure inside extraction;
            # its message can quote source text, so it is not echoed.
            raise BundleError("build_failed", "build failed before publication to staging") from exc

        manifest = json.loads(draft.manifest_bytes)
        manifest["indexVersion"] = "draft-lexical-v1"
        manifest["postingsDigest"] = _digest(postings_bytes)
        manifest["indexDigest"] = index_digest(draft.chunks_bytes, postings_bytes)
        manifest_bytes = _canonical_json(manifest)

        report = json.loads(draft.report_bytes)
        report.update(
            {
                "indexDigest": manifest["indexDigest"],
                "manifestDigest": _digest(manifest_bytes),
                "queryFixtureStatus": "not_run_synthetic_fixture_only",
            }
        )
        report_bytes = _canonical_json(report)
        files = {
            "manifest.json": manifest_bytes,
            "chunks.jsonl": draft.chunks_bytes,
            "postings.json": postings_bytes,
            "build-report.json": report_bytes,
        }
        for name, data in files.items():
            _write_file(stage / name, data)
        directory_fd = os.open(stage, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        verify_bundle(bundle=stage, repository=root)
        _publish_directory_no_replace(stage, target)
        parent_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return BuildResult(
            status="draft",
            output=target,
            manifest_digest=str(report["manifestDigest"]),
            index_digest=str(manifest["indexDigest"]),
        )
    except BundleError:
        raise
    except OSError as exc:
        raise BundleError("build_failed", "build failed before publication to staging") from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def verify_bundle(
    *, bundle: str | Path, repository: str | Path, tolerate_source_drift: bool = False
) -> VerificationResult:
    """Check a bundle against its own bytes and the repository snapshot.

    `tolerate_source_drift` is for the serving path only. A cited file whose
    bytes changed after approval is then reported in `drifted_sources` rather
    than refusing the whole bundle, because retrieval re-checks every chunk's
    source on each query and never serves a drifted one. Every other issue,
    such as a malformed manifest or an unsafe path, still refuses the bundle,
    and the build and sign-off paths never set this.
    """
    root = _repository_root(repository)
    bundle_path = Path(bundle).absolute()
    _reject_symlink_chain(bundle_path, code="invalid_bundle", label="bundle")
    if not bundle_path.is_dir():
        raise BundleError("invalid_bundle", "bundle must be an existing directory")
    try:
        names = {entry.name for entry in bundle_path.iterdir()}
    except OSError as exc:
        raise BundleError("invalid_bundle", "bundle directory could not be read") from exc
    if names != EXPECTED_FILES:
        raise BundleError("invalid_bundle", "bundle has missing or unexpected files")

    manifest_bytes = _read_regular(
        bundle_path / "manifest.json",
        maximum=MAX_MANIFEST_BYTES,
        code="invalid_bundle",
        label="manifest",
        root=bundle_path,
    )
    chunks_bytes = _read_regular(
        bundle_path / "chunks.jsonl",
        maximum=MAX_CHUNKS_BYTES,
        code="invalid_bundle",
        label="chunks",
        root=bundle_path,
    )
    postings_bytes = _read_regular(
        bundle_path / "postings.json",
        maximum=MAX_INDEX_BYTES,
        code="invalid_bundle",
        label="postings",
        root=bundle_path,
    )
    report_bytes = _read_regular(
        bundle_path / "build-report.json",
        maximum=MAX_REPORT_BYTES,
        code="invalid_bundle",
        label="build report",
        root=bundle_path,
    )
    manifest = _load_canonical_object(manifest_bytes, label="manifest")
    report = _load_canonical_object(report_bytes, label="build report")
    _canonical_chunks(chunks_bytes)
    try:
        expected_postings = build_postings(chunks_bytes)
    except RetrievalIndexError as exc:
        raise BundleError("invalid_bundle", "chunks cannot produce a valid bounded index") from exc
    if expected_postings != postings_bytes:
        raise BundleError("invalid_bundle", "postings do not exactly match chunks")

    required_manifest = {
        "chunkCount",
        "chunkDigest",
        "coveredTopics",
        "exclusions",
        "extractedAt",
        "incompleteTopics",
        "indexDigest",
        "indexVersion",
        "postingsDigest",
        "schemaVersion",
        "sourceCommit",
        "sources",
        "status",
    }
    if set(manifest) != required_manifest or manifest.get("status") != "draft":
        raise BundleError("invalid_bundle", "manifest contract is invalid or not draft")
    source_records = manifest.get("sources")
    covered = manifest.get("coveredTopics")
    incomplete = manifest.get("incompleteTopics")
    topic_values = [topic.value for topic in REQUIRED_TOPICS]
    chunk_count = len(chunks_bytes.decode("utf-8").splitlines())
    identity_is_valid = (
        manifest.get("schemaVersion") == "1.0"
        and isinstance(manifest.get("sourceCommit"), str)
        and _COMMIT_PATTERN.fullmatch(str(manifest.get("sourceCommit"))) is not None
        and _is_utc_timestamp(manifest.get("extractedAt"))
        and isinstance(manifest.get("chunkCount"), int)
        and not isinstance(manifest.get("chunkCount"), bool)
        and manifest.get("chunkCount") == chunk_count
        and isinstance(covered, list)
        and isinstance(incomplete, list)
        and all(isinstance(topic, str) for topic in covered + incomplete)
        and covered == [topic for topic in topic_values if topic in covered]
        and incomplete == [topic for topic in topic_values if topic in incomplete]
        and set(covered).isdisjoint(incomplete)
        and set(covered + incomplete) == set(topic_values)
        and isinstance(source_records, list)
        and bool(source_records)
        and source_records
        == sorted(
            source_records,
            key=lambda item: (
                item.get("id", "")
                if isinstance(item, dict) and isinstance(item.get("id"), str)
                else ""
            ),
        )
        and all(
            isinstance(item, dict)
            and set(item) == {"id", "path", "rightsDisposition", "version"}
            and isinstance(item.get("id"), str)
            and isinstance(item.get("path"), str)
            and item.get("rightsDisposition") in {"repository_owned", "approved_open_data"}
            and isinstance(item.get("version"), str)
            and _SHA256_PATTERN.fullmatch(str(item.get("version"))) is not None
            for item in source_records
        )
    )
    if not identity_is_valid:
        raise BundleError("invalid_bundle", "manifest build identity is invalid")
    calculated_index = index_digest(chunks_bytes, postings_bytes)
    if (
        manifest.get("chunkDigest") != _digest(chunks_bytes)
        or manifest.get("postingsDigest") != _digest(postings_bytes)
        or manifest.get("indexDigest") != calculated_index
        or manifest.get("indexVersion") != "draft-lexical-v1"
    ):
        raise BundleError("invalid_bundle", "bundle digest identity does not match exact bytes")
    required_report = {
        "coveredTopics",
        "errors",
        "incompleteTopics",
        "indexDigest",
        "manifestDigest",
        "queryFixtureStatus",
        "status",
    }
    if (
        set(report) != required_report
        or report.get("status") != "draft"
        or report.get("manifestDigest") != _digest(manifest_bytes)
        or report.get("indexDigest") != calculated_index
        or report.get("coveredTopics") != manifest.get("coveredTopics")
        or report.get("incompleteTopics") != manifest.get("incompleteTopics")
        or report.get("errors") != []
        or report.get("queryFixtureStatus") != "not_run_synthetic_fixture_only"
    ):
        raise BundleError("invalid_bundle", "build report does not match exact bundle bytes")

    source_issues = _source_identity_issues(manifest, chunks_bytes, root)
    drifted: tuple[str, ...] = ()
    if source_issues and tolerate_source_drift and all(
        issue.startswith("source:") for issue in source_issues
    ):
        drifted = tuple(issue.removeprefix("source:") for issue in source_issues)
        source_issues = ()
    if source_issues:
        raise BundleError(
            "source_drift",
            "bundle sources do not match the repository snapshot",
            source_issues,
        )
    return VerificationResult(
        valid=True,
        status="draft",
        manifest_digest=_digest(manifest_bytes),
        index_digest=calculated_index,
        drifted_sources=drifted,
    )


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def prepare_receipt(
    *,
    bundle: str | Path,
    repository: str | Path,
    steward: str,
    approved_at: str,
    output: str | Path,
) -> Path:
    """Write the unsigned receipt for a verified bundle. It approves nothing."""
    result = verify_bundle(bundle=bundle, repository=repository)
    bundle_path = Path(bundle).absolute().resolve()
    target = Path(output).absolute()
    _reject_symlink_chain(target, code="unsafe_output", label="receipt output")
    if target.exists() or _inside(target.resolve(strict=False), bundle_path):
        raise BundleError("unsafe_output", "receipt output must be a new file outside the bundle")
    if not target.parent.is_dir():
        raise BundleError("unsafe_output", "receipt output parent must be an existing directory")
    manifest = json.loads((bundle_path / "manifest.json").read_bytes())
    try:
        receipt = ApprovalReceipt(
            schemaVersion="1.0",
            kind="symgov-ed-knowledge-approval",
            decision="approved",
            indexDigest=result.index_digest,
            manifestDigest=result.manifest_digest,
            sourceCommit=manifest["sourceCommit"],
            steward=steward,
            approvedAt=approved_at,
        )
    except ValidationError as exc:
        diagnostics = tuple(
            ".".join(str(part) for part in error.get("loc", ())) or "receipt"
            for error in exc.errors(include_url=False, include_input=False)
        )
        raise BundleError("invalid_input", "receipt fields do not match the strict schema", diagnostics) from exc
    try:
        _write_file(target, _canonical_json(receipt.model_dump(mode="json")))
    except FileExistsError as exc:
        raise BundleError("unsafe_output", "receipt output must be a new file outside the bundle") from exc
    return target


def _ssh_keygen(arguments: list[str], *, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    # Security review L3: a fixed system location, never the caller's PATH.
    executable = shutil.which("ssh-keygen", path="/usr/bin:/bin")
    if executable is None:
        raise BundleError("approval_unavailable", "ssh-keygen is required to verify an approval")
    try:
        return subprocess.run(
            [executable, *arguments],
            input=stdin,
            capture_output=True,
            timeout=_SSH_KEYGEN_TIMEOUT_SECONDS,
            check=False,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BundleError("approval_invalid", "the signature could not be checked") from exc


def _signing_principal(receipt_bytes: bytes, signature: Path, signers: Path) -> str:
    """Return the listed steward whose key signed these exact receipt bytes.

    ssh-keygen output is never echoed: it can name keys and files.
    """
    found = _ssh_keygen(["-Y", "find-principals", "-s", str(signature), "-f", str(signers)])
    principals = [
        line.strip()
        for line in found.stdout.decode("utf-8", "replace").splitlines()
        if line.strip() and len(line.strip()) <= 200 and line.strip().isprintable()
    ]
    if found.returncode != 0 or not principals:
        raise BundleError("approval_invalid", "the receipt is not signed by a listed steward")
    for principal in principals:
        verified = _ssh_keygen(
            [
                "-Y", "verify",
                "-f", str(signers),
                "-I", principal,
                "-n", APPROVAL_NAMESPACE,
                "-s", str(signature),
            ],
            stdin=receipt_bytes,
        )
        if verified.returncode == 0:
            return principal
    raise BundleError("approval_invalid", "the signature does not match this receipt")


def verify_approval(
    *,
    bundle: str | Path,
    repository: str | Path,
    receipt: str | Path,
    signature: str | Path,
    allowed_signers: str | Path,
    tolerate_source_drift: bool = False,
) -> ApprovalResult:
    """Read-only: prove a listed steward signed a receipt for exactly this bundle.

    The allowed-signers file must live outside the repository and the bundle.
    Agents can write to the repository, so a key list there would let an agent
    approve its own work. Where the file does live, and who may change it, is
    server configuration, and that is where the real control sits.
    """
    result = verify_bundle(
        bundle=bundle, repository=repository, tolerate_source_drift=tolerate_source_drift
    )
    root = _repository_root(repository)
    bundle_path = Path(bundle).absolute().resolve()
    signers_path = Path(allowed_signers).absolute()
    _reject_symlink_chain(signers_path, code="unsafe_signers", label="allowed signers")
    if _inside(signers_path.resolve(), root) or _inside(signers_path.resolve(), bundle_path):
        raise BundleError("unsafe_signers", "allowed signers must live outside the repository and the bundle")
    _read_regular(signers_path, maximum=MAX_SIGNERS_BYTES, code="unsafe_signers", label="allowed signers")
    # The signer list decides who may approve, so nobody but its owner may
    # be able to change it (security review L3).
    if signers_path.stat().st_mode & 0o022:
        raise BundleError("unsafe_signers", "allowed signers must not be writable by group or others")
    receipt_path = Path(receipt).absolute()
    signature_path = Path(signature).absolute()
    receipt_bytes = _read_regular(
        receipt_path, maximum=MAX_RECEIPT_BYTES, code="invalid_receipt", label="receipt"
    )
    _read_regular(signature_path, maximum=MAX_SIGNATURE_BYTES, code="invalid_receipt", label="signature")
    try:
        parsed = ApprovalReceipt.model_validate_json(receipt_bytes)
    except ValidationError as exc:
        raise BundleError("invalid_receipt", "receipt does not match the strict schema") from exc
    if _canonical_json(parsed.model_dump(mode="json")) != receipt_bytes:
        raise BundleError("invalid_receipt", "receipt is not canonical")

    principal = _signing_principal(receipt_bytes, signature_path, signers_path)

    manifest = json.loads((bundle_path / "manifest.json").read_bytes())
    if (
        parsed.indexDigest != result.index_digest
        or parsed.manifestDigest != result.manifest_digest
        or parsed.sourceCommit != manifest.get("sourceCommit")
    ):
        raise BundleError("approval_mismatch", "the signed receipt names a different bundle")
    return ApprovalResult(
        principal=principal,
        index_digest=result.index_digest,
        manifest_digest=result.manifest_digest,
        drifted_sources=result.drifted_sources,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or verify an offline, draft-only Ed corpus bundle."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Build a new external draft staging bundle")
    build.add_argument("--repository", required=True)
    build.add_argument("--inventory", required=True)
    build.add_argument("--claims", required=True)
    build.add_argument("--output", required=True)
    build.add_argument("--source-commit", required=True)
    build.add_argument("--extracted-at", required=True)
    verify = commands.add_parser("verify", help="Read-only verification of a draft bundle")
    verify.add_argument("--bundle", required=True)
    verify.add_argument("--repository", required=True)
    prepare = commands.add_parser(
        "prepare-receipt", help="Write the unsigned approval receipt for a verified bundle"
    )
    prepare.add_argument("--bundle", required=True)
    prepare.add_argument("--repository", required=True)
    prepare.add_argument("--steward", required=True)
    prepare.add_argument("--approved-at", required=True)
    prepare.add_argument("--output", required=True)
    approval = commands.add_parser(
        "verify-approval", help="Read-only check of a steward-signed receipt against a bundle"
    )
    approval.add_argument("--bundle", required=True)
    approval.add_argument("--repository", required=True)
    approval.add_argument("--receipt", required=True)
    approval.add_argument("--signature", required=True)
    approval.add_argument("--allowed-signers", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            result = build_bundle(
                repository=args.repository,
                inventory_path=args.inventory,
                claims_path=args.claims,
                output=args.output,
                source_commit=args.source_commit,
                extracted_at=args.extracted_at,
            )
            print(
                f"draft bundle built; manifest={result.manifest_digest}; "
                f"index={result.index_digest}"
            )
        elif args.command == "verify":
            result = verify_bundle(bundle=args.bundle, repository=args.repository)
            print(
                f"draft bundle verified; manifest={result.manifest_digest}; "
                f"index={result.index_digest}"
            )
        elif args.command == "prepare-receipt":
            path = prepare_receipt(
                bundle=args.bundle,
                repository=args.repository,
                steward=args.steward,
                approved_at=args.approved_at,
                output=args.output,
            )
            print(f"unsigned receipt written; sign it off this server: {path}")
        else:
            approved = verify_approval(
                bundle=args.bundle,
                repository=args.repository,
                receipt=args.receipt,
                signature=args.signature,
                allowed_signers=args.allowed_signers,
            )
            print(
                f"approval verified; steward={approved.principal}; "
                f"index={approved.index_digest}"
            )
        return 0
    except BundleError as exc:
        print(f"error[{exc.code}]: {exc}", file=sys.stderr)
        for diagnostic in exc.diagnostics:
            print(f"- {diagnostic}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
