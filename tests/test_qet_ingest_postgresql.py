"""The QElectroTech elements import against a real PostgreSQL, end to end.

PostgreSQL-only for the reasons `test_commons_ingest_postgresql` gives. The
package is the small synthetic one in `qet_fixtures`. The tests in a class run
in order and share the database, because the import is the thing under test.
"""
from __future__ import annotations

import json
import sys
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

from qet_fixtures import ELEMENTS, build_package, write_package  # noqa: E402
from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402
from test_semantic_review_routes_postgresql import _user  # noqa: E402

from symgov_backend import qet_ingest as ingest  # noqa: E402
from symgov_backend.models import (  # noqa: E402
    AssetTransformation,
    GovernedSymbol,
    RightsRecord,
    SourcePackageEntry,
    SymbolRevision,
    SymbolStandardLink,
)
from symgov_backend.published_symbol_details import load_published_symbol_details  # noqa: E402
from symgov_backend.rights_provenance import transition_rights_record  # noqa: E402
from symgov_backend.semantic_concepts import create_semantic_concept, transition_semantic_concept_revision  # noqa: E402
from symgov_backend.services import qet_ingestion as plan_module  # noqa: E402

psycopg = pytest.importorskip("psycopg")

NOW = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
SLUGS = [f"qet-{item['qet_uuid']}" for item in ELEMENTS]


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
    with _database("symgov-qet") as (engine, url, _raw_url):
        _alembic(url, "upgrade", "head")
        yield engine, url


@pytest.fixture(scope="module")
def env(database, tmp_path_factory):
    engine, url = database
    mp = pytest.MonkeyPatch()
    mp.setenv("SYMGOV_DATABASE_URL", url)
    directory = write_package(build_package(), tmp_path_factory.mktemp("qet-package"))
    bucket = FakeBucket()
    mp.setattr("symgov_backend.qet_ingest.download_object_bytes", bucket.get)
    mp.setattr("symgov_backend.qet_ingest.upload_object_bytes", bucket.put)
    yield {"engine": engine, "directory": directory, "bucket": bucket, "state": {}}
    mp.undo()


@pytest.fixture(scope="module")
def actor_id(env):
    Session_ = sessionmaker(bind=env["engine"], autoflush=False, expire_on_commit=False)
    return _user(Session_, email="qet-importer@example.test", roles=())


@pytest.fixture(scope="module")
def existing(env, actor_id):
    """A concept already named TerminalStrip, which the import must reuse."""
    with Session(env["engine"]) as session, session.begin():
        concept, revision = create_semantic_concept(
            session, concept_kind="physical_equipment", preferred_name="TerminalStrip",
            definition="The existing terminal strip.", created_by_user_id=actor_id, created_at=NOW,
        )
        for target_state in ("review", "approved", "published"):
            transition_semantic_concept_revision(session, revision.id, target_state=target_state, actor_id=actor_id, occurred_at=NOW)
        return {"terminal_strip": concept.concept_code}


def _count(engine, sql, **params):
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def _counts(engine):
    return {
        "symbols": _count(engine, "SELECT count(*) FROM governed_symbols WHERE slug LIKE 'qet-%'"),
        "revisions": _count(engine, "SELECT count(*) FROM symbol_revisions"),
        "concepts": _count(engine, "SELECT count(*) FROM semantic_concepts"),
        "packages": _count(engine, "SELECT count(*) FROM source_packages"),
        "rights": _count(engine, "SELECT count(*) FROM rights_records"),
        "standards": _count(engine, "SELECT count(*) FROM standards"),
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
    def test_a_dry_run_reports_and_writes_nothing(self, env, existing, capsys, tmp_path):
        before = _counts(env["engine"])
        output = tmp_path / "dry.json"
        code, _out, err = _run(["apply", "--dry-run", "--package", str(env["directory"]), "--output", str(output)], capsys)
        assert code == 0, err
        report = json.loads(output.read_text())
        assert report["blocked"] is False and report["symbols"]["would_create"] == 4
        assert report["concepts"]["reuse_existing"] == 1 and report["concepts"]["would_create"] == 2
        assert report["concepts"]["reused"][0]["concept_code"] == existing["terminal_strip"]
        assert report["standards"] == [
            {"standard_code": "EN 60617", "exists": False, "versions": {"edition not stated": "would_register"}}
        ]
        assert _counts(env["engine"]) == before

    def test_apply_records_the_package(self, env, actor_id, capsys, tmp_path):
        before = _counts(env["engine"])
        output = tmp_path / "apply.json"
        code, _out, err = _run(["apply", "--package", str(env["directory"]), "--actor-id", str(actor_id), "--output", str(output)], capsys)
        assert code == 0, err
        report = json.loads(output.read_text())
        assert len(report["symbols_created"]) == 4 and report["rights_decision_status"] == "proposed"
        assert sorted(c["concept_key"] for c in report["concepts_created"]) == ["NEW:FuseGeneralSymbol", "NEW:Lamp"]
        after = _counts(env["engine"])
        assert after["standards"] == before["standards"] + 1 and after["concepts"] == before["concepts"] + 2
        assert after["transformations"] == before["transformations"] + 4 and after["links"] == before["links"] + 4
        env["state"]["after_apply"] = after

    def test_each_revision_carries_its_provenance_link_and_one_step_chain(self, env):
        with Session(env["engine"]) as session:
            for planned in _plan(env)["symbols"]:
                symbol = session.query(GovernedSymbol).filter_by(slug=planned["slug"]).one()
                assert (symbol.discipline, symbol.category, symbol.canonical_name) == (
                    "Electrical", planned["category"], planned["canonical_name"])
                revision = session.get(SymbolRevision, symbol.current_revision_id)
                assert revision.payload_json["attribution"]["creator"] == planned["qet"]["author"]
                entry = session.query(SourcePackageEntry).filter_by(symbol_revision_id=revision.id).one()
                assert (entry.provider_entry_identifier, entry.original_asset_sha256) == (planned["qet_uuid"], planned["original_sha256"])
                steps = session.query(AssetTransformation).filter_by(symbol_revision_id=revision.id).all()
                assert [(s.source_asset_sha256, s.derived_asset_sha256) for s in steps] == [
                    (planned["original_sha256"], planned["final_sha256"])
                ]
                assert steps[0].evidence_json["removed_labels"] == planned["conversion"]["removed_labels"]
                link = session.query(SymbolStandardLink).filter_by(symbol_revision_id=revision.id).one()
                assert (link.relationship_type, link.assertion_status, link.clause_reference) == (
                    "vendor_implementation", "verified", planned["qet"]["en60617_ref"])

    def test_the_rights_record_is_proposed_open_with_the_template(self, env):
        with Session(env["engine"]) as session:
            record = session.query(RightsRecord).one()
            assert (record.rights_status, record.disposition, record.decision_status) == ("open", "distribute", "proposed")
            assert record.evidence_json["attribution_template"] == plan_module.ATTRIBUTION_TEMPLATE
            assert record.evidence_json["source_url"] == "https://qelectrotech.org"

    def test_a_second_apply_changes_nothing(self, env, actor_id, capsys, tmp_path):
        output = tmp_path / "again.json"
        code, _out, err = _run(["apply", "--package", str(env["directory"]), "--actor-id", str(actor_id), "--output", str(output)], capsys)
        assert code == 0, err
        report = json.loads(output.read_text())
        assert report["symbols_created"] == [] and report["symbols_revised"] == [] and report["symbols_unchanged"] == 4
        assert report["concepts_created"] == []
        assert _counts(env["engine"]) == env["state"]["after_apply"]


class TestPublication:
    def test_upload_puts_every_converted_svg_in_the_bucket(self, env, capsys):
        code, _out, err = _run(["upload", "--package", str(env["directory"]), "--storage-env-file", "unused"], capsys)
        assert code == 0, err
        stored = {key.rsplit("primary-", 1)[1][:-4] for key in env["bucket"].objects}
        assert stored == {symbol["final_sha256"] for symbol in _plan(env)["symbols"]}

    def test_the_gate_refuses_until_a_named_person_approves_the_rights(self, env, actor_id, capsys):
        with Session(env["engine"]) as session, session.begin():
            report = ingest.evaluate_gate(session, plan=_plan(env), occurred_at=NOW, actor_id=actor_id)
        assert report["outcomes"] == {"refused": 4} and report["refusal_reasons"] == {"rights_undecided": 4}
        base = ["publish", "--package", str(env["directory"]), "--actor-id", str(actor_id), "--storage-env-file", "unused"]
        code, _out, err = _run(base, capsys)
        assert code == 1 and "rights_undecided" in err
        assert _count(env["engine"], "SELECT count(*) FROM publication_packs WHERE pack_code = 'qelectrotech-elements'") == 0

    def test_once_approved_every_symbol_publishes(self, env, actor_id, capsys, tmp_path):
        with Session(env["engine"]) as session, session.begin():
            record = session.query(RightsRecord).filter_by(decision_status="proposed").one()
            transition_rights_record(session, record.id, target_status="approved", occurred_at=NOW,
                                     decided_by_user_id=actor_id, decision_reason="Approved for the test.")
        output = tmp_path / "publish.json"
        code, _out, err = _run(["publish", "--package", str(env["directory"]), "--actor-id", str(actor_id),
                                "--storage-env-file", "unused", "--output", str(output)], capsys)
        assert code == 0, err
        report = json.loads(output.read_text())
        assert report["published_count"] == 4 and all(item["catalog_symbol_id"] for item in report["published"])

    def test_details_credit_each_author_and_link_qelectrotech(self, env):
        from symgov_backend.commons_verify import _published_rows

        with Session(env["engine"]) as session:
            rows = {row.slug: row for row in _published_rows(session) if row.slug in SLUGS}
            credits = {}
            for slug, row in rows.items():
                rights = load_published_symbol_details(session, row)["rights"]
                assert rights["sourceUrl"] == "https://qelectrotech.org"
                assert rights["attributionText"] == plan_module.attribution_text(rights["creator"])
                credits[slug] = rights["creator"]
        assert credits == {SLUGS[0]: "The QElectroTech team", SLUGS[1]: "The QElectroTech team",
                           SLUGS[2]: "QElectroTech contributors", SLUGS[3]: "Ada Element"}

    def test_verify_passes(self, env, capsys, tmp_path):
        output = tmp_path / "verify.json"
        code, _out, err = _run(["verify", "--package", str(env["directory"]), "--storage-env-file", "unused", "--output", str(output)], capsys)
        report = json.loads(output.read_text())
        assert code == 0 and report["failed"] == [], report
        checks = {check["id"]: check for check in report["checks"]}
        assert checks["stored-objects"]["status"] == "pass"
        assert checks["details-attribution"]["detail"]["checked"] == 4
        assert checks["electrical-facet"]["detail"]["electrical_facet"] == 4

    def test_a_new_author_becomes_a_new_revision_that_republishes(self, env, actor_id, capsys):
        package = ingest.load_package(env["directory"])
        package["manifest"]["elements"][3]["credit"] = "Ada Element and Bo Element"
        plan = plan_module.plan_import(package)
        with Session(env["engine"]) as session, session.begin():
            report = ingest.apply(session, plan=plan, actor_id=actor_id, occurred_at=NOW)
        assert [(item["slug"], item["revision"], item["reasons"]) for item in report["symbols_revised"]] == [
            (SLUGS[3], "r2", ["author changed"])
        ]
