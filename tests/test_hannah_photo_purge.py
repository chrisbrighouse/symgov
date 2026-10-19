import json
import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from symgov_backend.hannah_photo_purge import purge_hannah_photos


# Only the columns the purge reads or writes; the real tables are PostgreSQL-only.
_TABLES = (
    "CREATE TABLE agent_definitions (id TEXT PRIMARY KEY, slug TEXT)",
    "CREATE TABLE attachments (id TEXT PRIMARY KEY, object_key TEXT)",
    "CREATE TABLE hannah_photo_candidates (id TEXT PRIMARY KEY, status TEXT, attachment_id TEXT, object_key TEXT)",
    "CREATE TABLE hannah_symbol_curation_states (id TEXT PRIMARY KEY, symbol_id TEXT)",
    "CREATE TABLE agent_queue_items (id TEXT PRIMARY KEY, agent_id TEXT, source_type TEXT, status TEXT, created_at TEXT, completed_at TEXT)",
    "CREATE TABLE audit_events (id TEXT PRIMARY KEY, entity_type TEXT, entity_id TEXT, action TEXT, actor_id TEXT, payload_json TEXT, created_at TEXT)",
    "CREATE TABLE classification_records (id TEXT PRIMARY KEY, origin_attachment_id TEXT)",
    "CREATE TABLE published_preview_authorizations (id TEXT PRIMARY KEY, attachment_id TEXT)",
)

NOW = datetime(2026, 10, 19, 16, 0, tzinfo=timezone.utc)


class HannahPhotoPurgeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.session = Session(self.engine)
        for ddl in _TABLES:
            self.session.execute(text(ddl))
        self.hannah_id = str(uuid.uuid4())
        self.other_agent_id = str(uuid.uuid4())
        self._insert("agent_definitions", id=self.hannah_id, slug="hannah")
        self._insert("agent_definitions", id=self.other_agent_id, slug="libby")
        self.hannah_attachment = str(uuid.uuid4())
        self.symbol_attachment = str(uuid.uuid4())
        self._insert("attachments", id=self.hannah_attachment, object_key="hannah/supplemental-photos/gate-valve/a.jpg")
        self._insert("attachments", id=self.symbol_attachment, object_key="symbols/gate-valve/source.svg")
        for status in ("candidate", "rejected", "no_candidate_found"):
            self._insert("hannah_photo_candidates", id=str(uuid.uuid4()), status=status, attachment_id=None, object_key=None)
        self._insert(
            "hannah_photo_candidates",
            id=str(uuid.uuid4()),
            status="attached",
            attachment_id=None,
            object_key="hannah/supplemental-photos/old/orphan.jpg",
        )
        self._insert("hannah_symbol_curation_states", id=str(uuid.uuid4()), symbol_id=str(uuid.uuid4()))
        self.open_card = str(uuid.uuid4())
        self.done_card = str(uuid.uuid4())
        self.other_agent_card = str(uuid.uuid4())
        self._queue(self.open_card, self.hannah_id, "queued")
        self._queue(self.done_card, self.hannah_id, "candidate")
        self._queue(self.other_agent_card, self.other_agent_id, "queued")
        self.session.commit()
        self.deleted_keys = []

    def tearDown(self):
        self.session.close()

    def _insert(self, table, **values):
        columns = ", ".join(values)
        params = ", ".join(f":{key}" for key in values)
        self.session.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({params})"), values)

    def _queue(self, item_id, agent_id, status):
        self._insert(
            "agent_queue_items",
            id=item_id,
            agent_id=agent_id,
            source_type="published_symbol_photo_enrichment",
            status=status,
            created_at="2026-10-01T00:00:00Z",
            completed_at=None,
        )

    def _scalar(self, sql):
        return self.session.execute(text(sql)).scalar()

    def test_dry_run_reports_without_changing_anything(self):
        result = purge_hannah_photos(self.session, apply=False)

        self.assertFalse(result["applied"])
        self.assertEqual(result["attachments"], 1)
        self.assertEqual(
            result["object_keys"],
            ["hannah/supplemental-photos/gate-valve/a.jpg", "hannah/supplemental-photos/old/orphan.jpg"],
        )
        self.assertEqual(result["candidate_total"], 4)
        self.assertEqual(result["curation_states"], 1)
        self.assertEqual([item["id"] for item in result["open_queue_items_to_cancel"]], [self.open_card])
        self.assertEqual(self._scalar("SELECT count(*) FROM hannah_photo_candidates"), 4)
        self.assertEqual(self._scalar("SELECT count(*) FROM attachments"), 2)

    def test_apply_clears_hannah_state_and_keeps_everything_else(self):
        result = purge_hannah_photos(self.session, apply=True, delete_object=self.deleted_keys.append, now=NOW)

        self.assertTrue(result["applied"])
        self.assertEqual(self._scalar("SELECT count(*) FROM hannah_photo_candidates"), 0)
        self.assertEqual(self._scalar("SELECT count(*) FROM hannah_symbol_curation_states"), 0)
        self.assertEqual(self._scalar("SELECT id FROM attachments"), self.symbol_attachment)
        statuses = dict(self.session.execute(text("SELECT id, status FROM agent_queue_items")).all())
        self.assertEqual(statuses[self.open_card], "cancelled")
        self.assertEqual(statuses[self.done_card], "candidate")
        self.assertEqual(statuses[self.other_agent_card], "queued")
        self.assertEqual(
            self.deleted_keys,
            ["hannah/supplemental-photos/gate-valve/a.jpg", "hannah/supplemental-photos/old/orphan.jpg"],
        )
        action, payload = self.session.execute(text("SELECT action, payload_json FROM audit_events")).one()
        self.assertEqual(action, "hannah_photos_purged")
        self.assertEqual(json.loads(payload)["cancelled_queue_items"], [self.open_card])

    def test_apply_refuses_when_another_table_references_a_hannah_attachment(self):
        self._insert("classification_records", id=str(uuid.uuid4()), origin_attachment_id=self.hannah_attachment)
        self.session.commit()

        with self.assertRaises(RuntimeError):
            purge_hannah_photos(self.session, apply=True, delete_object=self.deleted_keys.append, now=NOW)

        self.assertEqual(self._scalar("SELECT count(*) FROM hannah_photo_candidates"), 4)
        self.assertEqual(self.deleted_keys, [])

    def test_storage_failures_are_reported_after_the_database_commit(self):
        def failing_delete(key):
            raise OSError("storage unavailable")

        result = purge_hannah_photos(self.session, apply=True, delete_object=failing_delete, now=NOW)

        self.assertEqual(len(result["object_delete_failures"]), 2)
        self.assertEqual(self._scalar("SELECT count(*) FROM hannah_photo_candidates"), 0)


if __name__ == "__main__":
    unittest.main()
