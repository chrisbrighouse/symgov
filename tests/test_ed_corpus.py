from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import pytest


def _module():
    return importlib.import_module("symgov_backend.ed_corpus")


def _write_source(root: Path, content: str = "# Guide\nEd is read-only.\n") -> tuple[str, str]:
    relative = "docs/guide.md"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return relative, f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _request(root: Path, *, content: str = "# Guide\nEd is read-only.\n"):
    module = _module()
    source_path, source_version = _write_source(root, content)
    inventory = {
        "schema_version": "1.0",
        "inventory_version": "draft:test",
        "sources": [
            {
                "id": "source:guide:v1",
                "title": "Guide",
                "topic": "application",
                "source_path": source_path,
                "source_version": source_version,
                "source_kind": "markdown",
                "source_line_start": 1,
                "source_line_end": 2,
                "source_heading": "guide",
            }
        ],
    }
    source_record = {
        "id": "document:guide:v1",
        "title": "Guide",
        "topic": "application",
        "source_path": source_path,
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
    claim = {
        **source_record,
        "id": "claim:guide-read-only:v1",
        "text": "Ed's first phase is read-only.",
    }
    manifest = {
        "schema_version": "1.0",
        "knowledge_version": "draft:test",
        "generated_at": "2026-09-29T12:00:00Z",
        "documents": [{**source_record, "claims": [claim]}],
    }
    return module.DraftCorpusRequest.model_validate(
        {
            "source_commit": "a" * 40,
            "extracted_at": "2026-09-29T12:00:00Z",
            "inventory": inventory,
            "manifest": manifest,
            "source_approvals": [
                {
                    "source_id": "source:guide:v1",
                    "rights_disposition": "repository_owned",
                    "approved_line_start": 1,
                    "approved_line_end": 2,
                    "approved_symbol": "guide",
                }
            ],
            "claim_metadata": [
                {
                    "claim_id": "claim:guide-read-only:v1",
                    "fact_key": "application.ed.read-only",
                    "scope": "product",
                }
            ],
        }
    )


def _codes(exc: pytest.ExceptionInfo) -> set[str]:
    return {issue.code for issue in exc.value.issues}


def test_build_is_draft_only_deterministic_and_reports_incomplete_topics(tmp_path: Path):
    module = _module()
    request = _request(tmp_path)

    first = module.build_draft_corpus(request, repository_root=tmp_path)
    second = module.build_draft_corpus(request, repository_root=tmp_path)

    assert first.manifest_bytes == second.manifest_bytes
    assert first.chunks_bytes == second.chunks_bytes
    assert first.report_bytes == second.report_bytes
    manifest = json.loads(first.manifest_bytes)
    chunks = [json.loads(line) for line in first.chunks_bytes.decode().splitlines()]
    report = json.loads(first.report_bytes)
    assert manifest["status"] == "draft"
    assert manifest["sourceCommit"] == "a" * 40
    assert manifest["indexVersion"] == "draft-chunks-v1"
    assert manifest["coveredTopics"] == ["application"]
    assert manifest["incompleteTopics"] == report["incompleteTopics"]
    assert manifest["exclusions"] == [
        "credentials",
        "customer_documents",
        "external_sources",
        "raw_security_configuration",
        "standards_documents",
    ]
    assert manifest["chunkDigest"] == f"sha256:{hashlib.sha256(first.chunks_bytes).hexdigest()}"
    assert first.manifest_digest == f"sha256:{hashlib.sha256(first.manifest_bytes).hexdigest()}"
    assert chunks == [
        {
            "extractedAt": "2026-09-29T12:00:00Z",
            "factKey": "application.ed.read-only",
            "id": "claim:guide-read-only:v1",
            "scope": "product",
            "sourceLineEnd": 2,
            "sourceLineStart": 1,
            "sourcePath": "docs/guide.md",
            "sourceSymbol": "guide",
            "sourceVersion": request.inventory.sources[0].source_version,
            "supersededBy": [],
            "text": "Ed's first phase is read-only.",
            "title": "Guide",
            "topic": "application",
            "visibility": "authenticated",
        }
    ]
    assert report["coveredTopics"] == ["application"]
    assert report["incompleteTopics"] == [
        "classification",
        "organization",
        "project",
        "symbol_set",
        "symbol",
        "security",
        "user_setup",
        "known_limitations",
        "support",
    ]
    assert b"Ed is read-only" not in first.chunks_bytes


@pytest.mark.parametrize("problem", ["changed", "missing"])
def test_build_rejects_changed_or_missing_source_hash(tmp_path: Path, problem: str):
    module = _module()
    request = _request(tmp_path)
    source = tmp_path / request.inventory.sources[0].source_path
    if problem == "changed":
        source.write_text("# Guide\nChanged bytes.\n", encoding="utf-8")
    else:
        source.unlink()

    with pytest.raises(module.CorpusValidationError) as exc:
        module.build_draft_corpus(request, repository_root=tmp_path)

    expected = "source_hash_mismatch" if problem == "changed" else "missing_source"
    assert expected in _codes(exc)


@pytest.mark.parametrize("mutation", ["range", "symbol", "missing_evidence"])
def test_build_rejects_invalid_or_missing_range_and_symbol_evidence(tmp_path: Path, mutation: str):
    module = _module()
    request = _request(tmp_path)
    source = request.inventory.sources[0]
    updates: dict[str, object] = {}
    if mutation == "range":
        updates = {"source_line_start": 3, "source_line_end": 3}
    elif mutation == "symbol":
        updates = {"source_heading": "missing-heading"}
    else:
        updates = {
            "source_line_start": None,
            "source_line_end": None,
            "source_heading": None,
            "source_symbol": None,
        }
    request = request.model_copy(
        update={"inventory": request.inventory.model_copy(update={"sources": (source.model_copy(update=updates),)})}
    )

    with pytest.raises(module.CorpusValidationError) as exc:
        module.build_draft_corpus(request, repository_root=tmp_path)

    assert _codes(exc) & {"invalid_source_range", "source_symbol_not_found", "missing_source_evidence"}


@pytest.mark.parametrize("problem", ["root", "unallowlisted", "symlink"])
def test_build_rejects_root_unallowlisted_and_symlink_paths(tmp_path: Path, problem: str):
    module = _module()
    request = _request(tmp_path)
    entry = request.inventory.sources[0]
    if problem == "root":
        path = "guide.md"
        (tmp_path / path).write_text("# Guide\nEd is read-only.\n", encoding="utf-8")
    elif problem == "unallowlisted":
        path = "other/guide.md"
        (tmp_path / "other").mkdir()
        (tmp_path / path).write_text("# Guide\nEd is read-only.\n", encoding="utf-8")
    else:
        private = tmp_path / "private.md"
        private.write_text("private", encoding="utf-8")
        path = "docs/linked.md"
        (tmp_path / path).symlink_to(private)
    version = f"sha256:{hashlib.sha256((tmp_path / path).read_bytes()).hexdigest()}"
    request = request.model_copy(
        update={"inventory": request.inventory.model_copy(update={"sources": (entry.model_copy(update={"source_path": path, "source_version": version}),)})}
    )

    with pytest.raises(module.CorpusValidationError) as exc:
        module.build_draft_corpus(request, repository_root=tmp_path)

    assert "disallowed_source_path" in _codes(exc)


@pytest.mark.parametrize("problem", ["secret", "rights"])
def test_build_fails_closed_for_secret_like_bytes_and_unsupported_rights(tmp_path: Path, problem: str):
    module = _module()
    if problem == "secret":
        request = _request(tmp_path, content="# Guide\nAPI_TOKEN=super-secret-value-123456789\n")
    else:
        request = _request(tmp_path)
        approval = request.source_approvals[0].model_copy(update={"rights_disposition": "unknown"})
        request = request.model_copy(update={"source_approvals": (approval,)})

    with pytest.raises((module.CorpusValidationError, ValueError)) as exc:
        module.build_draft_corpus(request, repository_root=tmp_path)

    if isinstance(exc.value, module.CorpusValidationError):
        assert _codes(exc) & {"secret_like_source", "unsupported_rights"}


def test_build_rejects_duplicate_global_ids_and_contradictory_claim_fact_keys(tmp_path: Path):
    module = _module()
    request = _request(tmp_path)
    document = request.manifest.documents[0]
    claim = document.claims[0]
    second = claim.model_copy(update={"id": "claim:guide-read-only:v2", "text": "Ed can mutate data."})
    manifest = request.manifest.model_copy(
        update={"documents": (document.model_copy(update={"claims": (claim, second)}),)}
    )
    metadata = (*request.claim_metadata, request.claim_metadata[0].model_copy(update={"claim_id": second.id}))
    request = request.model_copy(update={"manifest": manifest, "claim_metadata": metadata})

    with pytest.raises(module.CorpusValidationError) as exc:
        module.build_draft_corpus(request, repository_root=tmp_path)

    assert "contradictory_fact_key" in _codes(exc)


def test_build_rejects_identifier_reuse_across_document_and_claim_records(tmp_path: Path):
    module = _module()
    request = _request(tmp_path)
    document = request.manifest.documents[0]
    claim = document.claims[0].model_copy(update={"id": document.id})
    manifest = request.manifest.model_copy(
        update={"documents": (document.model_copy(update={"claims": (claim,)}),)}
    )
    metadata = request.claim_metadata[0].model_copy(update={"claim_id": claim.id})
    request = request.model_copy(update={"manifest": manifest, "claim_metadata": (metadata,)})

    with pytest.raises(module.CorpusValidationError) as exc:
        module.build_draft_corpus(request, repository_root=tmp_path)

    assert "duplicate_global_id" in _codes(exc)


@pytest.mark.parametrize("field", ["source_path", "source_version", "source_line_end"])
def test_build_rejects_claim_evidence_that_differs_from_parent(tmp_path: Path, field: str):
    module = _module()
    request = _request(tmp_path)
    document = request.manifest.documents[0]
    replacements = {
        "source_path": "docs/other.md",
        "source_version": "sha256:" + "0" * 64,
        "source_line_end": 1,
    }
    claim = document.claims[0].model_copy(update={field: replacements[field]})
    request = request.model_copy(
        update={
            "manifest": request.manifest.model_copy(
                update={"documents": (document.model_copy(update={"claims": (claim,)}),)}
            )
        }
    )

    with pytest.raises(module.CorpusValidationError) as exc:
        module.build_draft_corpus(request, repository_root=tmp_path)

    assert "claim_source_mismatch" in _codes(exc)


@pytest.mark.parametrize(
    ("updates", "expected_code"),
    [
        ({"source_line_start": 2, "source_line_end": 2}, "document_source_mismatch"),
        ({"source_symbol": "different-heading"}, "document_source_mismatch"),
    ],
)
def test_build_rejects_parent_and_claim_locator_that_differs_from_reviewed_source(
    tmp_path: Path, updates: dict[str, object], expected_code: str
):
    module = _module()
    request = _request(tmp_path)
    document = request.manifest.documents[0]
    claim = document.claims[0].model_copy(update=updates)
    document = document.model_copy(update={**updates, "claims": (claim,)})
    request = request.model_copy(
        update={
            "manifest": request.manifest.model_copy(update={"documents": (document,)})
        }
    )

    with pytest.raises(module.CorpusValidationError) as exc:
        module.build_draft_corpus(request, repository_root=tmp_path)

    assert expected_code in _codes(exc)


@pytest.mark.parametrize("field", ["text", "title"])
def test_build_rejects_secret_like_claim_output_without_echoing_value(
    tmp_path: Path, field: str
):
    module = _module()
    request = _request(tmp_path)
    document = request.manifest.documents[0]
    secret = "API_TOKEN=claim-secret-value-123456789"
    claim = document.claims[0].model_copy(update={field: secret})
    request = request.model_copy(
        update={
            "manifest": request.manifest.model_copy(
                update={"documents": (document.model_copy(update={"claims": (claim,)}),)}
            )
        }
    )

    with pytest.raises(module.CorpusValidationError) as exc:
        module.build_draft_corpus(request, repository_root=tmp_path)

    assert "secret_like_claim" in _codes(exc)
    assert secret not in " ".join(issue.message for issue in exc.value.issues)


def test_build_fails_closed_when_reviewed_source_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _module()
    request = _request(tmp_path)
    source_path = tmp_path / request.inventory.sources[0].source_path
    original_open = Path.open

    def fail_source_read(self: Path, *args, **kwargs):
        if self == source_path and args and args[0] == "rb":
            raise OSError("simulated source read failure")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_source_read)

    with pytest.raises(module.CorpusValidationError) as exc:
        module.build_draft_corpus(request, repository_root=tmp_path)

    assert "unreadable_source" in _codes(exc)


def test_build_extracts_from_the_single_bounded_validated_source_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _module()
    request = _request(tmp_path)
    source_path = tmp_path / request.inventory.sources[0].source_path
    original_open = Path.open
    source_reads = 0

    def count_source_reads(self: Path, *args, **kwargs):
        nonlocal source_reads
        if self == source_path:
            source_reads += 1
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", count_source_reads)

    module.build_draft_corpus(request, repository_root=tmp_path)

    assert source_reads == 1


def test_build_reports_invalid_source_encoding_as_bounded_validation(
    tmp_path: Path,
):
    module = _module()
    request = _request(tmp_path)
    source_path = tmp_path / request.inventory.sources[0].source_path
    invalid_bytes = b"# Guide\n\xff\xfe\n"
    source_path.write_bytes(invalid_bytes)
    source_version = f"sha256:{hashlib.sha256(invalid_bytes).hexdigest()}"
    entry = request.inventory.sources[0].model_copy(update={"source_version": source_version})
    document = request.manifest.documents[0]
    claim = document.claims[0].model_copy(update={"source_version": source_version})
    document = document.model_copy(
        update={"source_version": source_version, "claims": (claim,)}
    )
    request = request.model_copy(
        update={
            "inventory": request.inventory.model_copy(update={"sources": (entry,)}),
            "manifest": request.manifest.model_copy(update={"documents": (document,)}),
        }
    )

    with pytest.raises(module.CorpusValidationError) as exc:
        module.build_draft_corpus(request, repository_root=tmp_path)

    assert "invalid_source_encoding" in _codes(exc)


def test_build_rejects_nonreciprocal_and_cyclic_supersession(tmp_path: Path):
    module = _module()
    request = _request(tmp_path)
    document = request.manifest.documents[0]
    first = document.claims[0].model_copy(
        update={"superseded_by": ("claim:guide-read-only:v2",)}
    )
    second = document.claims[0].model_copy(
        update={
            "id": "claim:guide-read-only:v2",
            "supersedes": (first.id,),
            "superseded_by": (first.id,),
        }
    )
    first = first.model_copy(update={"supersedes": (second.id,)})
    manifest = request.manifest.model_copy(
        update={"documents": (document.model_copy(update={"claims": (first, second)}),)}
    )
    metadata = (
        request.claim_metadata[0],
        request.claim_metadata[0].model_copy(
            update={"claim_id": second.id, "fact_key": "application.ed.read-only.v2"}
        ),
    )
    request = request.model_copy(update={"manifest": manifest, "claim_metadata": metadata})

    with pytest.raises(module.CorpusValidationError) as exc:
        module.build_draft_corpus(request, repository_root=tmp_path)

    assert "cyclic_supersession" in _codes(exc)

    second = second.model_copy(update={"supersedes": (), "superseded_by": ()})
    request = request.model_copy(
        update={
            "manifest": manifest.model_copy(
                update={"documents": (document.model_copy(update={"claims": (first, second)}),)}
            )
        }
    )
    with pytest.raises(module.CorpusValidationError) as nonreciprocal:
        module.build_draft_corpus(request, repository_root=tmp_path)
    assert "nonreciprocal_supersession" in _codes(nonreciprocal)


@pytest.mark.parametrize("problem", ["source", "unit", "claim"])
def test_build_enforces_documented_source_unit_and_claim_limits(tmp_path: Path, problem: str):
    module = _module()
    content = "# Guide\nEd is read-only.\n"
    if problem == "source":
        content = "# Guide\n" + "x" * (module.MAX_SOURCE_BYTES + 1)
    elif problem == "unit":
        content = "# Guide\n" + "x" * (module.MAX_UNIT_BYTES + 1)
    request = _request(tmp_path, content=content)
    if problem in {"source", "unit"}:
        line_count = len(content.splitlines())
        entry = request.inventory.sources[0].model_copy(update={"source_line_end": line_count})
        approval = request.source_approvals[0].model_copy(update={"approved_line_end": line_count})
        document = request.manifest.documents[0]
        claim = document.claims[0].model_copy(update={"source_line_end": line_count})
        manifest = request.manifest.model_copy(
            update={"documents": (document.model_copy(update={"source_line_end": line_count, "claims": (claim,)}),)}
        )
        request = request.model_copy(
            update={
                "inventory": request.inventory.model_copy(update={"sources": (entry,)}),
                "source_approvals": (approval,),
                "manifest": manifest,
            }
        )
    else:
        document = request.manifest.documents[0]
        claim = document.claims[0].model_copy(update={"text": "x" * (module.MAX_CLAIM_CHARS + 1)})
        request = request.model_copy(
            update={"manifest": request.manifest.model_copy(update={"documents": (document.model_copy(update={"claims": (claim,)}),)})}
        )

    with pytest.raises((module.CorpusValidationError, ValueError)) as exc:
        module.build_draft_corpus(request, repository_root=tmp_path)

    if isinstance(exc.value, module.CorpusValidationError):
        assert _codes(exc) & {"source_too_large", "unit_too_large", "claim_too_large"}


def _heading_only(request):
    """The same request, located by its reviewed heading alone."""
    no_range = {"source_line_start": None, "source_line_end": None}
    entry = request.inventory.sources[0].model_copy(update=no_range)
    approval = request.source_approvals[0].model_copy(
        update={"approved_line_start": None, "approved_line_end": None}
    )
    document = request.manifest.documents[0]
    claims = tuple(claim.model_copy(update=no_range) for claim in document.claims)
    document = document.model_copy(update={**no_range, "claims": claims})
    return request.model_copy(
        update={
            "inventory": request.inventory.model_copy(update={"sources": (entry,)}),
            "source_approvals": (approval,),
            "manifest": request.manifest.model_copy(update={"documents": (document,)}),
        }
    )


def test_heading_only_evidence_is_resolved_to_its_exact_line_range(tmp_path: Path):
    """Retrieval and verify check line ranges against pinned bytes, so the
    builder resolves a reviewed heading once rather than leaving each later
    reader to re-derive it."""
    module = _module()
    content = "# Intro\nOther.\n# Guide\nEd is read-only.\nMore.\n"
    request = _heading_only(_request(tmp_path, content=content))

    build = module.build_draft_corpus(request, repository_root=tmp_path)

    chunk = json.loads(build.chunks_bytes)
    assert (chunk["sourceLineStart"], chunk["sourceLineEnd"]) == (3, 5)
    assert chunk["sourceSymbol"] == "guide"


def test_heading_only_unit_is_bounded_like_a_line_range(tmp_path: Path):
    module = _module()
    content = "# Guide\n" + "x" * (module.MAX_UNIT_BYTES + 1) + "\n"
    request = _heading_only(_request(tmp_path, content=content))

    with pytest.raises(module.CorpusValidationError) as exc:
        module.build_draft_corpus(request, repository_root=tmp_path)

    assert "unit_too_large" in _codes(exc)


@pytest.mark.parametrize(
    "line",
    [
        "token = request.cookies.get(SESSION_COOKIE)",
        "token: auth.challenge.token,",
        "access_token = settings.openrouter_api_key",
        "password = form_data.password_confirmation",
        'secret: str = field(default=os.environ.get("X", ""), repr=False)',
    ],
)
def test_ordinary_code_naming_a_credential_is_not_secret_like(tmp_path: Path, line: str):
    """Four of the plan's fifteen candidate sources were rejected on lines
    like these, which name a credential without containing one."""
    module = _module()
    request = _request(tmp_path, content=f"# Guide\nEd is read-only.\n{line}\n")

    module.build_draft_corpus(request, repository_root=tmp_path)


# Assembled from fragments so this file carries no credential-shaped literal
# for the repository's own added-line secret scan to flag.
@pytest.mark.parametrize(
    "line",
    [
        "".join(("pass", "word = ", '"correct-horse-battery-staple"')),
        "".join(("OPENROUTER_API_", "KEY: ", "sk-", "or-v1-0123456789abcdefghij")),
        "".join(("Authorization: Bearer ", "gh", "p_", "abcdefghijklmnopqrstuvwxyz0123")),
        "".join(("-----BEGIN RSA ", "PRIVATE", " KEY-----")),
        "".join(("aws ", "AK", "IA", "ABCDEFGHIJKLMNOP")),
    ],
)
def test_credential_material_is_still_secret_like(tmp_path: Path, line: str):
    module = _module()
    request = _request(tmp_path, content=f"# Guide\nEd is read-only.\n{line}\n")

    with pytest.raises(module.CorpusValidationError) as exc:
        module.build_draft_corpus(request, repository_root=tmp_path)

    assert "secret_like_source" in _codes(exc)


def test_chunks_carry_supersession_and_no_approval_state(tmp_path: Path):
    """Approval belongs to the whole bundle, bound to its index digest, so
    nothing inside a chunk may claim it. Supersession must survive the build
    or a retired claim would rank again."""
    module = _module()
    request = _request(tmp_path)
    document = request.manifest.documents[0]
    older = document.claims[0].model_copy(
        update={"superseded_by": ("claim:guide-read-only:v2",)}
    )
    newer = document.claims[0].model_copy(
        update={"id": "claim:guide-read-only:v2", "supersedes": (older.id,)}
    )
    request = request.model_copy(
        update={
            "manifest": request.manifest.model_copy(
                update={"documents": (document.model_copy(update={"claims": (older, newer)}),)}
            ),
            "claim_metadata": (
                request.claim_metadata[0],
                request.claim_metadata[0].model_copy(update={"claim_id": newer.id}),
            ),
        }
    )

    build = module.build_draft_corpus(request, repository_root=tmp_path)

    chunks = {chunk["id"]: chunk for chunk in map(json.loads, build.chunks_bytes.splitlines())}
    assert chunks[older.id]["supersededBy"] == [newer.id]
    assert chunks[newer.id]["supersededBy"] == []
    assert all("approvalState" not in chunk for chunk in chunks.values())


def test_one_file_can_back_several_reviewed_ranges_and_topics(tmp_path: Path):
    from symgov_backend.ed_knowledge import KnowledgeTopic

    module = _module()
    request = _request(tmp_path, content="# Guide\nEd is read-only.\n# Help\nAsk support.\n")
    entry = request.inventory.sources[0]
    approval = request.source_approvals[0]
    document = request.manifest.documents[0]
    help_range = {"source_line_start": 3, "source_line_end": 4, "source_symbol": "help"}
    help_entry = entry.model_copy(
        update={
            "id": "source:guide-help:v1",
            "topic": KnowledgeTopic.SUPPORT,
            "source_line_start": 3,
            "source_line_end": 4,
            "source_heading": "help",
        }
    )
    help_approval = approval.model_copy(
        update={
            "source_id": help_entry.id,
            "approved_line_start": 3,
            "approved_line_end": 4,
            "approved_symbol": "help",
        }
    )
    help_claim = document.claims[0].model_copy(
        update={"id": "claim:guide-help:v1", "topic": KnowledgeTopic.SUPPORT, "text": "Ask support.", **help_range}
    )
    help_document = document.model_copy(
        update={"id": "document:guide-help:v1", "topic": KnowledgeTopic.SUPPORT, "claims": (help_claim,), **help_range}
    )
    request = request.model_copy(
        update={
            "inventory": request.inventory.model_copy(update={"sources": (entry, help_entry)}),
            "source_approvals": (approval, help_approval),
            "manifest": request.manifest.model_copy(update={"documents": (document, help_document)}),
            "claim_metadata": (
                request.claim_metadata[0],
                request.claim_metadata[0].model_copy(
                    update={"claim_id": help_claim.id, "fact_key": "support.ask"}
                ),
            ),
        }
    )

    build = module.build_draft_corpus(request, repository_root=tmp_path)

    chunks = {chunk["id"]: chunk for chunk in map(json.loads, build.chunks_bytes.splitlines())}
    assert (chunks["claim:guide-read-only:v1"]["sourceLineStart"], chunks["claim:guide-read-only:v1"]["sourceLineEnd"]) == (1, 2)
    assert (chunks["claim:guide-help:v1"]["sourceLineStart"], chunks["claim:guide-help:v1"]["sourceLineEnd"]) == (3, 4)
    assert chunks["claim:guide-help:v1"]["topic"] == "support"
    assert json.loads(build.manifest_bytes)["coveredTopics"] == ["application", "support"]
