"""The DISC DEXPI import against a real PostgreSQL, end to end.

PostgreSQL-only for the reasons `test_dexpi_ingest_postgresql` gives: every
governed row lands in a table whose constraints SQLite does not carry. Beyond
the import itself this file proves the surfaces the brief asks for, through the
real routes: the attribution filled from the rights record, geometry and state
variants in the published and Catalog APIs, the opt-in variants download, the
preview that is always the primary, the Support endpoint, Semantic Review's
DEXPI-CLASS rows, and the facet fix.

The package is the small synthetic one in `disc_dexpi_fixtures`, with its own
made-up attribution wording. The tests in this class run in order and share the
database, because the import is the thing under test.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(Path(__file__).resolve().parent))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from disc_dexpi_fixtures import ATTRIBUTION, build_package, change_primary, write_package  # noqa: E402
from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402
from test_semantic_review_routes_postgresql import _client, _login, _user  # noqa: E402

from symgov_backend import disc_dexpi_ingest as ingest  # noqa: E402
from symgov_backend.catalog_facets import compute_catalog_facets  # noqa: E402
from symgov_backend.catalog_api_auth import hash_api_key  # noqa: E402
from symgov_backend.models import (  # noqa: E402
    AssetTransformation,
    Attachment,
    CatalogApiKey,
    ClassificationNode,
    ClassificationScheme,
    ConceptClassificationAssignment,
    ConceptExternalReference,
    GovernedSymbol,
    PackEntry,
    PublishedPage,
    RightsRecord,
    SemanticConcept,
    SourcePackage,
    SourcePackageEntry,
    SymbolRevision,
    SymbolRevisionClassificationAssignment,
    SymbolSemanticAssignment,
    SymbolStandardLink,
)
from symgov_backend.rights_provenance import transition_rights_record  # noqa: E402
from symgov_backend.semantic_concepts import (  # noqa: E402
    create_semantic_concept,
    transition_semantic_concept_revision,
)
from symgov_backend.services import disc_dexpi_ingestion as plan_module  # noqa: E402

psycopg = pytest.importorskip("psycopg")

V1 = "/api/v1"
NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
PILOT_SLUG = "dexpi-ttc-" + "ab" * 16
PILOT_ATTRIBUTION = "The pilot's own stored attribution, which must come back exactly as stored."


class FakeBucket:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put(self, *, object_key, payload, content_type, env_file=None):
        assert content_type == "image/svg+xml"
        self.objects[object_key] = payload
        return {"object_key": object_key}

    def get(self, *, object_key, env_file=None):
        if object_key not in self.objects:
            raise RuntimeError("Storage download failed with HTTP 404")
        return {"object_key": object_key, "payload": self.objects[object_key], "content_type": "image/svg+xml"}


@pytest.fixture(scope="module")
def database():
    with _database("symgov-disc-dexpi") as (engine, url, _raw_url):
        _alembic(url, "upgrade", "head")
        yield engine, url


@pytest.fixture(scope="module")
def env(database, tmp_path_factory):
    """The package on disk, the environment the CLI reads, and the shared state."""
    engine, url = database
    mp = pytest.MonkeyPatch()
    mp.setenv("SYMGOV_DATABASE_URL", url)
    # The fixture library shares two classes with the "pilot" seeded below.
    mp.setattr(plan_module, "EXPECTED_OVERLAP_CLASSES", ("CentrifugalPump",))
    package = build_package()
    directory = write_package(package, tmp_path_factory.mktemp("disc-package"))
    bucket = FakeBucket()
    for target in (
        "symgov_backend.disc_dexpi_ingest.download_object_bytes",
        "symgov_backend.routes.catalog.download_object_bytes",
        "symgov_backend.routes.published.download_object_bytes",
        "symgov_backend.symbol_state_variants.download_object_bytes",
    ):
        mp.setattr(target, bucket.get)
    mp.setattr("symgov_backend.disc_dexpi_ingest.upload_object_bytes", bucket.put)
    yield {"engine": engine, "package": package, "directory": directory, "bucket": bucket, "state": {}}
    mp.undo()


@pytest.fixture(scope="module")
def actor_id(env):
    Session_ = sessionmaker(bind=env["engine"], autoflush=False, expire_on_commit=False)
    return _user(Session_, email="disc-importer@example.test", roles=())


@pytest.fixture(scope="module")
def pilot(env, actor_id):
    """One published pilot symbol, as the TrainingTestCases import leaves them: a
    representation type and no discipline or category assignment, and a heat
    exchanger's name, which is what the keyword rules misread."""
    engine = env["engine"]
    package = plan_module.plan_import(env["package"])
    del package
    with Session(engine) as session, session.begin():
        concept, revision = create_semantic_concept(
            session,
            concept_kind="physical_equipment",
            preferred_name="CentrifugalPump",
            definition="The pilot's concept.",
            created_by_user_id=actor_id,
            created_at=NOW,
        )
        for target_state in ("review", "approved", "published"):
            transition_semantic_concept_revision(
                session, revision.id, target_state=target_state, actor_id=actor_id, occurred_at=NOW
            )
        concept_code = concept.concept_code
    symbol_id, revision_id, page_id, entry_id, pack_id = (uuid.uuid4() for _ in range(5))
    payload = {
        "name": "ShellAndTubeHeatExchanger (HEX 1)",
        "summary": "ShellAndTubeHeatExchanger (HEX 1), converted from the DEXPI 1.2 example corpus.",
        "description": "A heat exchanger as drawn in the DEXPI TrainingTestCases corpus.",
        "keywords": ["DEXPI", "HeatExchanger"],
        "dexpi": {
            "name_basis": "dexpi_component_class",
            "component_classes": ["CentrifugalPump"],
            "attribution": PILOT_ATTRIBUTION,
            "semantic_concept": {"concept_key": "CentrifugalPump", "concept_code": concept_code},
        },
    }
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO publication_packs (id,pack_code,title,audience,effective_date,status,created_at,updated_at) "
            "VALUES (:id,'dexpi-ttc-1-2-1-3','DEXPI pilot','public',DATE '2026-09-24','published',:now,:now)"
        ), {"id": pack_id, "now": NOW})
        connection.execute(text(
            "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,visibility,created_at,updated_at) "
            "VALUES (:id,:slug,'ShellAndTubeHeatExchanger (HEX 1)','Equipment','Piping / P&ID',:owner,'public',:now,:now)"
        ), {"id": symbol_id, "slug": PILOT_SLUG, "owner": actor_id, "now": NOW})
        connection.execute(text(
            "INSERT INTO symbol_revisions (id,symbol_id,revision_label,lifecycle_state,payload_json,author_id,created_at) "
            "VALUES (:id,:symbol,'r1','published',CAST(:payload AS jsonb),:owner,:now)"
        ), {"id": revision_id, "symbol": symbol_id, "owner": actor_id, "now": NOW, "payload": json.dumps(payload)})
        connection.execute(text("UPDATE governed_symbols SET current_revision_id=:r WHERE id=:s"), {"r": revision_id, "s": symbol_id})
        connection.execute(text(
            "INSERT INTO catalog_symbol_identifiers (identifier,role,governed_symbol_id,allocation_source,allocated_at) "
            "VALUES ('TST-1','canonical',:s,'global_sequence',now())"
        ), {"s": symbol_id})
        connection.execute(text("UPDATE governed_symbols SET catalog_symbol_id='TST-1' WHERE id=:s"), {"s": symbol_id})
        connection.execute(text(
            "INSERT INTO published_pages (id,page_code,title,pack_id,current_symbol_revision_id,effective_date,created_at,updated_at) "
            "VALUES (:id,'PAGE-TST-1','HEX',:pack,:r,DATE '2026-09-24',:now,:now)"
        ), {"id": page_id, "pack": pack_id, "r": revision_id, "now": NOW})
        connection.execute(text(
            "INSERT INTO pack_entries (id,pack_id,symbol_revision_id,published_page_id,sort_order,created_at) "
            "VALUES (:id,:pack,:r,:page,1,:now)"
        ), {"id": entry_id, "pack": pack_id, "r": revision_id, "page": page_id, "now": NOW})
    # A search has already stored this symbol's facets under the keyword rules, as
    # production's will have been by the time the import runs.
    with Session(engine) as session, session.begin():
        compute_catalog_facets(session, [revision_id])
    stored = _count(engine, "SELECT disciplines::text FROM catalog_symbol_facets WHERE symbol_revision_id = :r", r=revision_id)
    assert "Fire & Life Safety" in stored
    return {"slug": PILOT_SLUG, "revision_id": revision_id, "concept_code": concept_code}


def _count(engine, sql, **params):
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def _disc_counts(engine):
    return {
        "symbols": _count(engine, "SELECT count(*) FROM governed_symbols WHERE slug LIKE 'disc-dexpi-%'"),
        "revisions": _count(engine, "SELECT count(*) FROM symbol_revisions sr JOIN governed_symbols gs ON gs.id=sr.symbol_id WHERE gs.slug LIKE 'disc-dexpi-%'"),
        "packages": _count(engine, "SELECT count(*) FROM source_packages WHERE package_code='DISC-DEXPI-0.6.3'"),
        "schemes": _count(engine, "SELECT count(*) FROM classification_schemes WHERE scheme_code='DEXPI-CLASS'"),
        "concepts": _count(engine, "SELECT count(*) FROM semantic_concepts"),
        "mappings": _count(engine, "SELECT count(*) FROM concept_external_references"),
    }


def _run(argv, capsys):
    code = ingest.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


class TestDryRun:
    def test_it_reports_counts_and_writes_nothing(self, env, pilot, capsys, tmp_path):
        before = _disc_counts(env["engine"])
        output = tmp_path / "dry.json"
        code, out, err = _run(["apply", "--dry-run", "--package", str(env["directory"]), "--output", str(output)], capsys)
        assert code == 0, err
        report = json.loads(output.read_text())
        assert report["mode"] == "dry-run" and report["blocked"] is False
        assert report["symbols"] == {"would_create": 4, "would_revise": [], "unchanged": 0}
        assert report["package"]["option_assets"] == 2 and report["package"]["with_connection_points"] == 3
        assert report["scheme"]["nodes_total"] == 11 and report["scheme"]["nodes_to_create"] == 11
        assert report["scheme"]["pilot_symbols"] == 1
        assert report["scheme"]["node_code_map"]["package:Equipment"] == "PACKAGE_EQUIPMENT"
        assert report["concepts"]["reuse_existing"] == 1
        assert report["concepts"]["overlap"] == [
            {"concept_key": "CentrifugalPump", "pilot_concept_code": pilot["concept_code"], "matched": True}
        ]
        assert report["external_mappings"]["would_create"] == 3
        assert report["pilot_governed_facets"]["assignments"] == 2
        assert report["display_name_corrections"][0]["changes"] == [["Fuced", "Fixed"]]
        assert _disc_counts(env["engine"]) == before
        assert ATTRIBUTION not in out

    def test_it_stops_on_an_expected_overlap_that_is_missing(self, env, pilot, capsys, monkeypatch, tmp_path):
        monkeypatch.setattr(plan_module, "EXPECTED_OVERLAP_CLASSES", ("CentrifugalPump", "BallValve"))
        output = tmp_path / "blocked.json"
        code, _out, _err = _run(["apply", "--dry-run", "--package", str(env["directory"]), "--output", str(output)], capsys)
        assert code == ingest.EXIT_BLOCKED
        report = json.loads(output.read_text())
        assert report["blocked"] is True
        assert any("BallValve" in reason for reason in report["blocking"])

    def test_a_real_apply_refuses_to_write_when_blocked(self, env, pilot, actor_id, monkeypatch):
        monkeypatch.setattr(plan_module, "EXPECTED_OVERLAP_CLASSES", ("CentrifugalPump", "BallValve"))
        package = ingest.load_package(env["directory"])
        plan = plan_module.plan_import(package)
        before = _disc_counts(env["engine"])
        with pytest.raises(ingest.Blocked):
            with Session(env["engine"]) as session, session.begin():
                ingest.apply(session, plan=plan, package_data=package, actor_id=actor_id, occurred_at=NOW)
        assert _disc_counts(env["engine"]) == before

    def test_a_near_miss_concept_blocks_before_anything_is_duplicated(self, env, pilot, actor_id):
        package = ingest.load_package(env["directory"])
        plan = plan_module.plan_import(package)
        with Session(env["engine"]) as session:
            state = ingest.read_state(session, plan)
            state["concepts"] = {**state["concepts"], "shell and fixed tube heat exchanger": {
                "concept_id": uuid.uuid4(), "concept_code": "SGC-99999999", "status": "active",
                "concept_kind": "physical_equipment", "lifecycle_state": "published", "definition": "x", "aliases": []}}
            decision = ingest.decide(plan, state)
            session.rollback()
        assert any("nearly matches" in reason for reason in decision["blocking"])


class TestApply:
    def test_it_records_the_library(self, env, pilot, actor_id):
        package = ingest.load_package(env["directory"])
        plan = plan_module.plan_import(package)
        with Session(env["engine"]) as session, session.begin():
            report = ingest.apply(session, plan=plan, package_data=package, actor_id=actor_id, occurred_at=NOW)
        env["state"]["apply"] = report
        assert len(report["symbols_created"]) == 4
        assert report["concepts_reused"] == [{"concept_key": "CentrifugalPump", "concept_code": pilot["concept_code"]}]
        assert len(report["concepts_created"]) == len(plan["concepts"]) - 1
        assert report["external_mappings"] == {"created": 3, "existing": 0}
        assert report["pilot_classification"] == {"classified": 1, "already": 0, "no_target": 0}
        assert report["pilot_governed_facets"]["assignments"] == 2
        assert report["rights_decision_status"] == "proposed"

    def test_the_source_package_and_rights_record_hold_the_attribution_once(self, env):
        with Session(env["engine"]) as session:
            package = session.query(SourcePackage).filter_by(package_code="DISC-DEXPI-0.6.3").one()
            assert package.source_uri == "https://example.test/disc-library/tree/main"
            assert package.metadata_json["source_commit"] == "0123456789abcdef0123456789abcdef01234567"
            assert package.metadata_json["licensor"] == "A. Licensor"
            rights = session.query(RightsRecord).filter_by(source_package_id=package.id).one()
            assert (rights.rights_status, rights.disposition, rights.decision_status) == ("licensed", "distribute", "proposed")
            assert rights.determination_method == "manual"
            assert rights.licence_reference.startswith("Permission granted by Tonia Pedersen to Chris Brighouse, Oct 2026")
            assert rights.evidence_json["attribution_text"] == ATTRIBUTION
            assert rights.evidence_json["attribution_is_placeholder"] is True
            # Nowhere else stores it: not the package row, not any payload.
            assert ATTRIBUTION not in json.dumps(package.metadata_json)
            assert ATTRIBUTION not in (package.release_version or "") + (package.title or "") + (package.provider or "")
        stored_payloads = _count(
            env["engine"],
            "SELECT count(*) FROM symbol_revisions sr JOIN governed_symbols gs ON gs.id=sr.symbol_id "
            "WHERE gs.slug LIKE 'disc-dexpi-%' AND CAST(sr.payload_json AS text) LIKE :needle",
            needle=f"%{ATTRIBUTION}%",
        )
        assert stored_payloads == 0

    def test_each_revision_has_its_governed_facts(self, env):
        with Session(env["engine"]) as session:
            symbols = session.query(GovernedSymbol).filter(GovernedSymbol.slug.like("disc-dexpi-%")).all()
            assert len(symbols) == 4
            for symbol in symbols:
                revision = session.get(SymbolRevision, symbol.current_revision_id)
                assert revision.revision_label == "r1" and revision.lifecycle_state == "approved"
                assert symbol.catalog_symbol_id is None  # nothing here publishes
                entry = session.query(SourcePackageEntry).filter_by(symbol_revision_id=revision.id).one()
                assert entry.provider_entry_identifier == symbol.slug.rsplit("-", 1)[-1].upper()
                step = session.query(AssetTransformation).filter_by(symbol_revision_id=revision.id).one()
                assert step.derived_asset_sha256 == revision.payload_json["assets"][0]["sha256"]
                assert step.tool_name == "convert_disc.py"
                link = session.query(SymbolStandardLink).filter_by(symbol_revision_id=revision.id).one()
                assert (link.assertion_status, link.verification_method) == ("verified", "import_manifest")
                assignment = session.query(SymbolSemanticAssignment).filter_by(symbol_revision_id=revision.id).one()
                assert (assignment.status, assignment.method) == ("verified", "source_mapping")
                methods = {
                    (row.method, row.status)
                    for row in session.query(SymbolRevisionClassificationAssignment).filter_by(symbol_revision_id=revision.id)
                }
                assert methods == {("source_mapping", "verified"), ("rule", "verified")}
                assert len(list(session.query(SymbolRevisionClassificationAssignment).filter_by(symbol_revision_id=revision.id))) == 4

    def test_options_are_stored_as_option_assets_with_their_condition(self, env):
        with env["engine"].connect() as connection:
            attachments = connection.execute(
                text(
                    "SELECT a.asset_role, a.option_index, a.condition, a.object_key FROM attachments a "
                    "JOIN governed_symbols gs ON gs.current_revision_id = a.parent_id "
                    "WHERE gs.slug = 'disc-dexpi-nd0004' AND a.parent_type = 'symbol_revision' "
                    "ORDER BY a.asset_role DESC, a.option_index"
                )
            ).all()
        assert [(a.asset_role, a.option_index, a.condition) for a in attachments] == [
            ("primary", None, None), ("option", 1, "ValvePosition = 'NC'"), ("option", 2, None)
        ]
        assert all("/r1/" in a.object_key for a in attachments)
        total_options = _count(env["engine"], "SELECT count(*) FROM attachments WHERE asset_role='option'")
        assert total_options == env["package"]["report"]["option_assets"]

    def test_register_warnings_are_private_and_not_in_the_payload(self, env):
        def warnings_of(slug):
            with env["engine"].connect() as connection:
                return connection.execute(
                    text(
                        "SELECT e.import_warnings_json FROM source_package_entries e "
                        "JOIN governed_symbols gs ON gs.current_revision_id = e.symbol_revision_id WHERE gs.slug = :slug"
                    ),
                    {"slug": slug},
                ).scalar_one()

        assert warnings_of("disc-dexpi-nd0004") == ["Origo and base SVG differ in Symbol cells; base SVG geometry used"]
        assert warnings_of("disc-dexpi-nd0136") == []
        with Session(env["engine"]) as session:
            valve = session.query(GovernedSymbol).filter_by(slug="disc-dexpi-nd0004").one()
            revision = session.get(SymbolRevision, valve.current_revision_id)
            assert "Origo" not in json.dumps(revision.payload_json)
        assert _count(env["engine"], "SELECT count(*) FROM source_package_entries WHERE jsonb_array_length(import_warnings_json) > 0") == 2

    def test_the_dexpi_class_scheme_has_every_node_active_with_the_source_name_as_alias(self, env):
        with Session(env["engine"]) as session:
            scheme = session.query(ClassificationScheme).filter_by(scheme_code="DEXPI-CLASS").one()
            assert scheme.status == "active"
            nodes = {n.node_code: n for n in session.query(ClassificationNode).filter_by(scheme_id=scheme.id)}
            # 11 from the package, plus the pilot's class which the DEXPI list has as CentrifugalPump already.
            assert len(nodes) == 11
            assert {n.status for n in nodes.values()} == {"active"}
            custom = nodes["DOUBLE_BLOCK_AND_BLEED_VALVE"]
            assert custom.preferred_label == "DoubleBlockAndBleedValve"
            extras = {
                row.node_code: (row.aliases_json, row.source_label)
                for row in session.execute(
                    text("SELECT node_code, aliases_json, source_label FROM classification_nodes WHERE scheme_id = :s"),
                    {"s": scheme.id},
                )
            }
            assert extras["DOUBLE_BLOCK_AND_BLEED_VALVE"] == (
                ["DoubleBlockAndBleedValve", "Double Block And Bleed Valve"], "DISC Symbols.xlsm custom type"
            )
            assert nodes["CUSTOM_OPERATED_VALVE"].id == custom.parent_node_id
            assert nodes["EQUIPMENT"].parent_node_id == nodes["PACKAGE_EQUIPMENT"].id
            assert nodes["PACKAGE_EQUIPMENT"].parent_node_id is None
            assert extras["BALL_VALVE"][0] == ["BallValve"]
            assert extras["PACKAGE_EQUIPMENT"][1] == "DEXPI 1.3 package"

    def test_new_concepts_are_published_and_sit_under_their_dexpi_node(self, env, pilot):
        with Session(env["engine"]) as session:
            concept = session.query(SemanticConcept).filter_by(
                concept_code=next(c["concept_code"] for c in env["state"]["apply"]["concepts_created"] if c["concept_key"] == "DoubleBlockAndBleedValve")
            ).one()
            assert concept.status == "active"
            assignment = session.query(ConceptClassificationAssignment).filter_by(semantic_concept_id=concept.id).one()
            node = session.get(ClassificationNode, assignment.classification_node_id)
            assert node.node_code == "DOUBLE_BLOCK_AND_BLEED_VALVE"
            assert (assignment.status, assignment.method, assignment.assignment_role) == ("verified", "source_mapping", "primary")

    def test_external_mappings_arrive_verified_as_imported_and_per_concept(self, env):
        with Session(env["engine"]) as session:
            rows = session.query(ConceptExternalReference).all()
            assert len(rows) == 3
            assert {(r.mapping_type, r.mapping_status, r.mapping_method) for r in rows} == {
                ("broader", "verified", "imported"), ("exact", "verified", "imported")
            }
            exact = next(r for r in rows if r.external_identifier == "http://rdl.example.test/RDS552689")
            assert exact.evidence_json["source_commit"] == "0123456789abcdef0123456789abcdef01234567"
        schemes = _count(env["engine"], "SELECT count(*) FROM external_semantic_schemes WHERE scheme_code IN ('DEXPI-RDL','ISO15926-RDL-PCA','DISC-NOAKA-RDL')")
        assert schemes == 3

    def test_the_pilot_symbol_is_classified_and_its_facets_are_now_governed(self, env, pilot):
        with Session(env["engine"]) as session:
            rows = (
                session.query(SymbolRevisionClassificationAssignment, ClassificationScheme.scheme_code)
                .join(ClassificationScheme, ClassificationScheme.id == SymbolRevisionClassificationAssignment.classification_scheme_id)
                .filter(SymbolRevisionClassificationAssignment.symbol_revision_id == pilot["revision_id"])
                .all()
            )
            by_scheme = {code: (a.method, a.status) for a, code in rows}
            assert by_scheme["DEXPI-CLASS"] == ("source_mapping", "verified")
            assert by_scheme["ENGINEERING-DISCIPLINE"] == ("legacy_backfill", "proposed")
            assert by_scheme["SYMBOL-CATEGORY-FAMILY"] == ("legacy_backfill", "proposed")

    def test_a_second_run_changes_nothing(self, env, pilot, actor_id):
        before = _disc_counts(env["engine"])
        package = ingest.load_package(env["directory"])
        plan = plan_module.plan_import(package)
        with Session(env["engine"]) as session, session.begin():
            report = ingest.apply(session, plan=plan, package_data=package, actor_id=actor_id, occurred_at=NOW + timedelta(minutes=1))
        assert report["symbols_created"] == [] and report["symbols_revised"] == [] and report["symbols_unchanged"] == 4
        assert report["concepts_created"] == [] and report["scheme_nodes_created"] == 0
        assert report["external_mappings"] == {"created": 0, "existing": 3}
        assert report["pilot_classification"] == {"classified": 0, "already": 1, "no_target": 0}
        assert report["pilot_governed_facets"]["assignments"] == 0
        assert _disc_counts(env["engine"]) == before


class TestPublication:
    def test_upload_puts_every_primary_and_option_in_the_bucket(self, env):
        package = ingest.load_package(env["directory"])
        plan = plan_module.plan_import(package)
        with Session(env["engine"]) as session:
            dry = ingest.upload_assets(session, plan, package, storage_env_file=None, dry_run=True, uploader=env["bucket"].put)
            assert dry["attachments"] == 6 and dry["uploaded_count"] == 0 and env["bucket"].objects == {}
            report = ingest.upload_assets(session, plan, package, storage_env_file=None, dry_run=False, uploader=env["bucket"].put)
            session.rollback()
        assert (report["primary"], report["options"], report["uploaded_count"]) == (4, 2, 6)
        assert len(env["bucket"].objects) == 6
        # What is stored is what the package holds, byte for byte, metadata included.
        for name, data in package["svg"].items():
            assert data in env["bucket"].objects.values()
            assert ATTRIBUTION.encode() in data

    def test_the_gate_refuses_until_a_named_person_approves_the_rights_record(self, env, actor_id):
        plan = plan_module.plan_import(ingest.load_package(env["directory"]))
        with Session(env["engine"]) as session, session.begin():
            report = ingest.evaluate_gate(session, plan=plan, occurred_at=NOW, actor_id=actor_id)
        assert report["refusal_reasons"] == {"rights_undecided": 4}
        with Session(env["engine"]) as session, session.begin():
            with pytest.raises(ingest.PublicationRefused):
                ingest.publish(session, plan=plan, actor_id=actor_id, occurred_at=NOW,
                               storage_env_file=None, dry_run=False, fetcher=env["bucket"].get)
        assert _count(env["engine"], "SELECT count(*) FROM published_pages WHERE page_code LIKE 'disc-dexpi%'") == 0

    def test_licensed_distribute_is_accepted_by_the_gate_once_approved(self, env, actor_id):
        with Session(env["engine"]) as session, session.begin():
            record = session.query(RightsRecord).filter_by(decision_status="proposed").one()
            transition_rights_record(
                session, record.id, target_status="approved", occurred_at=NOW,
                decided_by_user_id=actor_id, decision_reason="Permission granted; approved for the test.",
            )
        plan = plan_module.plan_import(ingest.load_package(env["directory"]))
        with Session(env["engine"]) as session, session.begin():
            report = ingest.evaluate_gate(session, plan=plan, occurred_at=NOW, actor_id=actor_id)
        assert report["outcomes"] == {"permitted": 4} and report["traceability_levels"] == {"T5": 4}

    def test_publish_refuses_when_an_object_is_missing_and_publishes_nothing(self, env, actor_id):
        plan = plan_module.plan_import(ingest.load_package(env["directory"]))
        removed = next(key for key in env["bucket"].objects if "option2" in key)
        saved = env["bucket"].objects.pop(removed)
        try:
            with Session(env["engine"]) as session, session.begin():
                with pytest.raises(ingest.PublicationRefused) as refused:
                    ingest.publish(session, plan=plan, actor_id=actor_id, occurred_at=NOW,
                                   storage_env_file=None, dry_run=False, fetcher=env["bucket"].get)
            assert "storage_problems" in refused.value.report
        finally:
            env["bucket"].objects[removed] = saved
        assert _count(env["engine"], "SELECT count(*) FROM published_pages WHERE page_code LIKE 'disc-dexpi%'") == 0

    def test_publish_dry_run_then_real(self, env, actor_id):
        plan = plan_module.plan_import(ingest.load_package(env["directory"]))
        with Session(env["engine"]) as session, session.begin():
            dry = ingest.publish(session, plan=plan, actor_id=actor_id, occurred_at=NOW,
                                 storage_env_file=None, dry_run=True, fetcher=env["bucket"].get)
            session.rollback()
        assert dry["would_publish"] == 4
        with Session(env["engine"]) as session, session.begin():
            report = ingest.publish(session, plan=plan, actor_id=actor_id, occurred_at=NOW,
                                    storage_env_file=None, dry_run=False, fetcher=env["bucket"].get)
        assert report["published_count"] == 4 and report["publication_pack_code"] == "disc-dexpi"
        env["state"]["published"] = {item["slug"]: item for item in report["published"]}
        assert all(item["catalog_symbol_id"] for item in report["published"])
        with Session(env["engine"]) as session:
            pack = session.execute(text("SELECT title, audience, status FROM publication_packs WHERE pack_code='disc-dexpi'")).one()
            assert tuple(pack) == ("DISC DEXPI symbol library (DISC Profile 0.6.3)", "public", "published")
        # Run again: nothing left to publish.
        with Session(env["engine"]) as session, session.begin():
            again = ingest.publish(session, plan=plan, actor_id=actor_id, occurred_at=NOW,
                                   storage_env_file=None, dry_run=False, fetcher=env["bucket"].get)
        assert again["published_count"] == 0 and again["unchanged_count"] == 4


@pytest.fixture(scope="module")
def reader(env, pilot):
    """A signed-in personal session, and one with the reviewer role."""
    Session_ = sessionmaker(bind=env["engine"], autoflush=False, expire_on_commit=False)
    client, _ = _client(env["engine"])
    _user(Session_, email="disc-reader@example.test", roles=())
    _login(client, "disc-reader@example.test")
    reviewer, _ = _client(env["engine"])
    _user(Session_, email="disc-reviewer@example.test", roles=("reviewer",))
    _login(reviewer, "disc-reviewer@example.test")
    return client, reviewer


def _item(client, ref):
    response = client.get(f"{V1}/published/symbols/{ref}")
    assert response.status_code == 200, response.text
    return response.json()["item"]


class TestApis:
    def test_a_published_symbol_carries_its_geometry_options_and_the_attribution_from_the_record(self, env, reader):
        client, _ = reader
        ref = env["state"]["published"]["disc-dexpi-nd0004"]["catalog_symbol_id"]
        item = _item(client, ref)
        assert item["payload"]["dexpi"]["attribution"] == ATTRIBUTION
        assert item["payload"]["dexpi"]["attribution_source"] == "rights_record"
        points = item["payload"]["geometry"]["connection_points"]
        assert [(p["x_mm"], p["y_mm"]) for p in points] == [(9.0, 0.0625), (-9.0, 0.0625), (0.0, -8.9375)]
        assert item["payload"]["geometry"]["label_slots"][0]["label_index"] == "A"
        assert item["payload"]["disc"]["disc_id"] == "ND0004"
        assert [(v["index"], v["condition"]) for v in item["stateVariants"]] == [(1, "ValvePosition = 'NC'"), (2, None)]
        assert item["stateVariants"][0]["url"] == f"/api/v1/published/symbols/{ref}/state-variants/1"
        # Served, not stored: the database row still has none.
        assert _count(env["engine"], "SELECT count(*) FROM symbol_revisions WHERE CAST(payload_json AS text) LIKE :n", n=f"%{ATTRIBUTION}%") == 0

    def test_a_pilot_symbol_returns_the_attribution_it_stores_exactly_as_before(self, reader):
        client, _ = reader
        item = _item(client, "TST-1")
        assert item["payload"]["dexpi"]["attribution"] == PILOT_ATTRIBUTION
        assert "stateVariants" in item and item["stateVariants"] == []

    def test_preview_is_always_the_primary_never_an_option(self, env, reader):
        client, _ = reader
        ref = env["state"]["published"]["disc-dexpi-nd0004"]["catalog_symbol_id"]
        primary = env["package"]["svg"]["ND0004.svg"]
        for query in ("", "?format=svg"):
            response = client.get(f"{V1}/published/symbols/{ref}/preview{query}")
            assert response.status_code == 200, response.text
            assert response.content == primary
            assert response.content != env["package"]["svg"]["ND0004_option1.svg"]

    def test_a_state_variant_is_served_by_its_number_and_only_by_its_own_symbol(self, env, reader):
        client, _ = reader
        ref = env["state"]["published"]["disc-dexpi-nd0004"]["catalog_symbol_id"]
        response = client.get(f"{V1}/published/symbols/{ref}/state-variants/2")
        assert response.status_code == 200 and response.content == env["package"]["svg"]["ND0004_option2.svg"]
        assert ATTRIBUTION.encode() in response.content
        assert client.get(f"{V1}/published/symbols/{ref}/state-variants/3").status_code == 404
        other = env["state"]["published"]["disc-dexpi-nd0136"]["catalog_symbol_id"]
        assert client.get(f"{V1}/published/symbols/{other}/state-variants/1").status_code == 404

    def test_the_default_svg_download_is_the_primary_only_and_keeps_its_metadata(self, env, reader):
        client, _ = reader
        ref = env["state"]["published"]["disc-dexpi-nd0004"]["catalog_symbol_id"]
        response = client.post(f"{V1}/catalog/symbols/download", json={"symbolIds": [ref], "format": "SVG"})
        assert response.status_code == 200, response.text
        assert response.content == env["package"]["svg"]["ND0004.svg"]
        assert ATTRIBUTION.encode() in response.content
        assert "X-Symgov-State-Variants-Count" not in response.headers

    def test_state_variants_are_an_opt_in_zip_with_a_manifest(self, env, reader):
        client, _ = reader
        ref = env["state"]["published"]["disc-dexpi-nd0004"]["catalog_symbol_id"]
        response = client.post(
            f"{V1}/catalog/symbols/download", json={"symbolIds": [ref], "format": "SVG", "includeStateVariants": True}
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/zip"
        assert response.headers["X-Symgov-State-Variants-Count"] == "2"
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = archive.namelist()
        assert len(names) == 4 and "state-variants.json" in names
        stem = next(name for name in names if name.endswith(".svg") and "_option" not in name)[:-4]
        assert archive.read(f"{stem}.svg") == env["package"]["svg"]["ND0004.svg"]
        assert archive.read(f"{stem}_option1.svg") == env["package"]["svg"]["ND0004_option1.svg"]
        manifest = json.loads(archive.read("state-variants.json"))["stateVariants"]
        assert [(m["index"], m["condition"]) for m in manifest] == [(1, "ValvePosition = 'NC'"), (2, None)]

    def test_the_variants_option_changes_nothing_for_a_symbol_without_any(self, env, reader):
        client, _ = reader
        ref = env["state"]["published"]["disc-dexpi-nd0136"]["catalog_symbol_id"]
        response = client.post(
            f"{V1}/catalog/symbols/download", json={"symbolIds": [ref], "format": "SVG", "includeStateVariants": True}
        )
        assert response.status_code == 200
        assert response.content == env["package"]["svg"]["ND0136.svg"]

    def test_the_download_request_still_refuses_unknown_and_malformed_fields(self, env, reader):
        client, _ = reader
        ref = env["state"]["published"]["disc-dexpi-nd0004"]["catalog_symbol_id"]
        assert client.post(f"{V1}/catalog/symbols/download", json={"symbolIds": [ref], "format": "SVG", "extra": 1}).status_code == 400
        assert client.post(f"{V1}/catalog/symbols/download", json={"symbolIds": [ref], "format": "SVG", "includeStateVariants": "yes"}).status_code == 400
        assert client.post(f"{V1}/catalog/symbols/download", json={"symbolIds": [ref]}).status_code == 400

    def _api_key(self, env):
        token = "symgov_live_disc_test_token"
        with Session(env["engine"]) as session, session.begin():
            session.add(CatalogApiKey(
                id=uuid.uuid4(), customer_name="Test", integration_name="DISC", key_prefix=token[:16],
                key_hash=hash_api_key(token), scopes_json=["catalog.read"], status="active",
                expires_at=datetime.now(timezone.utc) + timedelta(days=1), created_at=NOW, updated_at=NOW,
            ))
        return {"Authorization": f"Bearer {token}"}

    def test_the_catalog_api_exposes_geometry_options_attribution_and_state_variant_bytes(self, env):
        from symgov_backend.catalog_api_auth import hash_api_key as _  # noqa: F401

        client, _ = _client(env["engine"])
        headers = self._api_key(env)
        ref = env["state"]["published"]["disc-dexpi-nd0004"]["catalog_symbol_id"]
        detail = client.get(f"{V1}/catalog/symbols/{ref}", headers=headers)
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["provenance"]["attribution"] == ATTRIBUTION
        assert len(body["geometry"]["connection_points"]) == 3
        assert body["disc"]["label_templates"] == {"A": "<ObjectDisplayName>"}
        assert [(v["index"], v["condition"], v["url"]) for v in body["stateVariants"]] == [
            (1, "ValvePosition = 'NC'", f"/api/v1/catalog/symbols/{ref}/state-variants/1"),
            (2, None, f"/api/v1/catalog/symbols/{ref}/state-variants/2"),
        ]
        served = client.get(f"{V1}/catalog/symbols/{ref}/state-variants/1", headers=headers)
        assert served.status_code == 200 and served.content == env["package"]["svg"]["ND0004_option1.svg"]
        assert client.get(f"{V1}/catalog/symbols/{ref}/state-variants/9", headers=headers).status_code == 404
        preview = client.get(f"{V1}/catalog/symbols/{ref}/preview", headers=headers)
        assert preview.status_code == 200 and preview.content == env["package"]["svg"]["ND0004.svg"]
        plain = client.get(f"{V1}/catalog/symbols/{env['state']['published']['disc-dexpi-nd0136']['catalog_symbol_id']}", headers=headers).json()
        assert "stateVariants" not in plain and len(plain["geometry"]["connection_points"]) == 2
        pilot = client.get(f"{V1}/catalog/symbols/TST-1", headers=headers).json()
        assert "attribution" not in pilot["provenance"] and "geometry" not in pilot

    def test_support_lists_the_library_from_its_record_only_once_the_rights_are_approved(self, env, reader):
        client, _ = reader
        body = client.get(f"{V1}/published/data-sources").json()
        assert [s["packageCode"] for s in body["sources"]] == ["DISC-DEXPI-0.6.3"]
        source = body["sources"][0]
        assert source["attributionText"] == ATTRIBUTION and source["attributionIsPlaceholder"] is True
        assert source["publishedSymbols"] == 4 and source["title"] == "DISC DEXPI symbol library (DISC Profile 0.6.3)"
        assert (source["rightsStatus"], source["disposition"], source["licensor"]) == ("licensed", "distribute", "A. Licensor")
        assert source["sourceUri"] == "https://example.test/disc-library/tree/main"

    def test_semantic_review_lists_dexpi_class_assignments_for_both_libraries(self, reader):
        _, reviewer = reader
        response = reviewer.get(
            f"{V1}/semantic-review/queues/symbol-classifications",
            params={"status": "verified", "method": "source_mapping", "schemeCode": "DEXPI-CLASS", "limit": 100},
        )
        assert response.status_code == 200, response.text
        rows = response.json()["items"]
        assert {row["schemeCode"] for row in rows} == {"DEXPI-CLASS"}
        assert {row["method"] for row in rows} == {"source_mapping"}
        assert len(rows) == 5  # four DISC symbols and the pilot symbol
        nodes = {row["nodeCode"] for row in rows}
        assert {"DOUBLE_BLOCK_AND_BLEED_VALVE", "CENTRIFUGAL_PUMP"} <= nodes

    def test_dexpi_class_is_read_only_no_reviewer_can_propose_into_it(self, env, reader):
        _, reviewer = reader
        with Session(env["engine"]) as session:
            node_id = session.query(ClassificationNode.id).filter_by(node_code="BALL_VALVE").scalar()
            revision_id = session.query(GovernedSymbol.current_revision_id).filter_by(slug="disc-dexpi-nd0136").scalar()
        response = reviewer.post(
            f"{V1}/semantic-review/symbol-revisions/{revision_id}/classifications",
            json={"classificationNodeId": str(node_id), "assignmentRole": "secondary", "method": "manual"},
        )
        assert response.status_code == 422, response.text
        offered = reviewer.get(f"{V1}/semantic-review/classification-schemes").json()["items"]
        assert "DEXPI-CLASS" not in {scheme["schemeCode"] for scheme in offered}


class TestFacets:
    def test_no_heat_exchanger_is_filed_under_fire_and_life_safety(self, env, reader):
        client, _ = reader
        body = client.get(f"{V1}/published/symbols/search", params={"pageSize": 200}).json()
        assert body["total"] == 5
        for item in body["items"]:
            assert "Fire & Life Safety" not in item["catalogDisciplines"] if "catalogDisciplines" in item else True
        disciplines = {entry["value"] for entry in body["facets"]["catalogDisciplines"]}
        categories = {entry["value"] for entry in body["facets"]["catalogCategories"]}
        assert "Fire & Life Safety" not in disciplines
        assert not {"Fire Alarm Devices", "Sensors / Detectors"} & categories
        hot = client.get(f"{V1}/published/symbols/search", params={"catalogDisciplines": "Fire & Life Safety"}).json()
        assert hot["total"] == 0
        # The pilot's row was stored under the keyword rules before the import; it was recomputed.
        stored = _count(
            env["engine"],
            "SELECT disciplines::text || categories::text FROM catalog_symbol_facets f JOIN governed_symbols gs ON gs.id = f.governed_symbol_id WHERE gs.slug = :s",
            s=PILOT_SLUG,
        )
        assert "Fire" not in stored and "Equipment" in stored

    def test_the_pack_filter_counts_each_library(self, reader):
        client, _ = reader
        disc = client.get(f"{V1}/published/symbols/search", params={"pack": "DISC DEXPI symbol library (DISC Profile 0.6.3)", "pageSize": 200}).json()
        assert disc["total"] == 4
        pilot = client.get(f"{V1}/published/symbols/search", params={"pack": "DEXPI pilot", "pageSize": 200}).json()
        assert pilot["total"] == 1

    def test_the_disc_heat_exchanger_shows_its_corrected_name_and_equipment(self, env, reader):
        client, _ = reader
        item = _item(client, env["state"]["published"]["disc-dexpi-nd0114"]["catalog_symbol_id"])
        assert item["name"] == "Exch. Shell and Fixed Tube (DISC ND0114)"
        assert item["category"] == "Equipment"
        body = client.get(f"{V1}/published/symbols/search", params={"catalogCategories": "Equipment", "pageSize": 50}).json()
        assert item["catalogSymbolId"] in {i["catalogSymbolId"] for i in body["items"]}


class TestVerifyAndReport:
    def test_every_acceptance_check_passes_on_a_clean_import(self, env, reader, capsys, tmp_path):
        output = tmp_path / "verify.json"
        code, out, err = _run(
            ["verify", "--package", str(env["directory"]), "--expected-pilot-count", "1", "--output", str(output)], capsys
        )
        report = json.loads(output.read_text())
        assert code == 0, (err, [c for c in report["checks"] if c["status"] != "pass"])
        assert report["passed"] is True
        assert {c["id"] for c in report["checks"]} >= {
            "pack-count", "pilot-count", "stored-as-packaged", "counts", "dexpi-class-disc", "dexpi-class-pilot",
            "concepts", "external-mappings", "rights-record", "svg-metadata", "support-section",
            "no-heat-exchanger-in-fire", "held-back-absent",
        }
        assert ATTRIBUTION not in out
        env["state"]["verify"] = output

    def test_a_stored_file_that_lost_its_metadata_fails_the_check(self, env, capsys, tmp_path):
        key = next(k for k in env["bucket"].objects if k.endswith(".svg") and "/primary-" in k and "nd0050" in k)
        saved = env["bucket"].objects[key]
        env["bucket"].objects[key] = saved.replace(ATTRIBUTION.encode(), b"")
        try:
            output = tmp_path / "tampered.json"
            code, _out, _err = _run(["verify", "--package", str(env["directory"]), "--output", str(output)], capsys)
        finally:
            env["bucket"].objects[key] = saved
        report = json.loads(output.read_text())
        assert code == 1 and report["failed"] == ["svg-metadata"]

    def test_a_held_back_symbol_that_slipped_in_fails_the_check(self, env, actor_id, capsys, tmp_path):
        with Session(env["engine"]) as session, session.begin():
            owner = session.query(GovernedSymbol.owner_id).filter_by(slug="disc-dexpi-nd0050").scalar()
            session.add(GovernedSymbol(id=uuid.uuid4(), slug="disc-dexpi-nd0900", canonical_name="Held back", category="Valves",
                                       discipline="Piping / P&ID", owner_id=owner, visibility="public",
                                       organization_wide=False, created_at=NOW, updated_at=NOW))
        try:
            output = tmp_path / "slipped.json"
            code, _o, _e = _run(["verify", "--package", str(env["directory"]), "--no-storage", "--output", str(output)], capsys)
            assert code == 1 and json.loads(output.read_text())["failed"] == ["held-back-absent"]
        finally:
            with Session(env["engine"]) as session, session.begin():
                session.query(GovernedSymbol).filter_by(slug="disc-dexpi-nd0900").delete()

    def test_the_facet_change_list_and_the_report(self, env, capsys, tmp_path):
        facets = tmp_path / "facets.json"
        code, _out, _err = _run(["facet-changes", "--output", str(facets)], capsys)
        assert code == 0
        changes = json.loads(facets.read_text())["changes"]
        # The pilot heat exchanger loses Fire & Life Safety, and so would each DISC heat exchanger have.
        names = {item["name"] for item in changes}
        assert "ShellAndTubeHeatExchanger (HEX 1)" in names
        pilot = next(item for item in changes if item["name"].startswith("ShellAndTube"))
        browser = {c["facet"]: c for c in pilot["changes"] if c["view"] == "browser"}
        assert "Fire & Life Safety" in browser["disciplines"]["old"] and "Fire & Life Safety" not in browser["disciplines"]["new"]
        assert "Fire Alarm Devices" in browser["categories"]["old"] and browser["categories"]["new"] == ["Equipment"]

        report_path = tmp_path / "IMPORT_REPORT.md"
        apply_path = tmp_path / "apply.json"
        apply_path.write_text(json.dumps(env["state"]["apply"]))
        code, _out, err = _run(
            ["report", "--package", str(env["directory"]), "--out", str(report_path),
             "--apply-report", str(apply_path), "--verify-report", str(env["state"]["verify"]),
             "--facet-changes", str(facets), "--backup", "/data/symgov-backups/example.dump",
             "--commit", "abc1234", "--rights-approver", "A. Approver"],
            capsys,
        )
        assert code == 0, err
        markdown = report_path.read_text()
        for heading in ("## Summary", "## Rights and attribution", "## Things to tell Tonia Pedersen", "## Held-back symbols",
                        "## Register warnings recorded for review", "## DEXPI-CLASS scheme", "## Concepts",
                        "## External mappings", "## Catalog facet fix", "## Acceptance checks"):
            assert heading in markdown, heading
        assert "\"Fuced\"" in markdown and "\"Fixed\"" in markdown
        assert "/data/symgov-backups/example.dump" in markdown and "A. Approver" in markdown
        assert "PACKAGE_EQUIPMENT" in markdown and "DOUBLE_BLOCK_AND_BLEED_VALVE" in markdown
        assert "ND0900" in markdown and "register mapping status TBD" in markdown
        assert "Origo and base SVG differ" in markdown
        # One place only: the report names the record, and never repeats the wording.
        assert ATTRIBUTION not in markdown


class TestRevisions:
    def test_a_changed_drawing_is_a_new_revision_and_replaces_the_old_in_the_catalogue(self, env, reader, actor_id):
        changed = change_primary(env["package"], "ND0136", "a corrected pump drawing")
        directory = write_package(changed, env["directory"].parent / "disc-package-v2")
        package = ingest.load_package(directory)
        plan = plan_module.plan_import(package)
        with Session(env["engine"]) as session:
            state = ingest.read_state(session, plan)
            decision = ingest.decide(plan, state)
            session.rollback()
        assert decision["symbols"]["revise"] == [{"slug": "disc-dexpi-nd0136", "reasons": ["primary sha256 changed"]}]
        assert len(decision["symbols"]["unchanged"]) == 3
        with Session(env["engine"]) as session, session.begin():
            report = ingest.apply(session, plan=plan, package_data=package, actor_id=actor_id, occurred_at=NOW + timedelta(hours=1))
        assert [(r["slug"], r["revision"]) for r in report["symbols_revised"]] == [("disc-dexpi-nd0136", "r2")]
        assert report["symbols_created"] == []
        with Session(env["engine"]) as session:
            revisions = session.query(SymbolRevision).join(GovernedSymbol, GovernedSymbol.id == SymbolRevision.symbol_id).filter(GovernedSymbol.slug == "disc-dexpi-nd0136").order_by(SymbolRevision.revision_label).all()
            assert [(r.revision_label, r.lifecycle_state) for r in revisions] == [("r1", "published"), ("r2", "approved")]
            first, second = (r.payload_json["assets"][0] for r in revisions)
            assert first["sha256"] != second["sha256"] and first["object_key"] != second["object_key"]
        # The same package again: nothing further to do.
        with Session(env["engine"]) as session, session.begin():
            again = ingest.apply(session, plan=plan, package_data=package, actor_id=actor_id, occurred_at=NOW + timedelta(hours=2))
        assert again["symbols_revised"] == [] and again["symbols_unchanged"] == 4
        env["state"]["v2"] = (directory, package, plan)

    def test_publishing_the_new_revision_retires_the_old_page(self, env, reader, actor_id):
        _directory, package, plan = env["state"]["v2"]
        with Session(env["engine"]) as session:
            ingest.upload_assets(session, plan, package, storage_env_file=None, dry_run=False, uploader=env["bucket"].put)
            session.rollback()
        with Session(env["engine"]) as session, session.begin():
            ingest.evaluate_gate(session, plan=plan, occurred_at=NOW + timedelta(hours=3), actor_id=actor_id)
            report = ingest.publish(session, plan=plan, actor_id=actor_id, occurred_at=NOW + timedelta(hours=3),
                                    storage_env_file=None, dry_run=False, fetcher=env["bucket"].get)
        assert [(p["slug"], p["revision"]) for p in report["published"]] == [("disc-dexpi-nd0136", "r2")]
        with Session(env["engine"]) as session:
            pages = session.query(PublishedPage).filter(PublishedPage.page_code.like("%nd0136%")).order_by(PublishedPage.page_code).all()
            assert [(p.publication_state, p.retired_at is not None) for p in pages] == [("retired", True), ("active", False)]
            entries = session.query(PackEntry).join(PublishedPage, PublishedPage.id == PackEntry.published_page_id).filter(PublishedPage.page_code.like("%nd0136%")).all()
            assert sorted(e.publication_state for e in entries) == ["active", "retired"]
            states = [
                r.lifecycle_state
                for r in session.query(SymbolRevision).join(GovernedSymbol, GovernedSymbol.id == SymbolRevision.symbol_id).filter(GovernedSymbol.slug == "disc-dexpi-nd0136").order_by(SymbolRevision.revision_label)
            ]
            assert states == ["deprecated", "published"]
        client, _ = reader
        body = client.get(f"{V1}/published/symbols/search", params={"pack": "DISC DEXPI symbol library (DISC Profile 0.6.3)", "pageSize": 50}).json()
        assert body["total"] == 4  # one live row per symbol, not five
        pump = next(i for i in body["items"] if i["slug"] == "disc-dexpi-nd0136")
        assert pump["revision"] == "r2"
        download = client.post(f"{V1}/catalog/symbols/download", json={"symbolIds": [pump["catalogSymbolId"]], "format": "SVG"})
        assert download.content == package["svg"]["ND0136.svg"]
