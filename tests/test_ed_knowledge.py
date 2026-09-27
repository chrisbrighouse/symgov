from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from symgov_backend.ed_knowledge import (
    ApprovalState,
    KnowledgeManifest,
    KnowledgeTopic,
    Visibility,
    load_manifest,
    validate_manifest,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = (
    REPOSITORY_ROOT
    / "backend"
    / "symgov_backend"
    / "data"
    / "ed_knowledge"
    / "manifest.example.json"
)
REQUIRED_TOPICS = {
    "application",
    "classification",
    "organization",
    "project",
    "symbol_set",
    "symbol",
    "security",
    "user_setup",
    "known_limitations",
    "support",
}
SPEC_SOURCE_VERSION = "sha256:76b64204fb1c844fdc6b3a481736470d6368f037b728ce2ecc49bc9bc13fbb01"


def _fixture_payload() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _error_codes(report) -> set[str]:
    return {issue.code for issue in report.errors}


def test_closed_vocabularies_and_manifest_json_schema_are_versioned():
    assert {topic.value for topic in KnowledgeTopic} == REQUIRED_TOPICS
    assert {value.value for value in Visibility} == {
        "authenticated",
        "organization",
        "internal",
    }
    assert {value.value for value in ApprovalState} == {
        "draft",
        "published",
        "retired",
    }

    schema = KnowledgeManifest.model_json_schema()
    assert schema["properties"]["schema_version"]["const"] == "1.0"
    assert schema["properties"]["knowledge_version"]["minLength"] == 1


def test_representative_manifest_loads_and_reports_covered_and_incomplete_topics():
    manifest = load_manifest(FIXTURE_PATH)

    report = validate_manifest(manifest, repository_root=REPOSITORY_ROOT)

    assert report.valid is True
    assert report.errors == ()
    assert report.coverage.covered == (
        KnowledgeTopic.APPLICATION,
        KnowledgeTopic.SECURITY,
        KnowledgeTopic.SUPPORT,
    )
    assert report.coverage.incomplete == tuple(
        topic for topic in KnowledgeTopic if topic.value not in {"application", "security", "support"}
    )
    assert report.coverage.complete is False
    assert report.model_dump(mode="json") == validate_manifest(
        manifest, repository_root=REPOSITORY_ROOT
    ).model_dump(mode="json")


def test_representative_manifest_truthfully_versions_untracked_spec_bytes():
    payload = _fixture_payload()
    spec_document = next(
        document
        for document in payload["documents"]
        if document["source_path"] == "docs/plans/2026-09-27-ed-application-guru-spec.md"
    )

    assert spec_document["source_version"] == SPEC_SOURCE_VERSION
    assert spec_document["source_version"] != "c9cb088"
    assert {claim["source_version"] for claim in spec_document["claims"]} == {
        SPEC_SOURCE_VERSION
    }


def test_only_published_non_internal_claims_are_user_retrievable():
    payload = _fixture_payload()
    published = payload["documents"][0]["claims"][0]
    draft = copy.deepcopy(published)
    draft["approval_state"] = "draft"
    internal = copy.deepcopy(published)
    internal["visibility"] = "internal"

    assert manifest_claim(published).is_user_retrievable is True
    assert manifest_claim(draft).is_user_retrievable is False
    assert manifest_claim(internal).is_user_retrievable is False


def manifest_claim(claim_payload: dict):
    payload = _fixture_payload()
    payload["documents"][0]["claims"] = [claim_payload]
    return KnowledgeManifest.model_validate(payload).documents[0].claims[0]


@pytest.mark.parametrize(
    "mutation,expected_code",
    [
        (lambda data: data["documents"].append(copy.deepcopy(data["documents"][0])), "duplicate_document_id"),
        (
            lambda data: data["documents"][0]["claims"].append(
                copy.deepcopy(data["documents"][0]["claims"][0])
            ),
            "duplicate_claim_id",
        ),
        (lambda data: data["documents"][0].pop("title"), "missing_required_value"),
        (lambda data: data["documents"][0].update(topic="invented"), "unsupported_topic"),
        (lambda data: data["documents"][0].update(visibility="secret"), "unsupported_visibility"),
        (lambda data: data["documents"][0].update(approval_state="automatic"), "unsupported_approval_state"),
    ],
)
def test_validator_reports_duplicate_malformed_and_unsupported_values(mutation, expected_code):
    payload = _fixture_payload()
    mutation(payload)

    report = validate_manifest(payload, repository_root=REPOSITORY_ROOT)

    assert report.valid is False
    assert expected_code in _error_codes(report)


@pytest.mark.parametrize(
    "source_path",
    [
        ".env",
        ".env.production",
        ".claude/settings.local.json",
        "backend/credentials/service.json",
        "backend/symgov_backend/service_token.py",
        "docs/client-credentials.json",
        "frontend/src/api_token.ts",
        "docs/private.key",
        "outside/guide.md",
        "../docs/guide.md",
        "/etc/passwd",
    ],
)
def test_validator_rejects_private_secret_or_non_allowlisted_source_paths(source_path):
    payload = _fixture_payload()
    payload["documents"][0]["source_path"] = source_path

    report = validate_manifest(payload, repository_root=REPOSITORY_ROOT)

    assert report.valid is False
    assert "disallowed_source_path" in _error_codes(report)


def test_validator_reports_missing_allowlisted_repository_source(tmp_path):
    payload = _fixture_payload()
    payload["documents"][0]["source_path"] = "docs/missing-approved-source.md"

    report = validate_manifest(payload, repository_root=tmp_path)

    assert report.valid is False
    assert "missing_source" in _error_codes(report)


def test_validator_rejects_allowlisted_symlink_to_private_repository_source(tmp_path):
    private_directory = tmp_path / ".claude"
    private_directory.mkdir()
    private_source = private_directory / "settings.local.json"
    private_source.write_text("private configuration", encoding="utf-8")
    docs_directory = tmp_path / "docs"
    docs_directory.mkdir()
    (docs_directory / "approved.md").symlink_to(private_source)
    payload = _fixture_payload()
    payload["documents"][0]["source_path"] = "docs/approved.md"

    report = validate_manifest(payload, repository_root=tmp_path)

    assert any(
        issue.code == "disallowed_source_path"
        and issue.reference == "document:application-overview:v1"
        for issue in report.errors
    )


def test_validator_rejects_claim_topic_that_differs_from_parent_document():
    payload = _fixture_payload()
    payload["documents"][0]["claims"][0]["topic"] = "support"

    report = validate_manifest(payload, repository_root=REPOSITORY_ROOT)

    assert any(
        issue.code == "claim_topic_mismatch"
        and issue.reference == "claim:application-read-only:v1"
        for issue in report.errors
    )
    assert KnowledgeTopic.APPLICATION not in report.coverage.covered
    assert KnowledgeTopic.APPLICATION in report.coverage.incomplete


@pytest.mark.parametrize(
    "target,field,value,expected_code",
    [
        ("document", "supersedes", ["document:missing"], "broken_supersession_reference"),
        ("document", "superseded_by", ["document:application-overview:v1"], "self_supersession_reference"),
        ("claim", "supersedes", ["claim:missing"], "broken_supersession_reference"),
        ("claim", "superseded_by", ["claim:application-read-only:v1"], "self_supersession_reference"),
    ],
)
def test_validator_reports_broken_and_self_supersession_references(target, field, value, expected_code):
    payload = _fixture_payload()
    record = payload["documents"][0]
    if target == "claim":
        record = record["claims"][0]
    record[field] = value

    report = validate_manifest(payload, repository_root=REPOSITORY_ROOT)

    assert report.valid is False
    assert expected_code in _error_codes(report)
