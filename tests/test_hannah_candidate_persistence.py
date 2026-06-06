import unittest
import uuid
from datetime import datetime, timezone

from symgov_backend.models.schema import HannahPhotoCandidate, HannahSymbolCurationState
from symgov_backend.runtime import coerce_uuid, resolve_hannah_photo_candidate_record, resolve_hannah_symbol_curation_state


class _FakeQuery:
    def __init__(self, session):
        self.session = session
        self.criteria = {}

    def filter_by(self, **kwargs):
        self.criteria.update(kwargs)
        return self

    def one_or_none(self):
        for record in self.session.records_by_id.values():
            if all(getattr(record, key) == value for key, value in self.criteria.items()):
                return record
        return None


class _FakeSession:
    def __init__(self, model=HannahPhotoCandidate):
        self.model = model
        self.records_by_id = {}
        self.added = []

    def get(self, model, record_id):
        assert model is self.model
        return self.records_by_id.get(record_id)

    def query(self, model):
        assert model is self.model
        return _FakeQuery(self)

    def add(self, record):
        self.added.append(record)
        self.records_by_id[record.id] = record


class HannahPhotoCandidatePersistenceTests(unittest.TestCase):
    def test_resolves_existing_candidate_by_symbol_and_image_when_candidate_id_changes(self):
        session = _FakeSession()
        completed_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
        symbol_id = uuid.uuid4()
        existing = HannahPhotoCandidate(
            id=uuid.uuid4(),
            symbol_id=symbol_id,
            image_url="https://example.com/photo.jpg",
            source_url="https://example.com/photo.jpg",
            source_domain="example.com",
            rights_status="needs_review",
            status="candidate",
            first_seen_at=completed_at,
            last_seen_at=completed_at,
        )
        session.records_by_id[existing.id] = existing

        record = resolve_hannah_photo_candidate_record(
            session,
            {"id": str(uuid.uuid4()), "symbol_id": str(symbol_id), "image_url": existing.image_url},
            symbol_id,
            existing.image_url,
            completed_at,
        )

        self.assertIs(record, existing)
        self.assertEqual(session.added, [])

    def test_does_not_reuse_conflicting_candidate_id_for_different_symbol_image(self):
        session = _FakeSession()
        completed_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
        conflicting_id = coerce_uuid("shared-source-result")
        existing = HannahPhotoCandidate(
            id=conflicting_id,
            symbol_id=uuid.uuid4(),
            image_url="https://example.com/old.jpg",
            source_url="https://example.com/old.jpg",
            source_domain="example.com",
            rights_status="needs_review",
            status="candidate",
            first_seen_at=completed_at,
            last_seen_at=completed_at,
        )
        session.records_by_id[existing.id] = existing

        new_symbol_id = uuid.uuid4()
        record = resolve_hannah_photo_candidate_record(
            session,
            {"id": str(conflicting_id), "symbol_id": str(new_symbol_id), "image_url": "https://example.com/new.jpg"},
            new_symbol_id,
            "https://example.com/new.jpg",
            completed_at,
        )

        self.assertIsNot(record, existing)
        self.assertEqual(record.symbol_id, new_symbol_id)
        self.assertEqual(record.image_url, "https://example.com/new.jpg")
        self.assertNotEqual(record.id, conflicting_id)
        self.assertEqual(session.added, [record])
    def test_reuses_pending_symbol_state_for_duplicate_attempts_in_one_report(self):
        session = _FakeSession(HannahSymbolCurationState)
        completed_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
        symbol_id = uuid.uuid4()
        symbol_states = {}

        first = resolve_hannah_symbol_curation_state(session, symbol_states, symbol_id, completed_at)
        second = resolve_hannah_symbol_curation_state(session, symbol_states, symbol_id, completed_at)

        self.assertIs(first, second)
        self.assertEqual(len(session.added), 1)
        self.assertEqual(session.added[0].symbol_id, symbol_id)

    def test_resolves_existing_symbol_state_without_new_insert(self):
        session = _FakeSession(HannahSymbolCurationState)
        completed_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
        symbol_id = uuid.uuid4()
        existing = HannahSymbolCurationState(
            id=uuid.uuid4(),
            symbol_id=symbol_id,
            status="candidates_recorded",
            attempt_count=1,
            photo_count=0,
            notes_json={},
            created_at=completed_at,
            updated_at=completed_at,
        )
        session.records_by_id[existing.id] = existing

        state = resolve_hannah_symbol_curation_state(session, {}, symbol_id, completed_at)

        self.assertIs(state, existing)
        self.assertEqual(session.added, [])


if __name__ == "__main__":
    unittest.main()
