from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from symgov_backend.ed_knowledge import (
    ApprovalState,
    AtomicClaim,
    KnowledgeDocument,
    KnowledgeManifest,
    KnowledgeTopic,
    REQUIRED_TOPICS,
    ValidationIssue,
    Visibility,
    validate_manifest,
)
from symgov_backend.ed_knowledge_sources import (
    SOURCE_ADAPTERS,
    SourceInventory,
    SourceInventoryEntry,
    source_locator,
    validate_source_inventory,
)
from symgov_backend.ed_retrieval import MAX_SOURCE_BYTES as RETRIEVAL_MAX_SOURCE_BYTES

# These are draft-build safety ceilings, not production capacity promises.
# The source ceiling is shared with retrieval and verify (decision 7.4).
MAX_SOURCE_BYTES = RETRIEVAL_MAX_SOURCE_BYTES
MAX_UNIT_BYTES = 16_384
MAX_CLAIM_CHARS = 1_000
MAX_CLAIMS = 100
MAX_ERRORS = 100
_SHA256_VERSION = re.compile(r"^sha256:[0-9a-f]{64}$")
_CREDENTIAL_NAME = rb"(?:api[_-]?(?:key|token)|access[_-]?token|auth[_-]?token|password|passwd|secret|token)"
# Credential material, not code that merely names a credential. The first
# version flagged `token = request.cookies.get(...)`, which rejected four of
# the plan's fifteen candidate sources. A value now has to be a quoted
# literal, or a bare run of key characters that mixes letters with digits
# and is not followed by an attribute access or call; provider token
# prefixes are flagged wherever they appear.
_SECRET_LIKE = re.compile(
    rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    rb"|(?i:" + _CREDENTIAL_NAME + rb"['\"]?\s*[:=]\s*['\"][^'\"\s]{12,}['\"])"
    rb"|(?i:" + _CREDENTIAL_NAME + rb"\s*[:=]\s*"
    rb"(?=[A-Za-z0-9_+/=-]*[0-9])(?=[A-Za-z0-9_+/=-]*[A-Za-z])"
    rb"[A-Za-z0-9_+/=-]{20,}(?![A-Za-z0-9_+/=.(-]))"
    rb"|\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"
    rb"|\bgh[pousr]_[A-Za-z0-9]{20,}"
    rb"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    rb"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    rb"|\bAKIA[0-9A-Z]{16}\b"
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RightsDisposition(StrEnum):
    REPOSITORY_OWNED = "repository_owned"
    APPROVED_OPEN_DATA = "approved_open_data"


class SourceApproval(_StrictModel):
    source_id: str = Field(min_length=3, max_length=128)
    rights_disposition: RightsDisposition
    approved_line_start: int | None = Field(default=None, ge=1)
    approved_line_end: int | None = Field(default=None, ge=1)
    approved_symbol: str | None = Field(default=None, min_length=1, max_length=240)


class ClaimMetadata(_StrictModel):
    claim_id: str = Field(min_length=3, max_length=128)
    fact_key: str = Field(pattern=r"^[a-z][a-z0-9._-]{2,127}$")
    scope: Literal["product"] = "product"


class DraftCorpusRequest(_StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    extracted_at: datetime
    inventory: SourceInventory
    manifest: KnowledgeManifest
    source_approvals: tuple[SourceApproval, ...] = Field(min_length=1)
    claim_metadata: tuple[ClaimMetadata, ...] = Field(min_length=1)

    @field_validator("extracted_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("extracted_at must include a timezone")
        if value.utcoffset().total_seconds() != 0:
            raise ValueError("extracted_at must be UTC")
        return value


class DraftCorpusBuild(_StrictModel):
    manifest_bytes: bytes
    manifest_digest: str
    chunks_bytes: bytes
    report_bytes: bytes
    incomplete_topics: tuple[KnowledgeTopic, ...]


class CorpusValidationError(ValueError):
    def __init__(self, issues: tuple[ValidationIssue, ...]):
        self.issues = issues
        super().__init__("draft corpus validation failed")


def _issue(code: str, reference: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, reference=reference, message=message)


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _bounded(issues: list[ValidationIssue]) -> tuple[ValidationIssue, ...]:
    ordered = sorted(issues, key=lambda item: (item.code, item.reference, item.message))
    return tuple(ordered[:MAX_ERRORS])


def _duplicates(values: list[str], code: str) -> list[ValidationIssue]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return [_issue(code, value, "identifier must be globally unique") for value in sorted(duplicates)]


def _supersession_issues(records: tuple[KnowledgeDocument | AtomicClaim, ...]) -> list[ValidationIssue]:
    by_id = {record.id: record for record in records}
    issues: list[ValidationIssue] = []
    for record in records:
        for older_id in record.supersedes:
            older = by_id.get(older_id)
            if older is not None and record.id not in older.superseded_by:
                issues.append(
                    _issue(
                        "nonreciprocal_supersession",
                        record.id,
                        "supersedes and superseded_by must be reciprocal",
                    )
                )
        for newer_id in record.superseded_by:
            newer = by_id.get(newer_id)
            if newer is not None and record.id not in newer.supersedes:
                issues.append(
                    _issue(
                        "nonreciprocal_supersession",
                        record.id,
                        "supersedes and superseded_by must be reciprocal",
                    )
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visiting:
            issues.append(
                _issue("cyclic_supersession", identifier, "supersession graph must be acyclic")
            )
            return
        if identifier in visited:
            return
        visiting.add(identifier)
        record = by_id[identifier]
        for older_id in record.supersedes:
            if older_id in by_id:
                visit(older_id)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in sorted(by_id):
        visit(identifier)
    return issues


def _source_bytes(
    entry: SourceInventoryEntry, root: Path, issues: list[ValidationIssue]
) -> bytes | None:
    path = root / entry.source_path
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_SOURCE_BYTES + 1)
    except OSError:
        issues.append(
            _issue("unreadable_source", entry.id, "reviewed source could not be read")
        )
        return None
    if len(data) > MAX_SOURCE_BYTES:
        issues.append(
            _issue(
                "source_too_large",
                entry.id,
                f"source exceeds the {MAX_SOURCE_BYTES}-byte draft limit",
            )
        )
        return None
    if not _SHA256_VERSION.fullmatch(entry.source_version):
        issues.append(
            _issue("invalid_source_version", entry.id, "source version must be a SHA-256 digest")
        )
    elif entry.source_version != f"sha256:{hashlib.sha256(data).hexdigest()}":
        issues.append(
            _issue("source_hash_mismatch", entry.id, "source bytes do not match the reviewed digest")
        )
    if _SECRET_LIKE.search(data):
        issues.append(
            _issue("secret_like_source", entry.id, "source contains credential-like material")
        )
    return data


def _source_evidence(
    entry: SourceInventoryEntry,
    approval: SourceApproval | None,
    data: bytes,
) -> tuple[list[ValidationIssue], tuple[int, int] | None]:
    """Validate a source's reviewed evidence and resolve it to exact lines.

    A heading or symbol is resolved here, once, to the line range of the
    unit it names. Chunks carry that range, so retrieval and `verify` only
    need the pinned bytes and the range, and never re-derive a heading slug.
    """
    issues: list[ValidationIssue] = []
    if approval is None:
        return (
            [_issue("missing_source_approval", entry.id, "source requires rights and range approval")],
            None,
        )
    if not isinstance(approval.rights_disposition, RightsDisposition):
        issues.append(_issue("unsupported_rights", entry.id, "source rights are not approved"))
    has_range = entry.source_line_start is not None and entry.source_line_end is not None
    symbol = entry.source_symbol or entry.source_heading
    if not has_range and symbol is None:
        issues.append(
            _issue("missing_source_evidence", entry.id, "source requires a line range or reviewed symbol")
        )
        return issues, None

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        issues.append(
            _issue("invalid_source_encoding", entry.id, "reviewed source is not valid UTF-8")
        )
        return issues, None

    line_count = len(text.splitlines())
    if has_range and (
        entry.source_line_start > line_count or entry.source_line_end > line_count
    ):
        issues.append(_issue("invalid_source_range", entry.id, "source line range is outside the file"))
    if has_range and (
        approval.approved_line_start != entry.source_line_start
        or approval.approved_line_end != entry.source_line_end
    ):
        issues.append(_issue("unapproved_source_range", entry.id, "source range differs from approval"))
    if approval.approved_symbol != symbol:
        issues.append(_issue("unapproved_source_symbol", entry.id, "source symbol differs from approval"))

    adapter = SOURCE_ADAPTERS[entry.source_kind]
    if adapter is None:
        issues.append(
            _issue(
                "adapter_not_supported",
                entry.id,
                f"{entry.source_kind.value} source adapter is not yet supported",
            )
        )
        return issues, None
    extraction = adapter(entry, text)
    issues.extend(extraction.errors)
    matching_units = [
        unit
        for unit in extraction.units
        if (
            symbol is None
            or unit.source_symbol == symbol
            or unit.source_heading == symbol
        )
        and (
            not has_range
            or (
                unit.source_line_start <= entry.source_line_start
                and unit.source_line_end >= entry.source_line_end
            )
        )
    ]
    if symbol is not None and not matching_units:
        issues.append(_issue("source_symbol_not_found", entry.id, "reviewed source symbol was not found"))
        return issues, None
    if has_range:
        resolved = (entry.source_line_start, entry.source_line_end)
    else:
        ranges = {(unit.source_line_start, unit.source_line_end) for unit in matching_units}
        if len(ranges) != 1:
            issues.append(
                _issue("ambiguous_source_symbol", entry.id, "reviewed source symbol names more than one unit")
            )
            return issues, None
        resolved = ranges.pop()
    lines = data.splitlines(keepends=True)
    selected = b"".join(lines[resolved[0] - 1 : resolved[1]])
    if len(selected) > MAX_UNIT_BYTES:
        issues.append(
            _issue(
                "unit_too_large",
                entry.id,
                f"reviewed unit exceeds the {MAX_UNIT_BYTES}-byte draft limit",
            )
        )
    return issues, resolved


def _document_locator(document: KnowledgeDocument) -> tuple[object, ...]:
    return (
        document.source_path,
        document.source_line_start,
        document.source_line_end,
        document.source_symbol,
    )


def _identity_issues(
    request: DraftCorpusRequest, source_by_locator: dict[tuple[object, ...], SourceInventoryEntry]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    metadata_by_claim = {item.claim_id: item for item in request.claim_metadata}
    inventoried_paths = {locator[0] for locator in source_by_locator}
    for document in request.manifest.documents:
        if document.source_path not in inventoried_paths:
            issues.append(_issue("unknown_document_source", document.id, "document source is not inventoried"))
            continue
        source = source_by_locator.get(_document_locator(document))
        if source is None or any(
            (
                document.source_version != source.source_version,
                document.topic is not source.topic,
            )
        ):
            issues.append(_issue("document_source_mismatch", document.id, "document source identity differs from inventory"))
        if document.approval_state is not ApprovalState.DRAFT:
            issues.append(_issue("non_draft_record", document.id, "draft builds accept draft records only"))
        if document.visibility is not Visibility.AUTHENTICATED:
            issues.append(_issue("unsupported_draft_visibility", document.id, "first draft supports authenticated visibility only"))
        for claim in document.claims:
            if any(
                (
                    claim.source_path != document.source_path,
                    claim.source_version != document.source_version,
                    claim.topic is not document.topic,
                    claim.source_line_start != document.source_line_start,
                    claim.source_line_end != document.source_line_end,
                    claim.source_symbol != document.source_symbol,
                )
            ):
                issues.append(_issue("claim_source_mismatch", claim.id, "claim evidence must match its parent document"))
            if claim.approval_state is not ApprovalState.DRAFT:
                issues.append(_issue("non_draft_record", claim.id, "draft builds accept draft records only"))
            if claim.visibility is not Visibility.AUTHENTICATED:
                issues.append(_issue("unsupported_draft_visibility", claim.id, "first draft supports authenticated visibility only"))
            if len(claim.text) > MAX_CLAIM_CHARS:
                issues.append(
                    _issue(
                        "claim_too_large",
                        claim.id,
                        f"claim exceeds the {MAX_CLAIM_CHARS}-character draft limit",
                    )
                )
            if _SECRET_LIKE.search(claim.text.encode("utf-8")) or _SECRET_LIKE.search(
                claim.title.encode("utf-8")
            ):
                issues.append(
                    _issue(
                        "secret_like_claim",
                        claim.id,
                        "claim output contains credential-like material",
                    )
                )
            if claim.id not in metadata_by_claim:
                issues.append(_issue("missing_claim_metadata", claim.id, "claim requires a fact key and scope"))
    claim_ids = {
        claim.id for document in request.manifest.documents for claim in document.claims
    }
    for metadata in request.claim_metadata:
        if metadata.claim_id not in claim_ids:
            issues.append(_issue("orphan_claim_metadata", metadata.claim_id, "claim metadata has no claim"))
    return issues


def _fact_key_issues(request: DraftCorpusRequest) -> list[ValidationIssue]:
    claims = {
        claim.id: claim for document in request.manifest.documents for claim in document.claims
    }
    grouped: dict[str, list[AtomicClaim]] = {}
    for metadata in request.claim_metadata:
        claim = claims.get(metadata.claim_id)
        if claim is not None:
            grouped.setdefault(metadata.fact_key, []).append(claim)
    issues: list[ValidationIssue] = []
    for fact_key, versions in grouped.items():
        active = [claim for claim in versions if not claim.superseded_by]
        if len(active) > 1:
            issues.append(
                _issue(
                    "contradictory_fact_key",
                    fact_key,
                    "a fact key must have only one active draft claim",
                )
            )
    return issues


def _validate(
    request: DraftCorpusRequest, root: Path
) -> tuple[tuple[ValidationIssue, ...], dict[str, tuple[int, int]]]:
    """Return the bounded issues and each valid source locator's resolved line range."""
    issues: list[ValidationIssue] = []
    resolved: dict[tuple[object, ...], tuple[int, int]] = {}
    inventory_report = validate_source_inventory(request.inventory, repository_root=root)
    issues.extend(inventory_report.errors)
    manifest_report = validate_manifest(request.manifest, repository_root=root)
    issues.extend(manifest_report.errors)

    sources = request.inventory.sources
    documents = request.manifest.documents
    claims = tuple(claim for document in documents for claim in document.claims)
    issues.extend(
        _duplicates(
            [entry.id for entry in sources]
            + [document.id for document in documents]
            + [claim.id for claim in claims],
            "duplicate_global_id",
        )
    )
    if len(claims) > MAX_CLAIMS:
        issues.append(_issue("too_many_claims", "manifest", f"draft exceeds {MAX_CLAIMS} claims"))

    approvals: dict[str, SourceApproval] = {}
    for approval in request.source_approvals:
        if approval.source_id in approvals:
            issues.append(_issue("duplicate_source_approval", approval.source_id, "source approval is duplicated"))
        approvals[approval.source_id] = approval
    metadata_ids = [metadata.claim_id for metadata in request.claim_metadata]
    issues.extend(_duplicates(metadata_ids, "duplicate_claim_metadata"))

    source_by_locator = {source_locator(entry): entry for entry in sources}
    issues.extend(_identity_issues(request, source_by_locator))
    issues.extend(_fact_key_issues(request))
    issues.extend(_supersession_issues(tuple(documents)))
    issues.extend(_supersession_issues(claims))

    invalid_path_refs = {
        issue.reference
        for issue in inventory_report.errors
        if issue.code in {"disallowed_source_path", "missing_source"}
    }
    for entry in sources:
        if entry.id in invalid_path_refs:
            continue
        data = _source_bytes(entry, root, issues)
        if data is not None:
            evidence_issues, lines = _source_evidence(entry, approvals.get(entry.id), data)
            issues.extend(evidence_issues)
            if lines is not None:
                resolved[source_locator(entry)] = lines
    return _bounded(issues), resolved


def build_draft_corpus(
    request: DraftCorpusRequest, *, repository_root: str | Path
) -> DraftCorpusBuild:
    root = Path(repository_root).resolve()
    issues, resolved = _validate(request, root)
    if issues:
        raise CorpusValidationError(issues)

    metadata = {item.claim_id: item for item in request.claim_metadata}
    # A claim's evidence was checked above to equal its document's, and the
    # document's to equal one inventoried locator.
    claims = sorted(
        (
            (claim, _document_locator(document))
            for document in request.manifest.documents
            for claim in document.claims
        ),
        key=lambda pair: pair[0].id,
    )
    chunks = []
    for claim, locator in claims:
        item = metadata[claim.id]
        line_start, line_end = resolved[locator]
        # No approval state: approval is a property of the whole bundle,
        # bound to its index digest, and a steward approves these exact
        # bytes. A per-chunk field could only ever say "draft", and anyone
        # able to edit it could otherwise claim publication.
        chunks.append(
            {
                "extractedAt": _utc_text(request.extracted_at),
                "factKey": item.fact_key,
                "id": claim.id,
                "scope": item.scope,
                "sourceLineEnd": line_end,
                "sourceLineStart": line_start,
                "sourcePath": claim.source_path,
                "sourceSymbol": claim.source_symbol,
                "sourceVersion": claim.source_version,
                "supersededBy": sorted(claim.superseded_by),
                "text": claim.text,
                "title": claim.title,
                "topic": claim.topic.value,
                "visibility": claim.visibility.value,
            }
        )
    chunks_bytes = b"".join(_canonical_json(chunk) for chunk in chunks)
    covered_set = {claim.topic for claim, _locator in claims}
    covered = [topic.value for topic in REQUIRED_TOPICS if topic in covered_set]
    incomplete = tuple(topic for topic in REQUIRED_TOPICS if topic not in covered_set)
    report = {
        "coveredTopics": covered,
        "errors": [],
        "incompleteTopics": [topic.value for topic in incomplete],
        "status": "draft",
    }
    manifest = {
        "chunkCount": len(chunks),
        "chunkDigest": f"sha256:{hashlib.sha256(chunks_bytes).hexdigest()}",
        "coveredTopics": covered,
        "exclusions": [
            "credentials",
            "customer_documents",
            "external_sources",
            "raw_security_configuration",
            "standards_documents",
        ],
        "extractedAt": _utc_text(request.extracted_at),
        "incompleteTopics": [topic.value for topic in incomplete],
        "indexVersion": "draft-chunks-v1",
        "schemaVersion": "1.0",
        "sourceCommit": request.source_commit,
        "sources": [
            {
                "id": entry.id,
                "path": entry.source_path,
                "rightsDisposition": next(
                    approval.rights_disposition.value
                    for approval in request.source_approvals
                    if approval.source_id == entry.id
                ),
                "version": entry.source_version,
            }
            for entry in sorted(request.inventory.sources, key=lambda item: item.id)
        ],
        "status": "draft",
    }
    manifest_bytes = _canonical_json(manifest)
    return DraftCorpusBuild(
        manifest_bytes=manifest_bytes,
        manifest_digest=f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}",
        chunks_bytes=chunks_bytes,
        report_bytes=_canonical_json(report),
        incomplete_topics=incomplete,
    )
