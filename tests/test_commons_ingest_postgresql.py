"""The Wikimedia Commons import against a real PostgreSQL, end to end.

PostgreSQL-only for the reasons `test_disc_dexpi_ingest_postgresql` gives:
every governed row lands in a table whose constraints SQLite does not carry.
The package is the small synthetic one in `commons_fixtures`. The tests in a
class run in order and share the database, because the import is the thing
under test.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(Path(__file__).resolve().parent))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from commons_fixtures import build_package, write_package  # noqa: E402
from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402
from test_semantic_review_routes_postgresql import _user  # noqa: E402

from symgov_backend import commons_ingest as ingest  # noqa: E402
from symgov_backend import commons_verify  # noqa: E402
from symgov_backend.models import (  # noqa: E402
    AssetTransformation,
    GovernedSymbol,
    RightsRecord,
    SourcePackageEntry,
    SymbolRevision,
    SymbolStandardLink,
)
from symgov_backend.rights_provenance import transition_rights_record  # noqa: E402
from symgov_backend.semantic_concepts import create_semantic_concept, transition_semantic_concept_revision  # noqa: E402
from symgov_backend.services import commons_ingestion as plan_module  # noqa: E402
from symgov_backend.standard_sources import register_standard, register_standard_version  # noqa: E402
from symgov_backend.symbol_semantic_assignments import (  # noqa: E402
    propose_symbol_semantic_assignment,
    transition_symbol_semantic_assignment,
)

psycopg = pytest.importorskip("psycopg")

NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
PILOT_SLUG = "dexpi-ttc-" + "cd" * 16


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
    with _database("symgov-commons") as (engine, url, _raw_url):
        _alembic(url, "upgrade", "head")
        yield engine, url


@pytest.fixture(scope="module")
def env(database, tmp_path_factory):
    engine, url = database
    mp = pytest.MonkeyPatch()
    mp.setenv("SYMGOV_DATABASE_URL", url)
    directory = write_package(build_package(), tmp_path_factory.mktemp("commons-package"))
    bucket = FakeBucket()
    mp.setattr("symgov_backend.commons_ingest.download_object_bytes", bucket.get)
    mp.setattr("symgov_backend.commons_ingest.upload_object_bytes", bucket.put)
    yield {"engine": engine, "directory": directory, "bucket": bucket, "state": {}}
    mp.undo()


@pytest.fixture(scope="module")
def actor_id(env):
    Session_ = sessionmaker(bind=env["engine"], autoflush=False, expire_on_commit=False)
    return _user(Session_, email="commons-importer@example.test", roles=())


@pytest.fixture(scope="module")
def existing(env, actor_id):
    """What production already holds: the ISO 10628 record, a published `Pump` drawing
    in another pack, and a concept one letter-run away from a NEW: key."""
    engine = env["engine"]
    with Session(engine) as session, session.begin():
        standard = register_standard(session, standard_code="ISO 10628", title="ISO 10628", issuing_body="ISO", registered_at=NOW)
        session.flush()
        register_standard_version(session, standard_id=standard.id, version_label="2012", registered_at=NOW)
        concepts = {}
        for name in ("Pump", "Fire Damper"):
            concept, revision = create_semantic_concept(
                session, concept_kind="physical_equipment", preferred_name=name, definition=f"The existing {name}.",
                created_by_user_id=actor_id, created_at=NOW,
            )
            for target_state in ("review", "approved", "published"):
                transition_semantic_concept_revision(session, revision.id, target_state=target_state, actor_id=actor_id, occurred_at=NOW)
            concepts[name] = concept.id
    symbol_id, revision_id, page_id, entry_id, pack_id = (uuid.uuid4() for _ in range(5))
    payload = {"name": "Pump (HEX 1)", "summary": "A pilot pump.", "keywords": ["DEXPI"]}
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO publication_packs (id,pack_code,title,audience,effective_date,status,created_at,updated_at) "
            "VALUES (:id,'dexpi-ttc-1-2-1-3','DEXPI pilot','public',DATE '2026-09-24','published',:now,:now)"
        ), {"id": pack_id, "now": NOW})
        connection.execute(text(
            "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,visibility,created_at,updated_at) "
            "VALUES (:id,:slug,'Pump (HEX 1)','Pumps','Piping / P&ID',:owner,'public',:now,:now)"
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
            "VALUES (:id,'PAGE-TST-1','Pump',:pack,:r,DATE '2026-09-24',:now,:now)"
        ), {"id": page_id, "pack": pack_id, "r": revision_id, "now": NOW})
        connection.execute(text(
            "INSERT INTO pack_entries (id,pack_id,symbol_revision_id,published_page_id,sort_order,created_at) "
            "VALUES (:id,:pack,:r,:page,1,:now)"
        ), {"id": entry_id, "pack": pack_id, "r": revision_id, "page": page_id, "now": NOW})
    with Session(engine) as session, session.begin():
        assignment = propose_symbol_semantic_assignment(
            session, symbol_revision_id=revision_id, semantic_concept_id=concepts["Pump"], assignment_role="primary",
            method="source_mapping", proposed_at=NOW, proposed_by_user_id=actor_id,
        )
        session.flush()
        transition_symbol_semantic_assignment(session, assignment.id, target_status="verified", occurred_at=NOW)
    return {"pump_concept_id": concepts["Pump"]}


def _count(engine, sql, **params):
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def _counts(engine):
    return {
        "symbols": _count(engine, "SELECT count(*) FROM governed_symbols WHERE slug LIKE 'commons-%'"),
        "revisions": _count(engine, "SELECT count(*) FROM symbol_revisions sr JOIN governed_symbols gs ON gs.id=sr.symbol_id WHERE gs.slug LIKE 'commons-%'"),
        "concepts": _count(engine, "SELECT count(*) FROM semantic_concepts"),
        "packages": _count(engine, "SELECT count(*) FROM source_packages WHERE package_code = :c", c=plan_module.PACKAGE_CODE),
        "rights": _count(engine, "SELECT count(*) FROM rights_records"),
        "standards": _count(engine, "SELECT count(*) FROM standards"),
        "editions": _count(engine, "SELECT count(*) FROM standard_versions"),
        "links": _count(engine, "SELECT count(*) FROM symbol_standard_links"),
        "classifications": _count(engine, "SELECT count(*) FROM symbol_revision_classifications"),
        "transformations": _count(engine, "SELECT count(*) FROM asset_transformations"),
    }


def _run(argv, capsys):
    code = ingest.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def _plan(env):
    return plan_module.plan_import(ingest.load_package(env["directory"]))


class TestImport:
    def test_fix_svgs_then_a_dry_run_writes_nothing(self, env, existing, capsys, tmp_path):
        code, _out, err = _run(["fix-svgs", "--package", str(env["directory"])], capsys)
        assert code == 0, err
        before = _counts(env["engine"])
        output = tmp_path / "dry.json"
        code, _out, err = _run(["apply", "--dry-run", "--package", str(env["directory"]), "--output", str(output)], capsys)
        assert code == 0, err
        report = json.loads(output.read_text())
        assert report["blocked"] is False and report["symbols"]["would_create"] == 5
        assert report["concepts"]["reuse_existing"] == 1 and report["concepts"]["would_create"] == 3
        assert {"concept_key": "NEW:FireDamper", "existing_names": ["fire damper"]} in report["concepts"]["near_misses"]
        iso = next(s for s in report["standards"] if s["standard_code"] == "ISO 10628")
        assert iso == {"standard_code": "ISO 10628", "exists": True, "versions": {"2012": "existing"}}
        assert [item["pageid"] for item in report["excluded"]] == [1006]
        assert _counts(env["engine"]) == before

    def test_apply_records_the_package(self, env, existing, actor_id, capsys, tmp_path):
        before = _counts(env["engine"])
        output = tmp_path / "apply.json"
        code, _out, err = _run(["apply", "--package", str(env["directory"]), "--actor-id", str(actor_id), "--output", str(output)], capsys)
        assert code == 0, err
        report = json.loads(output.read_text())
        assert len(report["symbols_created"]) == 5 and report["rights_decision_status"] == "proposed"
        assert [c["concept_key"] for c in report["concepts_reused"]] == ["Pump"]
        assert sorted(c["concept_key"] for c in report["concepts_created"]) == [
            "NEW:AnchorAgitator", "NEW:FireDamper", "NEW:PlainValve",
        ]
        after = _counts(env["engine"])
        # The ISO record and its 2012 edition are reused; DIN EN 12792 is registered once.
        assert after["standards"] == before["standards"] + 1 and after["editions"] == before["editions"] + 1
        env["state"]["after_apply"] = after

    def test_each_revision_carries_its_provenance_and_lineage(self, env):
        plan = _plan(env)
        with Session(env["engine"]) as session:
            for planned in plan["symbols"]:
                symbol = session.query(GovernedSymbol).filter_by(slug=planned["slug"]).one()
                assert (symbol.discipline, symbol.category, symbol.canonical_name) == (
                    planned["discipline"], planned["category"], planned["canonical_name"])
                revision = session.get(SymbolRevision, symbol.current_revision_id)
                assert revision.lifecycle_state == "approved" and revision.payload_json["commons"]["pageid"] == planned["pageid"]
                entry = session.query(SourcePackageEntry).filter_by(symbol_revision_id=revision.id).one()
                assert entry.original_asset_sha256 == planned["original_sha256"]
                steps = session.query(AssetTransformation).filter_by(symbol_revision_id=revision.id).order_by(AssetTransformation.step_index).all()
                assert [s.evidence_json["fix"] for s in steps] == [s["fix"] for s in planned["fix_steps"]]
                if steps:
                    assert steps[0].source_asset_sha256 == planned["original_sha256"]
                    assert steps[-1].derived_asset_sha256 == planned["final_sha256"]
                    assert steps[-1].evidence_json["original_sha256"] == planned["original_sha256"]
                links = session.query(SymbolStandardLink).filter_by(symbol_revision_id=revision.id).all()
                assert [link.relationship_type for link in links] == ["vendor_implementation"] * len(planned["standards"])
                assert all(link.assertion_status == "verified" for link in links)

    def test_the_rights_record_is_proposed_open_with_per_file_evidence(self, env):
        with Session(env["engine"]) as session:
            record = session.query(RightsRecord).filter(RightsRecord.evidence_json["file_count"].astext.is_not(None)).one()
            assert (record.rights_status, record.disposition, record.decision_status) == ("open", "distribute", "proposed")
            assert [item["pageid"] for item in record.evidence_json["files"]] == [1001, 1002, 1003, 1004, 1007]

    def test_the_fire_damper_is_also_fire_and_life_safety(self, env):
        rows = _count(
            env["engine"],
            "SELECT string_agg(src.assignment_role || ':' || cn.preferred_label, ',' ORDER BY src.assignment_role, cn.preferred_label) "
            "FROM symbol_revision_classifications src "
            "JOIN classification_nodes cn ON cn.id = src.classification_node_id "
            "JOIN classification_schemes cs ON cs.id = src.classification_scheme_id "
            "JOIN governed_symbols gs ON gs.current_revision_id = src.symbol_revision_id "
            "WHERE gs.slug = 'commons-1002' AND cs.scheme_code = 'ENGINEERING-DISCIPLINE' AND src.status = 'verified' AND src.method = 'rule'",
        )
        assert rows == "primary:HVAC,secondary:Fire & Life Safety"
        others = _count(
            env["engine"],
            "SELECT count(*) FROM symbol_revision_classifications src JOIN classification_nodes cn ON cn.id = src.classification_node_id "
            "JOIN governed_symbols gs ON gs.current_revision_id = src.symbol_revision_id "
            "WHERE gs.slug <> 'commons-1002' AND gs.slug LIKE 'commons-%' AND cn.preferred_label = 'Fire & Life Safety'",
        )
        assert others == 0

    def test_a_second_apply_changes_nothing(self, env, actor_id, capsys, tmp_path):
        output = tmp_path / "again.json"
        code, _out, err = _run(["apply", "--package", str(env["directory"]), "--actor-id", str(actor_id), "--output", str(output)], capsys)
        assert code == 0, err
        report = json.loads(output.read_text())
        assert report["symbols_created"] == [] and report["symbols_revised"] == [] and report["symbols_unchanged"] == 5
        assert report["concepts_created"] == []
        assert _counts(env["engine"]) == env["state"]["after_apply"]


class TestPublication:
    def test_upload_puts_every_svg_in_the_bucket(self, env, capsys):
        code, _out, err = _run(["upload", "--package", str(env["directory"]), "--storage-env-file", "unused"], capsys)
        assert code == 0, err
        assert len(env["bucket"].objects) == 5
        plan = _plan(env)
        stored = {key.rsplit("primary-", 1)[1][:-4] for key in env["bucket"].objects}
        assert stored == {symbol["final_sha256"] for symbol in plan["symbols"]}

    def test_the_gate_refuses_until_a_named_person_approves_the_rights(self, env, actor_id):
        plan = _plan(env)
        with Session(env["engine"]) as session, session.begin():
            report = ingest.evaluate_gate(session, plan=plan, occurred_at=NOW, actor_id=actor_id)
        assert report["outcomes"] == {"refused": 5}
        assert report["refusal_reasons"] == {"rights_undecided": 5, "graphical_authority_unasserted": 1}
        with Session(env["engine"]) as session, session.begin():
            with pytest.raises(ingest.PublicationRefused):
                ingest.publish(session, plan=plan, actor_id=actor_id, occurred_at=NOW, storage_env_file=None,
                               dry_run=False, permitted_only=True, fetcher=env["bucket"].get)
        assert _count(env["engine"], "SELECT count(*) FROM publication_packs WHERE pack_code = 'commons-pid'") == 0

    def test_once_approved_only_the_symbol_without_a_standard_is_refused(self, env, actor_id):
        with Session(env["engine"]) as session, session.begin():
            record = session.query(RightsRecord).filter_by(decision_status="proposed").one()
            transition_rights_record(session, record.id, target_status="approved", occurred_at=NOW,
                                     decided_by_user_id=actor_id, decision_reason="Approved for the test.")
        with Session(env["engine"]) as session, session.begin():
            report = ingest.evaluate_gate(session, plan=_plan(env), occurred_at=NOW, actor_id=actor_id)
        assert report["outcomes"] == {"permitted": 4, "refused": 1}
        assert report["refused_slugs_by_reason"] == {"graphical_authority_unasserted": ["commons-1007"]}

    def test_publish_is_all_or_nothing_unless_told_to_leave_the_unlinked_behind(self, env, actor_id, capsys, tmp_path):
        base = ["publish", "--package", str(env["directory"]), "--actor-id", str(actor_id), "--storage-env-file", "unused"]
        code, _out, err = _run(base, capsys)
        assert code == 1 and "graphical_authority_unasserted" in err
        assert _count(env["engine"], "SELECT count(*) FROM publication_packs WHERE pack_code = 'commons-pid'") == 0
        output = tmp_path / "publish.json"
        code, _out, err = _run(base + ["--permitted-only", "--output", str(output)], capsys)
        assert code == 0, err
        report = json.loads(output.read_text())
        assert report["published_count"] == 4 and report["held_at_gate"] == [
            {"slug": "commons-1007", "reasons": ["graphical_authority_unasserted"]}
        ]
        assert all(item["catalog_symbol_id"] for item in report["published"])
        assert report["facet_rows_computed"] == 4

    def test_verify_passes_every_check_but_the_held_symbol(self, env, capsys, tmp_path):
        output = tmp_path / "verify.json"
        code, _out, err = _run(["verify", "--package", str(env["directory"]), "--storage-env-file", "unused", "--output", str(output)], capsys)
        report = json.loads(output.read_text())
        assert code == 1 and report["failed"] == ["all-published"], report
        checks = {check["id"]: check for check in report["checks"]}
        assert checks["all-published"]["detail"]["unpublished_slugs"] == ["commons-1007"]
        assert checks["hvac-facet"]["detail"] == {"hvac_facet": 1, "expected": 1, "stored_facet_rows_with_hvac": 1}
        assert checks["fire-classification"]["detail"]["fire_dampers"] == ["commons-1002"]
        assert checks["concept-alternatives"]["detail"]["reused_and_resolved"] == 1
        assert checks["near-duplicates"]["status"] == "skipped"
        env["state"]["verify"] = output

    def test_the_report_renders(self, env, capsys, tmp_path):
        out = tmp_path / "REPORT.md"
        code, _o, err = _run(["report", "--package", str(env["directory"]), "--out", str(out),
                              "--verify-report", str(env["state"]["verify"]), "--banner", "REHEARSAL"], capsys)
        assert code == 0, err
        text_ = out.read_text()
        assert text_.startswith("> **REHEARSAL**") and "Acceptance checks" in text_

    def test_a_renamed_row_becomes_a_new_revision(self, env, actor_id):
        package = ingest.load_package(env["directory"])
        next(row for row in package["worksheet"] if row["pageid"] == "1001")["proposed_name"] = "Pump, centrifugal (renamed)"
        plan = plan_module.plan_import(package)
        with Session(env["engine"]) as session, session.begin():
            report = ingest.apply(session, plan=plan, actor_id=actor_id, occurred_at=NOW)
        assert [(item["slug"], item["revision"], item["reasons"]) for item in report["symbols_revised"]] == [
            ("commons-1001", "r2", ["name changed"])
        ]


def test_the_near_duplicate_maths_finds_a_copy_and_not_a_different_drawing():
    from PIL import Image, ImageDraw

    def drawing(shape):
        image = Image.new("L", (128, 128), 255)
        draw = ImageDraw.Draw(image)
        if shape == "circle":
            draw.ellipse((20, 20, 108, 108), outline=0, width=3)
        else:
            draw.line((10, 10, 118, 118), fill=0, width=3)
            draw.line((10, 118, 118, 10), fill=0, width=3)
        return commons_verify._normalised(image)

    circle, cross = drawing("circle"), drawing("cross")
    assert (commons_verify._dhash(circle) ^ commons_verify._dhash(drawing("circle"))).bit_count() == 0
    assert commons_verify._ink_overlap(circle, drawing("circle")) == 1.0
    assert commons_verify._ink_overlap(circle, cross) < commons_verify.INK_OVERLAP_THRESHOLD
