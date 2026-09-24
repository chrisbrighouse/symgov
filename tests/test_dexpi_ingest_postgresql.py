"""Rehearsal for the DEXPI ingestion driver against a real PostgreSQL (WP4).

Why this file is PostgreSQL-only: every governed row the driver writes lands
in a table whose shape SQLite does not carry. `source_package_entries` has a
partial unique index and a check that an original asset hash must name its
entry, `asset_transformations` is ordered by a per-revision `step_index`,
`symbol_standard_links` has a partial unique index over the live assertion
statuses, and both assignment tables have a partial unique index over the
verified primary. A portable test could assert none of it.

**It ingests a trimmed selection, not all 174.** The full corpus takes about
ten minutes to write on a disposable Debian PostgreSQL, and every behaviour
here is per symbol. The subset is chosen to carry all four of decision D2's
assertion bases and both disciplines, so the paths are covered and the sweep
is not held for ten minutes to re-count numbers `test_dexpi_ingestion.py`
already pins without a database.

Redaction: this file never prints the disposable container's connection
string, and its seeded identity uses a synthetic `@example.test` email.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(Path(__file__).resolve().parent))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

from symgov_backend import dexpi_ingest, dexpi_seed  # noqa: E402
from symgov_backend.models import (  # noqa: E402
    AssetTransformation,
    GovernedSymbol,
    RightsRecord,
    SourcePackageEntry,
    SymbolRevision,
    SymbolRevisionClassificationAssignment,
    SymbolSemanticAssignment,
    SymbolStandardLink,
)
from symgov_backend.publication_gate import (  # noqa: E402
    collect_publication_gate_facts,
    evaluate_publication_gate,
)
from symgov_backend.services.dexpi_concepts import plan_concepts  # noqa: E402
from symgov_backend.services.dexpi_ingestion import PACKAGE_CODE, plan_ingestion  # noqa: E402

SELECTION_PATH = REPO_ROOT / "integrations" / "dexpi" / "selection.json"

NOW = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

# One symbol per decision D2 assertion basis, plus a second of the commonest,
# so every write path runs and the module still finishes in a minute.
_BASES_WANTED = (
    "iso_registration_with_edition",
    "dexpi_reference_shape_name",
    "iso_registration_without_edition",
    "vendor_component_name",
)


def _trimmed_selection() -> dict:
    """A selection carrying one symbol of each assertion basis."""
    full = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    from symgov_backend.services.dexpi_ingestion import standard_assertion

    chosen, seen = [], set()
    for item in full["selected"]:
        basis = standard_assertion(item)["basis"]
        if basis in _BASES_WANTED and basis not in seen:
            seen.add(basis)
            chosen.append(item)
        if len(seen) == len(_BASES_WANTED):
            break
    assert seen == set(_BASES_WANTED), "the selection no longer carries every assertion basis"
    return {**full, "selected": chosen}


@pytest.fixture(scope="module")
def ingest_database():
    if not SELECTION_PATH.is_file():
        pytest.skip("the WP2 selection manifest has not been generated")
    with _database("symgov-dexpi-ingest") as (engine, url, _raw_url):
        _alembic(url, "upgrade", "head")
        yield engine, url


@pytest.fixture(scope="module")
def actor_id(ingest_database) -> uuid.UUID:
    engine, _ = ingest_database
    identifier = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                "must_change_pin,is_active,created_at,updated_at) "
                "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
            ),
            {"id": identifier, "email": "dexpi-ingest@example.test", "now": NOW},
        )
    return identifier


@pytest.fixture(scope="module")
def selection() -> dict:
    return _trimmed_selection()


@pytest.fixture(scope="module")
def ingested(ingest_database, actor_id, selection):
    """One ingestion the whole module reads: it is the thing under test."""
    engine, _url = ingest_database
    concept_plan = plan_concepts(selection)
    with Session(engine) as session, session.begin():
        dexpi_seed.seed(session, plan=concept_plan, actor_id=actor_id, occurred_at=NOW)
    with Session(engine) as session:
        concept_map = dexpi_seed.concept_map(session, concept_plan)
    plan = plan_ingestion(selection, concept_map)
    with Session(engine) as session, session.begin():
        report = dexpi_ingest.ingest(session, plan=plan, actor_id=actor_id, occurred_at=NOW)
    return plan, report


class TestApply:
    def test_it_records_every_planned_symbol_once(self, ingest_database, ingested):
        plan, report = ingested
        engine, _ = ingest_database
        assert len(report["created"]) == len(plan["symbols"])
        assert report["package_code"] == PACKAGE_CODE
        with Session(engine) as session:
            slugs = {symbol["slug"] for symbol in plan["symbols"]}
            stored = set(
                session.execute(
                    text("SELECT slug FROM governed_symbols WHERE slug = ANY(:slugs)"),
                    {"slugs": list(slugs)},
                ).scalars()
            )
        assert stored == slugs

    def test_each_revision_is_approved_and_not_published(self, ingest_database, ingested):
        plan, _ = ingested
        engine, _ = ingest_database
        with Session(engine) as session:
            states = set(
                session.execute(
                    text(
                        "SELECT sr.lifecycle_state FROM symbol_revisions sr "
                        "JOIN governed_symbols gs ON gs.id = sr.symbol_id "
                        "WHERE gs.slug = ANY(:slugs)"
                    ),
                    {"slugs": [symbol["slug"] for symbol in plan["symbols"]]},
                ).scalars()
            )
            catalog_ids = set(
                session.execute(
                    text(
                        "SELECT catalog_symbol_id FROM governed_symbols WHERE slug = ANY(:slugs)"
                    ),
                    {"slugs": [symbol["slug"] for symbol in plan["symbols"]]},
                ).scalars()
            )
        assert states == {"approved"}
        # Nothing here allocates a catalogue identifier, so nothing here can
        # reach the public Catalogue.
        assert catalog_ids == {None}

    def test_the_five_governed_facts_are_all_present(self, ingest_database, ingested):
        plan, _ = ingested
        engine, _ = ingest_database
        with Session(engine) as session:
            for symbol_plan in plan["symbols"]:
                symbol = (
                    session.query(GovernedSymbol).filter_by(slug=symbol_plan["slug"]).one()
                )
                revision = session.get(SymbolRevision, symbol.current_revision_id)
                assert revision is not None

                entry = (
                    session.query(SourcePackageEntry)
                    .filter_by(symbol_revision_id=revision.id)
                    .one()
                )
                assert entry.source_path == symbol_plan["entry"]["source_path"]
                assert entry.original_asset_sha256 == symbol_plan["entry"]["original_asset_sha256"]

                step = (
                    session.query(AssetTransformation)
                    .filter_by(symbol_revision_id=revision.id)
                    .one()
                )
                assert step.step_index == 1
                assert step.source_package_entry_id == entry.id
                # Appendix B.2's chain: the source digest is the entry's, not
                # one the driver restated.
                assert step.source_asset_sha256 == entry.original_asset_sha256
                assert step.derived_asset_sha256 == symbol_plan["transformation"][
                    "derived_asset_sha256"
                ]
                assert step.tool_version

                link = (
                    session.query(SymbolStandardLink)
                    .filter_by(symbol_revision_id=revision.id)
                    .one()
                )
                assert link.assertion_status == "verified"
                assert link.verification_method == "import_manifest"
                # A controlled-system method verifies without a named reviewer.
                assert link.verified_by_user_id is None
                assert link.relationship_type == symbol_plan["assertion"]["relationship_type"]

                assignment = (
                    session.query(SymbolSemanticAssignment)
                    .filter_by(symbol_revision_id=revision.id)
                    .one()
                )
                assert (assignment.status, assignment.assignment_role) == ("verified", "primary")
                assert str(assignment.semantic_concept_id) == symbol_plan["concept"]["concept_id"]

                classification = (
                    session.query(SymbolRevisionClassificationAssignment)
                    .filter_by(symbol_revision_id=revision.id)
                    .one()
                )
                assert classification.status == "verified"

    def test_the_rights_record_is_proposed_and_nothing_approves_it(
        self, ingest_database, ingested
    ):
        _plan, report = ingested
        engine, _ = ingest_database
        with Session(engine) as session:
            record = session.get(RightsRecord, uuid.UUID(report["rights_record_id"]))
            assert record is not None
            # The one irreducibly manual step in the whole plan.
            assert record.decision_status == "proposed"
            assert record.decided_by_user_id is None
            assert record.disposition == "distribute"
            assert record.licence_reference.startswith("https://creativecommons.org/licenses/by/4.0")

    def test_the_package_carries_the_section_1_1_caveat(self, ingest_database, ingested):
        engine, _ = ingest_database
        with Session(engine) as session:
            row = session.execute(
                text(
                    "SELECT release_version, acquisition_method, package_type, metadata_json "
                    "FROM source_packages WHERE package_code = :code"
                ),
                {"code": PACKAGE_CODE},
            ).one()
        assert row.release_version == "DEXPI 1.2/1.3 example corpus"
        assert row.acquisition_method == "public_download"
        # The package type is what puts these symbols in the gate's scope.
        assert row.package_type == "authoritative_library"
        assert row.metadata_json["dexpi_2_0_example_set_published"] is False


class TestRerun:
    def test_a_second_apply_creates_nothing(self, ingest_database, ingested, actor_id):
        plan, _ = ingested
        engine, _ = ingest_database
        with Session(engine) as session, session.begin():
            again = dexpi_ingest.ingest(
                session, plan=plan, actor_id=actor_id, occurred_at=NOW
            )
        assert again["created"] == []
        assert len(again["unchanged"]) == len(plan["symbols"])
        assert again["package_created"] is False
        assert again["rights_record_created"] is False

    def test_an_unknown_actor_records_nothing(self, ingest_database, ingested):
        plan, _ = ingested
        engine, _ = ingest_database
        stranger = uuid.uuid4()
        with pytest.raises(dexpi_ingest.ConfigurationError, match="no user exists"):
            with Session(engine) as session, session.begin():
                dexpi_ingest.ingest(
                    session, plan=plan, actor_id=stranger, occurred_at=NOW
                )


class TestTheGate:
    def test_every_ingested_revision_is_in_scope_and_refused_only_on_rights(
        self, ingest_database, ingested
    ):
        plan, _ = ingested
        engine, _ = ingest_database
        with Session(engine) as session:
            for symbol_plan in plan["symbols"]:
                symbol = (
                    session.query(GovernedSymbol).filter_by(slug=symbol_plan["slug"]).one()
                )
                facts = collect_publication_gate_facts(session, symbol.current_revision_id)
                decision = evaluate_publication_gate(facts)
                # The first `authoritative_library` package in SymGov's history,
                # so these are the first symbols the gate has ever been in scope
                # for.
                assert decision.in_scope is True
                assert decision.outcome == "refused"
                # Expected, and not a defect: a human has not approved the
                # rights record and `transition_rights_record` demands one.
                assert decision.refusal_reasons == ("rights_undecided",)
                satisfied = {
                    result.dimension for result in decision.dimensions if result.satisfied
                }
                assert satisfied == {
                    "semantic_identity",
                    "source",
                    "graphical_authority",
                    "integrity",
                    "classification",
                }
                # No waiver was needed anywhere: decisions D3 and D7 exist so
                # that `semantic_identity` passes on its own.
                assert not any(result.waived for result in decision.dimensions)

    def test_the_rights_approval_is_the_only_thing_between_these_and_publication(
        self, ingest_database, ingested, actor_id
    ):
        """Approving the one rights record permits all of them, and nothing else changes."""
        from symgov_backend.rights_provenance import transition_rights_record

        plan, report = ingested
        engine, _ = ingest_database
        with Session(engine) as session, session.begin():
            transition_rights_record(
                session,
                uuid.UUID(report["rights_record_id"]),
                target_status="approved",
                occurred_at=NOW,
                decided_by_user_id=actor_id,
                decision_reason="Rehearsal: CC BY 4.0 permits redistribution with attribution.",
            )
        try:
            with Session(engine) as session:
                for symbol_plan in plan["symbols"]:
                    symbol = (
                        session.query(GovernedSymbol).filter_by(slug=symbol_plan["slug"]).one()
                    )
                    decision = evaluate_publication_gate(
                        collect_publication_gate_facts(session, symbol.current_revision_id)
                    )
                    assert decision.outcome == "permitted"
                    assert decision.refusal_reasons == ()
                    # Section 13.2's top rung: source, transformation, semantic,
                    # rights and governance provenance all present.
                    assert decision.traceability_level == "T5"
        finally:
            # Leave the module's fixture as the other tests found it.
            with Session(engine) as session, session.begin():
                transition_rights_record(
                    session,
                    uuid.UUID(report["rights_record_id"]),
                    target_status="retired",
                    occurred_at=NOW,
                )
