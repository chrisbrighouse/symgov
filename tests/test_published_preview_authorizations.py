from __future__ import annotations

from collections import deque
from types import SimpleNamespace
import uuid

import pytest

from symgov_backend.models import (
    Attachment,
    IntakeRecord,
    PublishedPreviewAuthorization,
    ReviewSplitItem,
    SymbolRevision,
    ValidationReport,
)
from symgov_backend.published_preview_authorizations import (
    PreviewAuthorizationResult,
    backfill_published_preview_authorizations,
    ensure_preview_authorization,
)


class _FakeQuery:
    def __init__(self, *, one_or_none_values=None, all_rows=None):
        self._one_or_none_values = one_or_none_values
        self._all_rows = all_rows

    def filter(self, *_criteria):
        return self

    def one_or_none(self):
        if self._one_or_none_values is None or not self._one_or_none_values:
            return None
        return self._one_or_none_values.popleft()

    def all(self):
        return list(self._all_rows or [])


class _FakeSession:
    def __init__(self, *, query_map=None, revisions=None, get_map=None):
        self._query_map = query_map or {}
        self._revisions = list(revisions or [])
        self._get_map = get_map or {}
        self.added = []
        self.flushed = 0
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        if model is SymbolRevision:
            return _FakeQuery(all_rows=self._revisions)
        values = self._query_map.get(model)
        if values is None:
            return _FakeQuery(one_or_none_values=deque())
        return _FakeQuery(one_or_none_values=values)

    def get(self, model, item_id):
        return self._get_map.get((model, item_id))

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.flushed += 1

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _revision(*, revision_id: uuid.UUID, object_key: str, lineage: dict | None = None) -> SimpleNamespace:
    payload = {
        "visual_assets": {
            "preview": {
                "object_key": object_key,
                "content_type": "image/svg+xml",
                "filename": "preview.svg",
                "format": "svg",
            }
        }
    }
    if lineage is not None:
        payload["lineage"] = lineage
    return SimpleNamespace(id=revision_id, payload_json=payload)


def _attachment(*, object_key: str, parent_type: str, parent_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        object_key=object_key,
        parent_type=parent_type,
        parent_id=parent_id,
        sha256="f739e7cb8015b7f664f57169eb3f2f43db649bfc4507814d9eb7f2d959ec1c5d",
        size_bytes=19,
        content_type="image/svg+xml",
    )


def test_ensure_preview_authorization_accepts_lineaged_validation_report_attachment():
    revision_id = uuid.uuid4()
    report_id = uuid.uuid4()
    key = "previews/lineaged.svg"
    revision = _revision(revision_id=revision_id, object_key=key, lineage={"validation_report_id": str(report_id)})
    attachment = _attachment(object_key=key, parent_type="validation_report", parent_id=report_id)
    session = _FakeSession(
        query_map={
            Attachment: deque([attachment]),
            PublishedPreviewAuthorization: deque([None, None]),
        },
        get_map={(ValidationReport, report_id): SimpleNamespace(id=report_id)},
    )

    result = ensure_preview_authorization(session, revision=revision, source="legacy_backfill")

    assert result.status == "created"
    assert session.flushed == 1
    assert len(session.added) == 1
    created = session.added[0]
    assert created.symbol_revision_id == revision_id
    assert created.attachment_parent_type == "validation_report"
    assert created.attachment_parent_id == report_id


def test_ensure_preview_authorization_rejects_untrusted_lineage():
    revision_id = uuid.uuid4()
    expected_report_id = uuid.uuid4()
    foreign_report_id = uuid.uuid4()
    key = "previews/foreign.svg"
    revision = _revision(
        revision_id=revision_id,
        object_key=key,
        lineage={"validation_report_id": str(expected_report_id)},
    )
    attachment = _attachment(object_key=key, parent_type="validation_report", parent_id=foreign_report_id)
    session = _FakeSession(query_map={Attachment: deque([attachment])})

    result = ensure_preview_authorization(session, revision=revision, source="legacy_backfill")

    assert result == PreviewAuthorizationResult(status="untrusted_lineage", object_key=key)
    assert session.added == []
    assert session.flushed == 0


def test_ensure_preview_authorization_accepts_validation_report_split_lineage_when_report_id_differs():
    revision_id = uuid.uuid4()
    lineage_report_id = uuid.uuid4()
    attachment_report_id = uuid.uuid4()
    split_item_id = uuid.uuid4()
    review_case_id = uuid.uuid4()
    key = "previews/split-lineage.svg"
    revision = _revision(
        revision_id=revision_id,
        object_key=key,
        lineage={
            "validation_report_id": str(lineage_report_id),
            "review_split_item_id": str(split_item_id),
            "parent_sheet_review_case_id": str(review_case_id),
            "reviewed_attachment_object_key": key,
        },
    )
    attachment = _attachment(object_key=key, parent_type="validation_report", parent_id=attachment_report_id)
    split_item = SimpleNamespace(
        id=split_item_id,
        review_case_id=review_case_id,
        attachment_object_key=key,
    )
    session = _FakeSession(
        query_map={
            Attachment: deque([attachment]),
            PublishedPreviewAuthorization: deque([None, None]),
        },
        get_map={
            (ValidationReport, attachment_report_id): SimpleNamespace(id=attachment_report_id),
            (ReviewSplitItem, split_item_id): split_item,
        },
    )

    result = ensure_preview_authorization(session, revision=revision, source="legacy_backfill")

    assert result.status == "created"
    assert session.flushed == 1
    assert len(session.added) == 1


@pytest.mark.parametrize("mismatch", ["split_item", "review_case", "object_key"])
def test_ensure_preview_authorization_rejects_validation_report_split_lineage_mismatch(mismatch: str):
    revision_id = uuid.uuid4()
    lineage_report_id = uuid.uuid4()
    attachment_report_id = uuid.uuid4()
    split_item_id = uuid.uuid4()
    review_case_id = uuid.uuid4()
    key = "previews/split-lineage-mismatch.svg"
    lineage_split_item_id = split_item_id if mismatch != "split_item" else uuid.uuid4()
    lineage_review_case_id = review_case_id if mismatch != "review_case" else uuid.uuid4()
    lineage_key = key if mismatch != "object_key" else "previews/other.svg"
    revision = _revision(
        revision_id=revision_id,
        object_key=key,
        lineage={
            "validation_report_id": str(lineage_report_id),
            "review_split_item_id": str(lineage_split_item_id),
            "parent_sheet_review_case_id": str(lineage_review_case_id),
            "reviewed_attachment_object_key": lineage_key,
        },
    )
    attachment = _attachment(object_key=key, parent_type="validation_report", parent_id=attachment_report_id)
    split_item = SimpleNamespace(
        id=split_item_id,
        review_case_id=review_case_id,
        attachment_object_key=key,
    )
    session = _FakeSession(
        query_map={Attachment: deque([attachment])},
        get_map={
            (ValidationReport, attachment_report_id): SimpleNamespace(id=attachment_report_id),
            (ReviewSplitItem, split_item_id): split_item,
        },
    )

    result = ensure_preview_authorization(session, revision=revision, source="legacy_backfill")

    assert result == PreviewAuthorizationResult(status="untrusted_lineage", object_key=key)
    assert session.added == []
    assert session.flushed == 0


def test_ensure_preview_authorization_accepts_external_submission_batch_with_intake_lineage():
    from symgov_backend import published_preview_authorizations as module

    revision_id = uuid.uuid4()
    intake_id = uuid.uuid4()
    batch_token = "subext-20260919T150000Z"
    parent_batch_id = module._coerce_uuid(batch_token)
    key = "external-submissions/subext-20260919T150000Z/01-valve.svg"
    revision = _revision(
        revision_id=revision_id,
        object_key=key,
        lineage={"intake_record_id": str(intake_id)},
    )
    attachment = _attachment(object_key=key, parent_type="external_submission_batch", parent_id=parent_batch_id)
    intake = SimpleNamespace(
        id=intake_id,
        raw_object_key=key,
        normalized_submission_json={
            "submission_batch_id": batch_token,
            "attachment_ids": [str(attachment.id)],
            "raw_object_key": key,
        },
    )
    session = _FakeSession(
        query_map={
            Attachment: deque([attachment]),
            PublishedPreviewAuthorization: deque([None, None]),
        },
        get_map={(IntakeRecord, intake_id): intake},
    )

    result = ensure_preview_authorization(session, revision=revision, source="legacy_backfill")

    assert result.status == "created"
    assert len(session.added) == 1


def test_ensure_preview_authorization_rejects_external_submission_payload_recursed_object_key():
    from symgov_backend import published_preview_authorizations as module

    revision_id = uuid.uuid4()
    intake_id = uuid.uuid4()
    batch_token = "subext-20260919T151500Z"
    parent_batch_id = module._coerce_uuid(batch_token)
    trusted_key = "external-submissions/subext-20260919T151500Z/01-trusted.svg"
    foreign_key = "external-submissions/subext-20260919T151500Z/99-foreign.svg"
    revision = _revision(
        revision_id=revision_id,
        object_key=foreign_key,
        lineage={"intake_record_id": str(intake_id)},
    )
    revision.payload_json["companion_files"] = [{"object_key": foreign_key}]

    attachment = _attachment(object_key=foreign_key, parent_type="external_submission_batch", parent_id=parent_batch_id)
    intake = SimpleNamespace(
        id=intake_id,
        raw_object_key=trusted_key,
        normalized_submission_json={
            "submission_batch_id": batch_token,
            "attachment_ids": [],
            "raw_object_key": trusted_key,
            "origin_object_key": trusted_key,
            "source_object_key": trusted_key,
        },
    )
    session = _FakeSession(
        query_map={
            Attachment: deque([attachment]),
        },
        get_map={(IntakeRecord, intake_id): intake},
    )

    result = ensure_preview_authorization(session, revision=revision, source="legacy_backfill")

    assert result == PreviewAuthorizationResult(status="untrusted_lineage", object_key=foreign_key)
    assert session.added == []
    assert session.flushed == 0


def test_backfill_apply_rolls_back_and_marks_not_applied_on_failure(monkeypatch):
    revisions = [SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(id=uuid.uuid4())]
    session = _FakeSession(revisions=revisions)
    statuses = iter([
        PreviewAuthorizationResult(status="created", object_key="ok.svg"),
        PreviewAuthorizationResult(status="missing_attachment", object_key="missing.svg"),
    ])

    monkeypatch.setattr(
        "symgov_backend.published_preview_authorizations.ensure_preview_authorization",
        lambda *_args, **_kwargs: next(statuses),
    )

    result = backfill_published_preview_authorizations(session, apply=True)

    assert result["created"] == 1
    assert result["missing_attachment"] == 1
    assert result["applied"] is False
    assert len(result["failures"]) == 1
    assert result["failures"][0]["status"] == "missing_attachment"
    assert session.commits == 0
    assert session.rollbacks == 1


def test_manage_symgov_backfill_apply_exits_nonzero_when_failures(monkeypatch, capsys):
    import manage_symgov

    class _Session:
        def close(self):
            return None

    monkeypatch.setattr(manage_symgov, "create_session_factory", lambda **_kwargs: lambda: _Session())
    monkeypatch.setattr(
        manage_symgov,
        "backfill_published_preview_authorizations",
        lambda *_args, **_kwargs: {
            "published_revisions": 2,
            "created": 1,
            "unchanged": 0,
            "no_preview": 0,
            "missing_attachment": 1,
            "missing_sha256": 0,
            "invalid_size": 0,
            "conflicting_object_key": 0,
            "immutable_mismatch": 0,
            "untrusted_lineage": 0,
            "failures": [{"symbol_revision_id": "abc", "status": "missing_attachment", "object_key": "x", "reason": ""}],
            "applied": False,
        },
    )

    assert manage_symgov.main(["backfill-published-preview-authorizations", "--apply"]) == 1
    assert "aborted: one or more failures were detected" in capsys.readouterr().err


def test_manage_symgov_backfill_dry_run_stays_zero_even_with_failures(monkeypatch):
    import manage_symgov

    class _Session:
        def close(self):
            return None

    monkeypatch.setattr(manage_symgov, "create_session_factory", lambda **_kwargs: lambda: _Session())
    monkeypatch.setattr(
        manage_symgov,
        "backfill_published_preview_authorizations",
        lambda *_args, **_kwargs: {
            "published_revisions": 1,
            "created": 0,
            "unchanged": 0,
            "no_preview": 0,
            "missing_attachment": 1,
            "missing_sha256": 0,
            "invalid_size": 0,
            "conflicting_object_key": 0,
            "immutable_mismatch": 0,
            "untrusted_lineage": 0,
            "failures": [{"symbol_revision_id": "abc", "status": "missing_attachment", "object_key": "x", "reason": ""}],
            "applied": False,
        },
    )

    assert manage_symgov.main(["backfill-published-preview-authorizations"]) == 0
