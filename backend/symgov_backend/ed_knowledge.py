from __future__ import annotations

import json
import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class KnowledgeTopic(StrEnum):
    APPLICATION = "application"
    CLASSIFICATION = "classification"
    ORGANIZATION = "organization"
    PROJECT = "project"
    SYMBOL_SET = "symbol_set"
    SYMBOL = "symbol"
    SECURITY = "security"
    USER_SETUP = "user_setup"
    KNOWN_LIMITATIONS = "known_limitations"
    SUPPORT = "support"


class Visibility(StrEnum):
    AUTHENTICATED = "authenticated"
    ORGANIZATION = "organization"
    INTERNAL = "internal"


class ApprovalState(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


REQUIRED_TOPICS = tuple(KnowledgeTopic)
ALLOWED_SOURCE_PREFIXES = (
    "docs/",
    "backend/symgov_backend/",
    "backend/alembic/",
    "frontend/src/",
)
_ID_PATTERN = r"^[a-z][a-z0-9._:-]{2,127}$"
_SENSITIVE_WORDS = {
    "config",
    "configs",
    "credential",
    "credentials",
    "key",
    "keys",
    "private",
    "secret",
    "secrets",
    "token",
    "tokens",
}
_SENSITIVE_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".jks"}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceMetadata(_StrictModel):
    id: str = Field(pattern=_ID_PATTERN)
    title: str = Field(min_length=1, max_length=240)
    topic: KnowledgeTopic
    source_path: str = Field(min_length=1, max_length=500)
    source_version: str = Field(min_length=1, max_length=200)
    extracted_at: datetime
    source_line_start: int | None = Field(default=None, ge=1)
    source_line_end: int | None = Field(default=None, ge=1)
    source_symbol: str | None = Field(default=None, min_length=1, max_length=240)
    visibility: Visibility
    approval_state: ApprovalState
    supersedes: tuple[str, ...] = ()
    superseded_by: tuple[str, ...] = ()

    @field_validator("title", "source_version")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("extracted_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("extracted_at must include a timezone")
        return value

    @field_validator("supersedes", "superseded_by")
    @classmethod
    def validate_reference_shape(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("supersession references must be unique")
        if any(re.fullmatch(_ID_PATTERN, value) is None for value in values):
            raise ValueError("supersession references must contain stable IDs")
        return values

    @model_validator(mode="after")
    def validate_source_range(self) -> SourceMetadata:
        if self.source_line_end is not None and self.source_line_start is None:
            raise ValueError("source_line_start is required when source_line_end is set")
        if (
            self.source_line_start is not None
            and self.source_line_end is not None
            and self.source_line_end < self.source_line_start
        ):
            raise ValueError("source_line_end must not precede source_line_start")
        return self

    @property
    def is_user_retrievable(self) -> bool:
        return (
            self.approval_state is ApprovalState.PUBLISHED
            and self.visibility is not Visibility.INTERNAL
        )


class AtomicClaim(SourceMetadata):
    text: str = Field(min_length=1, max_length=4000)

    @field_validator("text")
    @classmethod
    def reject_blank_claim(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("claim text must not be blank")
        return value


class KnowledgeDocument(SourceMetadata):
    claims: tuple[AtomicClaim, ...] = Field(min_length=1)


class KnowledgeManifest(_StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    knowledge_version: str = Field(min_length=1, max_length=200)
    generated_at: datetime
    documents: tuple[KnowledgeDocument, ...] = Field(min_length=1)

    @field_validator("knowledge_version")
    @classmethod
    def reject_blank_version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("knowledge_version must not be blank")
        return value

    @field_validator("generated_at")
    @classmethod
    def require_generated_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must include a timezone")
        return value


class ValidationIssue(_StrictModel):
    code: str
    reference: str
    message: str


class TopicCoverage(_StrictModel):
    covered: tuple[KnowledgeTopic, ...]
    incomplete: tuple[KnowledgeTopic, ...]

    @property
    def complete(self) -> bool:
        return not self.incomplete


class ValidationReport(_StrictModel):
    valid: bool
    errors: tuple[ValidationIssue, ...]
    coverage: TopicCoverage


def load_manifest(path: str | Path) -> KnowledgeManifest:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return KnowledgeManifest.model_validate(payload)


def _issue(code: str, reference: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, reference=reference, message=message)


def _schema_issue(error: dict[str, Any]) -> ValidationIssue:
    location = tuple(error.get("loc", ()))
    field = str(location[-1]) if location else "manifest"
    error_type = str(error.get("type", ""))
    if error_type == "missing":
        code = "missing_required_value"
    elif field == "topic" and error_type == "enum":
        code = "unsupported_topic"
    elif field == "visibility" and error_type == "enum":
        code = "unsupported_visibility"
    elif field == "approval_state" and error_type == "enum":
        code = "unsupported_approval_state"
    else:
        code = "malformed_value"
    reference = ".".join(str(part) for part in location) or "manifest"
    return _issue(code, reference, str(error.get("msg", "Invalid manifest value")))


def _raw_records(payload: object, key: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    documents = payload.get("documents")
    if not isinstance(documents, list):
        return []
    if key == "documents":
        return [record for record in documents if isinstance(record, dict)]
    claims: list[dict[str, Any]] = []
    for document in documents:
        if not isinstance(document, dict) or not isinstance(document.get("claims"), list):
            continue
        claims.extend(claim for claim in document["claims"] if isinstance(claim, dict))
    return claims


def _duplicate_issues(payload: object) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for record_kind, code in (
        ("documents", "duplicate_document_id"),
        ("claims", "duplicate_claim_id"),
    ):
        seen: set[str] = set()
        duplicates: set[str] = set()
        for record in _raw_records(payload, record_kind):
            identifier = record.get("id")
            if isinstance(identifier, str):
                if identifier in seen:
                    duplicates.add(identifier)
                seen.add(identifier)
        issues.extend(
            _issue(code, identifier, f"Duplicate {record_kind[:-1]} ID: {identifier}")
            for identifier in sorted(duplicates)
        )
    return issues


def _source_path_error(source_path: str) -> str | None:
    if "\\" in source_path:
        return "source paths must use repository-relative POSIX syntax"
    path = PurePosixPath(source_path)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        return "source path must remain inside the repository"
    lowered_parts = tuple(part.lower() for part in path.parts)
    if any(
        part == "settings.local.json"
        or part == ".env"
        or part.startswith(".env.")
        or bool(
            _SENSITIVE_WORDS.intersection(
                word for word in re.split(r"[._-]+", part) if word
            )
        )
        for part in lowered_parts
    ):
        return "source path is excluded as private configuration or key material"
    if path.suffix.lower() in _SENSITIVE_SUFFIXES:
        return "source path is excluded as credential or key material"
    if not any(source_path.startswith(prefix) for prefix in ALLOWED_SOURCE_PREFIXES):
        return "source path is outside the repository knowledge allowlist"
    return None


def source_path_issues(
    source_path: str,
    reference: str,
    repository_root: str | Path | None = None,
) -> tuple[ValidationIssue, ...]:
    """Apply the shared repository knowledge-source boundary to one path."""
    reason = _source_path_error(source_path)
    if reason is not None:
        return (_issue("disallowed_source_path", reference, reason),)
    if repository_root is None:
        return ()

    resolved_root = Path(repository_root).resolve()
    candidate = resolved_root
    for part in PurePosixPath(source_path).parts:
        candidate /= part
        if candidate.is_symlink():
            return (
                _issue(
                    "disallowed_source_path",
                    reference,
                    "source path must not traverse symbolic links",
                ),
            )
    source = (resolved_root / source_path).resolve()
    try:
        source.relative_to(resolved_root)
    except ValueError:
        return (
            _issue(
                "disallowed_source_path",
                reference,
                "source path resolves outside the repository",
            ),
        )
    if not source.is_file():
        return (_issue("missing_source", reference, "allowlisted source file does not exist"),)
    return ()


def _source_issues(
    records: tuple[SourceMetadata, ...], repository_root: Path | None
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for record in records:
        issues.extend(source_path_issues(record.source_path, record.id, repository_root))
    return issues


def _claim_topic_issues(manifest: KnowledgeManifest) -> list[ValidationIssue]:
    return [
        _issue(
            "claim_topic_mismatch",
            claim.id,
            f"claim topic {claim.topic.value} does not match parent topic {document.topic.value}",
        )
        for document in manifest.documents
        for claim in document.claims
        if claim.topic is not document.topic
    ]


def _supersession_issues(
    records: tuple[SourceMetadata, ...], record_kind: str
) -> list[ValidationIssue]:
    identifiers = {record.id for record in records}
    issues: list[ValidationIssue] = []
    for record in records:
        for field_name in ("supersedes", "superseded_by"):
            for reference in getattr(record, field_name):
                if reference == record.id:
                    issues.append(
                        _issue(
                            "self_supersession_reference",
                            record.id,
                            f"{record_kind} cannot {field_name} itself",
                        )
                    )
                elif reference not in identifiers:
                    issues.append(
                        _issue(
                            "broken_supersession_reference",
                            record.id,
                            f"{field_name} references unknown {record_kind} ID: {reference}",
                        )
                    )
    return issues


def _coverage(manifest: KnowledgeManifest | None) -> TopicCoverage:
    covered_set: set[KnowledgeTopic] = set()
    if manifest is not None:
        for document in manifest.documents:
            if not document.is_user_retrievable:
                continue
            if any(
                claim.is_user_retrievable and claim.topic is document.topic
                for claim in document.claims
            ):
                covered_set.add(document.topic)
    covered = tuple(topic for topic in REQUIRED_TOPICS if topic in covered_set)
    incomplete = tuple(topic for topic in REQUIRED_TOPICS if topic not in covered_set)
    return TopicCoverage(covered=covered, incomplete=incomplete)


def validate_manifest(
    value: KnowledgeManifest | dict[str, Any], *, repository_root: str | Path | None = None
) -> ValidationReport:
    raw_value: object = value.model_dump(mode="json") if isinstance(value, KnowledgeManifest) else value
    issues = _duplicate_issues(raw_value)
    manifest: KnowledgeManifest | None
    try:
        manifest = value if isinstance(value, KnowledgeManifest) else KnowledgeManifest.model_validate(value)
    except ValidationError as exc:
        manifest = None
        issues.extend(_schema_issue(error) for error in exc.errors(include_url=False, include_input=False))
    if manifest is not None:
        documents: tuple[SourceMetadata, ...] = tuple(manifest.documents)
        claims: tuple[SourceMetadata, ...] = tuple(
            claim for document in manifest.documents for claim in document.claims
        )
        root = Path(repository_root) if repository_root is not None else None
        issues.extend(_source_issues(documents + claims, root))
        issues.extend(_claim_topic_issues(manifest))
        issues.extend(_supersession_issues(documents, "document"))
        issues.extend(_supersession_issues(claims, "claim"))
    ordered_issues = tuple(sorted(issues, key=lambda item: (item.code, item.reference, item.message)))
    return ValidationReport(
        valid=not ordered_issues,
        errors=ordered_issues,
        coverage=_coverage(manifest),
    )
