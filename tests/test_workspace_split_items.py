from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.models import IntakeRecord, ReviewSplitItem  # noqa: E402
from symgov_backend.routes.workspace import ensure_split_items  # noqa: E402
from symgov_backend.runtime import coerce_uuid  # noqa: E402


class RaceySplitItemSession:
    """Fake Session that reproduces a concurrent insert between get() and flush()."""

    def __init__(self, review_case_id: uuid.UUID, children: list[dict]):
        self.review_case_id = review_case_id
        self.children = children
        self.added = []
        self.rollback_called = False
        self.flush_calls = 0
        self.existing_by_id = {}
        for child in children:
            proposed_symbol_id = str(child.get("proposed_symbol_id") or child.get("file_name") or "UNSPECIFIED")
            file_name = str(child.get("file_name") or "child.png")
            child_key = str(child.get("attachment_object_key") or proposed_symbol_id or file_name)
            item_id = coerce_uuid(f"review-split-item:{review_case_id}:{child_key}")
            self.existing_by_id[item_id] = SimpleNamespace(
                id=item_id,
                review_case_id=review_case_id,
                child_key=child_key,
                proposed_symbol_id=proposed_symbol_id,
                proposed_symbol_name=str(child.get("proposed_symbol_name") or file_name or "Unnamed child"),
                file_name=file_name,
                parent_file_name="fire-sheet.jpg",
                name_source=child.get("name_source"),
                attachment_object_key=child.get("attachment_object_key"),
                status="awaiting_decision",
                payload_json=child,
                updated_at=None,
            )

    def get(self, model, item_id):
        if model is IntakeRecord:
            return None
        if model is ReviewSplitItem and self.rollback_called:
            return self.existing_by_id.get(item_id)
        return None

    def add(self, item):
        self.added.append(item)

    def flush(self):
        self.flush_calls += 1
        raise IntegrityError(
            "INSERT INTO review_split_items ...",
            {},
            Exception('duplicate key value violates unique constraint "pk_review_split_items"'),
        )

    def rollback(self):
        self.rollback_called = True


def test_ensure_split_items_recovers_from_concurrent_duplicate_primary_key_insert():
    review_case_id = uuid.uuid4()
    children = [
        {
            "attachment_object_key": "derived-splits/fire/001-panel-region-01.png",
            "proposed_symbol_id": "FIRE-PANEL-REGION-01",
            "proposed_symbol_name": "Fire panel region 01",
            "file_name": "panel-region-01.png",
        },
        {
            "attachment_object_key": "derived-splits/fire/002-panel-region-02.png",
            "proposed_symbol_id": "FIRE-PANEL-REGION-02",
            "proposed_symbol_name": "Fire panel region 02",
            "file_name": "panel-region-02.png",
        },
    ]
    session = RaceySplitItemSession(review_case_id, children)
    review_case = SimpleNamespace(id=review_case_id)
    validation_report = SimpleNamespace(
        source_type="validation_report",
        source_id=None,
        normalized_payload_json={"derivative_manifest": {"children": children}},
    )

    items = ensure_split_items(
        session,
        review_case=review_case,
        validation_report=validation_report,
        source_file_name="fire-sheet.jpg",
    )

    assert session.flush_calls == 1
    assert session.rollback_called is True
    assert [item.child_key for item in items] == [child["attachment_object_key"] for child in children]
