"""Rehearsal for the DEXPI concept seed against a real PostgreSQL server (WP3).

Why this file is PostgreSQL-only: the seed's correctness is almost entirely
server-side. `SGC-########` codes come from a sequence, `aliases_json` is a
JSONB array with a check constraint, `concept_code` is uniquely indexed, and
`current_revision_id` is a circular foreign key that only a published revision
sets. None of that exists on SQLite, so a portable test could assert nothing
that matters here.

It also rehearses what decision D8 changed: the seed is a command taking an
`--actor-id`, not a migration, because `semantic_concepts.created_by_user_id`
is a NOT NULL foreign key to `users` and a migration has nobody to name.

Redaction: this file never prints the disposable container's connection string
and its seeded identity uses a synthetic `@example.test` email.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(Path(__file__).resolve().parent))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

from symgov_backend import dexpi_seed  # noqa: E402
from symgov_backend.services.dexpi_concepts import plan_concepts  # noqa: E402

SELECTION_PATH = REPO_ROOT / "integrations" / "dexpi" / "selection.json"

NOW = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

# What decisions D3 and D7 commit to: 57 ComponentClasses plus 26 reference
# shapes, covering all 174 selected symbols.
EXPECTED_CONCEPTS = 83
EXPECTED_ASSIGNMENTS = 174


@pytest.fixture(scope="module")
def seed_database():
    if not SELECTION_PATH.is_file():
        pytest.skip("the WP2 selection manifest has not been generated")
    with _database("symgov-dexpi-seed") as (engine, url, raw_url):
        _alembic(url, "upgrade", "head")
        yield engine, url


@pytest.fixture(scope="module")
def actor_id(seed_database) -> uuid.UUID:
    engine, _ = seed_database
    identifier = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                "must_change_pin,is_active,created_at,updated_at) "
                "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
            ),
            {"id": identifier, "email": "dexpi-seed@example.test", "now": NOW},
        )
    return identifier


@pytest.fixture()
def clean(seed_database, monkeypatch, tmp_path):
    """One empty concept table per test, and the CLI pointed at this database."""
    engine, url = seed_database
    with engine.begin() as connection:
        connection.execute(
            text("TRUNCATE semantic_concepts, semantic_concept_revisions CASCADE")
        )
    monkeypatch.setenv("SYMGOV_DATABASE_URL", url)
    return engine, tmp_path


def run(*argv) -> tuple[int, dict]:
    """Invoke the CLI and return its exit code with the report it printed."""
    import io
    import contextlib

    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        code = dexpi_seed.main([*argv, "--selection", str(SELECTION_PATH)])
    printed = stream.getvalue()
    # A failed run reports on stderr and prints no report at all, which is the
    # point: there is nothing to report because nothing was recorded.
    return code, json.loads(printed) if printed.strip() else {}


def apply_seed(actor_id, *extra) -> tuple[int, dict]:
    return run("apply", "--actor-id", str(actor_id), *extra)


class TestPlan:
    def test_it_reports_the_whole_seed_and_writes_nothing(self, clean, actor_id):
        engine, _ = clean
        code, report = run("plan")
        assert code == 0
        assert report["mode"] == "dry-run"
        assert report["would_create"] == EXPECTED_CONCEPTS
        assert report["unchanged"] == 0
        assert report["assigned_symbols"] == EXPECTED_ASSIGNMENTS
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM semantic_concepts")).scalar_one() == 0

    def test_a_dry_run_burns_no_concept_codes(self, clean, actor_id):
        engine, _ = clean
        run("plan")
        run("plan")
        apply_seed(actor_id)
        with engine.connect() as connection:
            codes = [
                row[0]
                for row in connection.execute(
                    text("SELECT concept_code FROM semantic_concepts ORDER BY concept_code")
                )
            ]
        # The plan command never calls the allocator, so the codes an apply
        # allocates are contiguous however many times it was rehearsed first.
        sequence = [int(code.removeprefix("SGC-")) for code in codes]
        assert sequence == list(range(sequence[0], sequence[0] + EXPECTED_CONCEPTS))


class TestApply:
    def test_it_creates_and_publishes_every_concept(self, clean, actor_id):
        engine, _ = clean
        code, report = apply_seed(actor_id)
        assert code == 0
        assert report["mode"] == "apply"
        assert report["created"] == EXPECTED_CONCEPTS
        assert report["drift"] == []
        with engine.connect() as connection:
            statuses = dict(
                connection.execute(
                    text("SELECT status, count(*) FROM semantic_concepts GROUP BY status")
                ).all()
            )
            states = dict(
                connection.execute(
                    text(
                        "SELECT lifecycle_state, count(*) FROM semantic_concept_revisions "
                        "GROUP BY lifecycle_state"
                    )
                ).all()
            )
        # Only a published revision makes a concept active, and only an active
        # concept can carry a verified primary assignment at the gate.
        assert statuses == {"active": EXPECTED_CONCEPTS}
        assert states == {"published": EXPECTED_CONCEPTS}

    def test_every_concept_points_at_its_published_revision(self, clean, actor_id):
        engine, _ = clean
        apply_seed(actor_id)
        with engine.connect() as connection:
            dangling = connection.execute(
                text("SELECT count(*) FROM semantic_concepts WHERE current_revision_id IS NULL")
            ).scalar_one()
        assert dangling == 0

    def test_the_actor_is_recorded_as_author_and_reviewer(self, clean, actor_id):
        engine, _ = clean
        apply_seed(actor_id)
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT count(*) FROM semantic_concept_revisions "
                    "WHERE author_id = :actor AND reviewed_by_user_id = :actor "
                    "AND reviewed_at IS NOT NULL"
                ),
                {"actor": actor_id},
            ).scalar_one()
        assert rows == EXPECTED_CONCEPTS

    def test_the_stored_content_is_what_the_plan_decided(self, clean, actor_id):
        engine, _ = clean
        apply_seed(actor_id)
        plan = plan_concepts(json.loads(SELECTION_PATH.read_text(encoding="utf-8")))
        planned = {concept["preferred_name"]: concept for concept in plan["concepts"]}
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT r.preferred_name, r.definition, r.aliases_json, r.notes, "
                    "r.rationale, c.concept_kind "
                    "FROM semantic_concept_revisions r "
                    "JOIN semantic_concepts c ON c.id = r.concept_id"
                )
            ).all()
        assert len(rows) == EXPECTED_CONCEPTS
        for name, definition, aliases, notes, rationale, kind in rows:
            expected = planned[name]
            assert definition == expected["definition"]
            assert aliases == expected["aliases"]
            assert notes == expected["notes"]
            assert rationale == expected["rationale"]
            assert kind == expected["concept_kind"]

    def test_an_unknown_actor_records_nothing(self, clean, actor_id):
        engine, _ = clean
        code, _ = apply_seed(uuid.uuid4())
        assert code == 1
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM semantic_concepts")).scalar_one() == 0


class TestRerun:
    def test_a_second_apply_creates_nothing(self, clean, actor_id):
        engine, _ = clean
        apply_seed(actor_id)
        code, report = apply_seed(actor_id)
        assert code == 0
        assert report["created"] == 0
        assert report["unchanged"] == EXPECTED_CONCEPTS
        assert report["drift"] == []
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT count(*) FROM semantic_concepts")).scalar_one()
                == EXPECTED_CONCEPTS
            )

    def test_an_interrupted_run_is_not_repeated(self, clean, actor_id):
        """A concept created but never published still counts as recorded.

        `index_existing` reads revisions rather than `current_revision_id` for
        exactly this case; keying off the current revision would create a
        second copy of every concept a failed run had left in draft.
        """
        engine, _ = clean
        apply_seed(actor_id)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE semantic_concepts SET current_revision_id = NULL, status = 'draft' "
                    "WHERE concept_code = (SELECT min(concept_code) FROM semantic_concepts)"
                )
            )
            connection.execute(
                text(
                    "UPDATE semantic_concept_revisions SET lifecycle_state = 'draft' "
                    "WHERE concept_id IN (SELECT id FROM semantic_concepts WHERE status = 'draft')"
                )
            )
        code, report = apply_seed(actor_id)
        assert report["created"] == 0
        # It is reported as drift rather than silently republished: finishing a
        # half-run concept is a governed transition, not a seed's decision.
        assert [item["differences"] for item in report["drift"]] == [
            ["concept status is draft, not active"]
        ]
        assert code == 1

    def test_changed_content_is_reported_not_rewritten(self, clean, actor_id):
        engine, _ = clean
        apply_seed(actor_id)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE semantic_concept_revisions SET definition = 'edited by a reviewer' "
                    "WHERE preferred_name = 'BallValve'"
                )
            )
        code, report = run("plan")
        assert code == 1
        assert [item["concept_key"] for item in report["drift"]] == ["BallValve"]
        assert report["drift"][0]["differences"] == ["definition differs from the plan"]
        with engine.connect() as connection:
            stored = connection.execute(
                text(
                    "SELECT definition FROM semantic_concept_revisions "
                    "WHERE preferred_name = 'BallValve'"
                )
            ).scalar_one()
        assert stored == "edited by a reviewer"


class TestConceptMap:
    def test_it_hands_the_ingestion_driver_codes_and_assignments(self, clean, actor_id):
        _, tmp_path = clean
        output = tmp_path / "dexpi-concepts.json"
        apply_seed(actor_id, "--output", str(output))
        mapping = json.loads(output.read_text(encoding="utf-8"))
        assert len(mapping["concepts"]) == EXPECTED_CONCEPTS
        assert mapping["missing"] == []
        assert len(mapping["assignments"]) == EXPECTED_ASSIGNMENTS
        codes = {entry["concept_code"] for entry in mapping["concepts"].values()}
        assert len(codes) == EXPECTED_CONCEPTS
        assert all(code.startswith("SGC-") for code in codes)
        # Every symbol's concept resolves to a seeded code, which is what WP4
        # needs to assign a verified primary concept to all 174.
        assert {item["concept_key"] for item in mapping["assignments"]} <= set(mapping["concepts"])
