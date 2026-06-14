from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import symgov_backend.routes.workspace as workspace_routes  # noqa: E402
from symgov_backend.routes.workspace import (  # noqa: E402
    build_provenance_workspace_item,
    choose_workspace_source_preview_asset,
)
from symgov_backend.models import Attachment, IntakeRecord, ReviewCase, ValidationReport  # noqa: E402


def test_workspace_source_preview_uses_generated_svg_for_dxf_validation_report():
    validation_report = SimpleNamespace(
        normalized_payload_json={
            "raw_object_key": "symbols/pump.dxf",
            "source_file_name": "pump.dxf",
            "source_content_type": "application/dxf",
            "visual_assets": {
                "source_assets": [
                    {
                        "object_key": "symbols/pump.dxf",
                        "filename": "pump.dxf",
                        "content_type": "application/dxf",
                        "format": "dxf",
                        "role": "source",
                    }
                ],
                "derivatives": [
                    {
                        "object_key": "symbols/pump.svg",
                        "filename": "pump.svg",
                        "content_type": "image/svg+xml",
                        "format": "svg",
                        "role": "generated_preview",
                    }
                ],
            },
        }
    )

    selected = choose_workspace_source_preview_asset(
        validation_report=validation_report,
        source_file_name="pump.dxf",
        source_object_key="symbols/pump.dxf",
    )

    assert selected["object_key"] == "symbols/pump.svg"


def test_workspace_source_preview_uses_companion_jpg_before_generated_svg():
    validation_report = SimpleNamespace(
        normalized_payload_json={
            "raw_object_key": "symbols/valve.dxf",
            "visual_assets": {
                "preview": {
                    "object_key": "symbols/valve.jpg",
                    "filename": "valve.jpg",
                    "content_type": "image/jpeg",
                    "format": "jpg",
                    "role": "companion_preview",
                },
                "source_assets": [
                    {
                        "object_key": "symbols/valve.dxf",
                        "filename": "valve.dxf",
                        "content_type": "application/dxf",
                        "format": "dxf",
                        "role": "source",
                    }
                ],
                "derivatives": [
                    {
                        "object_key": "symbols/valve.svg",
                        "filename": "valve.svg",
                        "content_type": "image/svg+xml",
                        "format": "svg",
                        "role": "generated_preview",
                    }
                ],
            },
        }
    )

    selected = choose_workspace_source_preview_asset(
        validation_report=validation_report,
        source_file_name="valve.dxf",
        source_object_key="symbols/valve.dxf",
    )

    assert selected["object_key"] == "symbols/valve.jpg"


def test_workspace_source_preview_returns_none_for_raw_dxf_without_preview_asset():
    validation_report = SimpleNamespace(normalized_payload_json={"source_content_type": "application/dxf"})

    assert (
        choose_workspace_source_preview_asset(
            validation_report=validation_report,
            source_file_name="plain.dxf",
            source_object_key="symbols/plain.dxf",
        )
        is None
    )


def test_workspace_source_preview_falls_back_to_browser_image_source():
    selected = choose_workspace_source_preview_asset(
        source_file_name="legacy.png",
        source_object_key="symbols/legacy.png",
    )

    assert selected["object_key"] == "symbols/legacy.png"
    assert selected["format"] == "png"


def test_workspace_source_preview_preserves_intake_companion_when_validation_adds_derivative():
    intake_record = SimpleNamespace(
        normalized_submission_json={
            "raw_object_key": "symbols/valve.dxf",
            "visual_assets": {
                "preview": {
                    "object_key": "symbols/valve.jpg",
                    "filename": "valve.jpg",
                    "content_type": "image/jpeg",
                    "format": "jpg",
                    "role": "companion_preview",
                },
                "source_assets": [
                    {
                        "object_key": "symbols/valve.dxf",
                        "filename": "valve.dxf",
                        "content_type": "application/dxf",
                        "format": "dxf",
                        "role": "source",
                    },
                    {
                        "object_key": "symbols/valve.jpg",
                        "filename": "valve.jpg",
                        "content_type": "image/jpeg",
                        "format": "jpg",
                        "role": "source",
                    },
                ],
            },
        }
    )
    validation_report = SimpleNamespace(
        normalized_payload_json={
            "raw_object_key": "symbols/valve.dxf",
            "visual_assets": {
                "preview": {
                    "object_key": "validation-reports/valve/preview.svg",
                    "filename": "valve.svg",
                    "content_type": "image/svg+xml",
                    "format": "svg",
                    "role": "generated_preview",
                    "downloadable": False,
                },
                "derivatives": [
                    {
                        "object_key": "validation-reports/valve/preview.svg",
                        "filename": "valve.svg",
                        "content_type": "image/svg+xml",
                        "format": "svg",
                        "role": "generated_preview",
                        "downloadable": False,
                    }
                ],
            },
        }
    )

    selected = choose_workspace_source_preview_asset(
        validation_report=validation_report,
        intake_record=intake_record,
        source_file_name="valve.dxf",
        source_object_key="symbols/valve.dxf",
    )

    assert selected["object_key"] == "symbols/valve.jpg"


def test_workspace_review_source_preview_endpoint_uses_intake_companion_for_validation_case(monkeypatch):
    review_case_id = uuid.uuid4()
    validation_report_id = uuid.uuid4()
    intake_record_id = uuid.uuid4()
    review_case = SimpleNamespace(
        id=review_case_id,
        source_entity_type="validation_report",
        source_entity_id=validation_report_id,
    )
    validation_report = SimpleNamespace(
        id=validation_report_id,
        source_type="intake_record",
        source_id=intake_record_id,
        normalized_payload_json={
            "raw_object_key": "symbols/valve.dxf",
            "source_file_name": "valve.dxf",
            "visual_assets": {
                "preview": {
                    "object_key": "validation-reports/valve/preview.svg",
                    "filename": "valve.svg",
                    "content_type": "image/svg+xml",
                    "format": "svg",
                    "role": "generated_preview",
                    "downloadable": False,
                },
                "derivatives": [
                    {
                        "object_key": "validation-reports/valve/preview.svg",
                        "filename": "valve.svg",
                        "content_type": "image/svg+xml",
                        "format": "svg",
                        "role": "generated_preview",
                        "downloadable": False,
                    }
                ],
            },
        },
        report_json={},
    )
    intake_record = SimpleNamespace(
        id=intake_record_id,
        normalized_submission_json={
            "raw_object_key": "symbols/valve.dxf",
            "visual_assets": {
                "preview": {
                    "object_key": "symbols/valve.jpg",
                    "filename": "valve.jpg",
                    "content_type": "image/jpeg",
                    "format": "jpg",
                    "role": "companion_preview",
                }
            },
        },
    )
    captured = {}

    class PreviewSession:
        def get(self, model, item_id):
            if model is ReviewCase and item_id == review_case_id:
                return review_case
            if model is ValidationReport and item_id == validation_report_id:
                return validation_report
            if model is IntakeRecord and item_id == intake_record_id:
                return intake_record
            return None

        def execute(self, statement):
            return SimpleNamespace(
                scalar_one_or_none=lambda: SimpleNamespace(content_type="image/jpeg")
                if Attachment.__tablename__ in str(statement)
                else None
            )

    def fake_download_object_bytes(*, object_key, env_file):
        captured["object_key"] = object_key
        return {"payload": b"jpeg-bytes", "content_type": "image/jpeg"}

    monkeypatch.setattr(workspace_routes, "download_object_bytes", fake_download_object_bytes)
    monkeypatch.setattr(
        workspace_routes,
        "get_settings",
        lambda: SimpleNamespace(storage_env_file="storage.env"),
    )

    response = workspace_routes.get_workspace_review_source_preview(str(review_case_id), session=PreviewSession())

    assert captured["object_key"] == "symbols/valve.jpg"
    assert response.media_type == "image/jpeg"
    assert response.body == b"jpeg-bytes"


class FakeQuery:
    def filter(self, *args, **kwargs):
        return self

    def one_or_none(self):
        return None


class FakeScalarResult:
    def __init__(self, item):
        self.item = item

    def first(self):
        return self.item


class FakeExecuteResult:
    def __init__(self, item):
        self.item = item

    def scalars(self):
        return FakeScalarResult(self.item)


class FakeSession:
    def __init__(self, latest_validation_report):
        self.latest_validation_report = latest_validation_report
        self.added = []

    def execute(self, statement):
        return FakeExecuteResult(self.latest_validation_report)

    def query(self, model):
        return FakeQuery()

    def add(self, item):
        self.added.append(item)

    def flush(self):
        return None

    def get(self, model, item_id):
        return None


def test_provenance_review_case_uses_latest_dxf_validation_derivative_preview():
    intake_id = uuid.uuid4()
    review_case = SimpleNamespace(
        id=uuid.uuid4(),
        source_entity_type="provenance_assessment",
        source_entity_id=uuid.uuid4(),
        opened_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
        escalation_level="medium",
        current_stage="classification_review",
    )
    intake_record = SimpleNamespace(
        id=intake_id,
        raw_object_key="symbols/pump.dxf",
        source_package_id=None,
        normalized_submission_json={
            "raw_object_key": "symbols/pump.dxf",
            "original_filename": "pump.dxf",
            "declared_format": "dxf",
            "candidate_symbol_id": "PUMP",
            "candidate_title": "Pump",
        },
    )
    provenance_assessment = SimpleNamespace(
        summary="Classification needs review.",
        report_json={},
        evidence_json={},
        rights_status="unknown",
    )
    validation_report = SimpleNamespace(
        normalized_payload_json={
            "asset_format": "dxf",
            "source_file_name": "pump.dxf",
            "raw_object_key": "symbols/pump.dxf",
            "visual_assets": {
                "derivatives": [
                    {
                        "object_key": "validation-reports/pump/preview.svg",
                        "filename": "pump.svg",
                        "content_type": "image/svg+xml",
                        "format": "svg",
                        "role": "generated_preview",
                    }
                ]
            },
        },
        report_json={},
    )

    item = build_provenance_workspace_item(
        FakeSession(validation_report),
        review_case,
        provenance_assessment,
        intake_record,
    )

    assert item.sourcePreviewUrl == f"/api/v1/workspace/review-cases/{review_case.id}/source/preview"
