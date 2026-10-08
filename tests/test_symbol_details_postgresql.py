"""The Catalog's Details view data, against a real PostgreSQL.

Covers `GET /published/symbols/{ref}/details`, the `dexpiClass` search filter
and the comments submitter label:

* a DISC symbol (the stand-in ND0004 of `disc_dexpi_fixtures`, imported and
  published exactly as `test_disc_dexpi_ingest_postgresql` does) with its
  classifications, external mapping, rights, provenance and history;
* the pilot symbol, which has no geometry, no source package and no rights
  record, and a legacy intake-style symbol, which has a review decision;
* rejected and retired classifications left out;
* an organization-private symbol answering 404 to its owner and to outsiders;
* nothing in any response that names a person or a storage key;
* the class filter composing with every other filter and agreeing with the
  Details view's own count.

The database, the pilot and the readers come from the DISC import test; this
module runs its own import in `published`.
"""
from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(Path(__file__).resolve().parent))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from disc_dexpi_fixtures import ATTRIBUTION, COMMIT, SOURCE_URL  # noqa: E402
from test_disc_dexpi_ingest_postgresql import (  # noqa: E402,F401  (fixtures)
    NOW,
    PILOT_SLUG,
    V1,
    actor_id,
    database,
    env,
    pilot,
    reader,
)
from test_semantic_review_routes_postgresql import _client, _login, _user  # noqa: E402
from test_wp74_symbol_demotion_postgresql import _add_membership, _create_user_with_global_roles  # noqa: E402
from test_wp81_catalog_organization_context_postgresql import _make_organization_wide_symbol  # noqa: E402

from symgov_backend import disc_dexpi_ingest as ingest  # noqa: E402
from symgov_backend.models import (  # noqa: E402
    ClarificationRecord,
    HumanReviewDecision,
    ReviewCase,
    RightsRecord,
    SemanticConcept,
)
from symgov_backend.rights_provenance import transition_rights_record  # noqa: E402
from symgov_backend.services import disc_dexpi_ingestion as plan_module  # noqa: E402
from symgov_backend.symbol_semantic_assignments import (  # noqa: E402
    propose_symbol_semantic_assignment,
    transition_symbol_semantic_assignment,
)

psycopg = pytest.importorskip("psycopg")

DETAIL_KEYS = {
    "catalogSymbolId", "classifications", "externalMappings", "sameConcept",
    "sameClass", "rights", "provenance", "history",
}
LEGACY_SLUG = "legacy-intake-hydrant"
LEGACY_REASON = "An internal demotion reason that no reader may see."
T0 = datetime(2026, 9, 1, 9, 0, 0, tzinfo=timezone.utc)


def _run_scalar(engine, sql, **params):
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def _insert_public_symbol(engine, owner_id, *, slug, catalog_id, payload, pack_code, revision_at, page_at):
    symbol_id, revision_id, page_id, entry_id, pack_id = (uuid.uuid4() for _ in range(5))
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO publication_packs (id,pack_code,title,audience,effective_date,status,created_at,updated_at) "
            "VALUES (:id,:code,:title,'public',DATE '2026-09-02','published',:now,:now)"
        ), {"id": pack_id, "code": pack_code, "title": f"{pack_code} pack", "now": page_at})
        connection.execute(text(
            "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,visibility,created_at,updated_at) "
            "VALUES (:id,:slug,:name,'Equipment','Fire & Life Safety',:owner,'public',:now,:now)"
        ), {"id": symbol_id, "slug": slug, "name": payload["name"], "owner": owner_id, "now": revision_at})
        connection.execute(text(
            "INSERT INTO symbol_revisions (id,symbol_id,revision_label,lifecycle_state,payload_json,author_id,created_at) "
            "VALUES (:id,:symbol,'r1','published',CAST(:payload AS jsonb),:owner,:now)"
        ), {"id": revision_id, "symbol": symbol_id, "owner": owner_id, "now": revision_at, "payload": json.dumps(payload)})
        connection.execute(text("UPDATE governed_symbols SET current_revision_id=:r WHERE id=:s"), {"r": revision_id, "s": symbol_id})
        connection.execute(text(
            "INSERT INTO catalog_symbol_identifiers (identifier,role,governed_symbol_id,allocation_source,allocated_at) "
            "VALUES (:ident,'canonical',:s,'global_sequence',now())"
        ), {"s": symbol_id, "ident": catalog_id})
        connection.execute(text("UPDATE governed_symbols SET catalog_symbol_id=:ident WHERE id=:s"), {"s": symbol_id, "ident": catalog_id})
        connection.execute(text(
            "INSERT INTO published_pages (id,page_code,title,pack_id,current_symbol_revision_id,effective_date,created_at,updated_at) "
            "VALUES (:id,:code,'Page',:pack,:r,DATE '2026-09-02',:now,:now)"
        ), {"id": page_id, "code": f"PAGE-{catalog_id}", "pack": pack_id, "r": revision_id, "now": page_at})
        connection.execute(text(
            "INSERT INTO pack_entries (id,pack_id,symbol_revision_id,published_page_id,sort_order,created_at) "
            "VALUES (:id,:pack,:r,:page,1,:now)"
        ), {"id": entry_id, "pack": pack_id, "r": revision_id, "page": page_id, "now": page_at})
    return {"symbol_id": symbol_id, "revision_id": revision_id, "page_id": page_id}


@pytest.fixture(scope="module")
def published(env, pilot, actor_id):
    """The DISC library imported and published, the pilot given its concept
    assignment, and one legacy intake-style symbol with a review decision."""
    engine = env["engine"]
    package = ingest.load_package(env["directory"])
    plan = plan_module.plan_import(package)
    with Session(engine) as session, session.begin():
        ingest.apply(session, plan=plan, package_data=package, actor_id=actor_id, occurred_at=NOW)
    with Session(engine) as session:
        ingest.upload_assets(session, plan, package, storage_env_file=None, dry_run=False, uploader=env["bucket"].put)
        session.rollback()
    with Session(engine) as session, session.begin():
        record = session.query(RightsRecord).filter_by(decision_status="proposed").one()
        transition_rights_record(
            session, record.id, target_status="approved", occurred_at=NOW,
            decided_by_user_id=actor_id, decision_reason="Permission granted; approved for the test.",
        )
    with Session(engine) as session, session.begin():
        report = ingest.publish(session, plan=plan, actor_id=actor_id, occurred_at=NOW,
                                storage_env_file=None, dry_run=False, fetcher=env["bucket"].get)
    refs = {item["slug"]: item["catalog_symbol_id"] for item in report["published"]}

    # The pilot, like production's, holds a verified semantic assignment to the
    # concept the DISC import reuses, which is what links the two libraries.
    with Session(engine) as session, session.begin():
        concept = session.query(SemanticConcept).filter_by(concept_code=pilot["concept_code"]).one()
        assignment = propose_symbol_semantic_assignment(
            session, symbol_revision_id=pilot["revision_id"], semantic_concept_id=concept.id,
            assignment_role="primary", method=ingest.SEMANTIC_METHOD, proposed_at=NOW,
            proposed_by_user_id=actor_id, evidence={"concept_key": "CentrifugalPump"},
        )
        session.flush()
        transition_symbol_semantic_assignment(session, assignment.id, target_status="verified", occurred_at=NOW)

    # A legacy intake-style symbol: internal IDs in its payload, a recorded
    # human approval, no source package, an internal demotion reason on file.
    with Session(engine) as session, session.begin():
        case = ReviewCase(
            source_entity_type="intake_record", source_entity_id=uuid.uuid4(), current_stage="done",
            escalation_level="none", opened_at=T0,
        )
        session.add(case)
        session.flush()
        case_id = case.id
        decision = HumanReviewDecision(
            review_case_id=case.id, decision_code="approve", decider_name="Reviewer Person",
            decider_role="reviewer", from_stage="symbol_review", decided_by=actor_id,
            created_at=T0 + timedelta(hours=1),
        )
        session.add(decision)
        session.flush()
        decision_id = decision.id
    legacy = _insert_public_symbol(
        engine, actor_id, slug=LEGACY_SLUG, catalog_id="LEG-1",
        payload={
            "name": "Legacy Hydrant", "summary": "A hydrant from the intake pipeline.",
            "review_case_id": str(case_id), "review_decision_id": str(decision_id),
            "classification": {"discipline": "Fire & Life Safety", "category": "Equipment"},
            "lineage": {"intake_record_id": str(uuid.uuid4())},
        },
        pack_code="legacy-intake", revision_at=T0, page_at=T0 + timedelta(hours=2),
    )
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO audit_events (id,entity_type,entity_id,action,actor_id,payload_json,created_at) "
            "VALUES (:id,'governed_symbol',:s,'governed_symbol.demoted',:actor,CAST(:payload AS jsonb),:at)"
        ), {"id": uuid.uuid4(), "s": legacy["symbol_id"], "actor": actor_id,
            "payload": json.dumps({"reason": LEGACY_REASON}), "at": T0 + timedelta(hours=3)})
    return {"refs": refs, "legacy_ref": "LEG-1", "legacy": legacy, "decision_id": decision_id,
            "nd0004": refs["disc-dexpi-nd0004"], "nd0136": refs["disc-dexpi-nd0136"],
            "nd0050": refs["disc-dexpi-nd0050"], "nd0114": refs["disc-dexpi-nd0114"]}


def _details(client, ref, expect=200):
    response = client.get(f"{V1}/published/symbols/{ref}/details")
    assert response.status_code == expect, response.text
    return response.json()


def _search(client, **params):
    response = client.get(f"{V1}/published/symbols/search", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _assert_no_people_or_keys(payload, forbidden):
    body = json.dumps(payload)
    for needle in forbidden:
        assert needle not in body, needle
    for word in ("object_key", "objectKey", "decided_by", "decidedBy", "proposed_by", "reviewed_by", "actor", "evidence"):
        assert word not in body, word
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", body), "an email address"


class TestDiscSymbol:
    def test_the_response_has_exactly_the_documented_shape(self, reader, published):
        client, _ = reader
        details = _details(client, published["nd0004"])
        assert set(details) == DETAIL_KEYS
        assert details["catalogSymbolId"] == published["nd0004"]
        assert set(details["rights"]) == {
            "status", "disposition", "licensor", "creator", "attributionText", "attributionIsPlaceholder", "sourceUrl",
        }
        assert set(details["provenance"]) == {
            "packCode", "pack", "sourceUri", "sourceCommit", "releaseVersion", "sourcePath", "providerEntryIdentifier",
        }
        for key in ("classifications", "externalMappings", "history"):
            assert details[key], key
        assert set(details["classifications"][0]) == {
            "schemeCode", "schemeName", "nodeCode", "nodePath", "nodeLabel", "method", "role", "status",
        }
        assert set(details["externalMappings"][0]) == {"system", "identifier", "label", "relation", "status"}
        assert set(details["history"][0]) == {"kind", "at", "label"}
        print("ND0004 DETAILS", json.dumps(details, indent=1, sort_keys=True))

    def test_classifications_show_the_dexpi_class_path_and_the_governed_taxonomy(self, reader, published):
        client, _ = reader
        rows = _details(client, published["nd0004"])["classifications"]
        dexpi = [row for row in rows if row["schemeCode"] == "DEXPI-CLASS"]
        assert len(dexpi) == 1
        assert dexpi[0]["nodePath"][-2:] == ["Custom Operated Valve", "DoubleBlockAndBleedValve"]
        # The governed label is the source's own name; its spaced form is an alias.
        assert dexpi[0]["nodeLabel"] == "DoubleBlockAndBleedValve"
        assert dexpi[0]["nodePath"][0] == "Piping"
        assert (dexpi[0]["method"], dexpi[0]["status"], dexpi[0]["role"]) == ("source_mapping", "verified", "primary")
        assert {row["schemeCode"] for row in rows} >= {
            "DEXPI-CLASS", "ENGINEERING-DISCIPLINE", "SYMBOL-CATEGORY-FAMILY", "REPRESENTATION-TYPE",
        }
        assert all(row["status"] in {"proposed", "verified"} for row in rows)

    def test_the_external_mapping_is_the_concepts_verified_posc_caesar_reference(self, reader, published):
        client, _ = reader
        mappings = _details(client, published["nd0004"])["externalMappings"]
        exact = [row for row in mappings if row["identifier"] == "http://rdl.example.test/RDS552689"]
        assert len(exact) == 1
        assert exact[0]["relation"] == "exact" and exact[0]["status"] == "verified"
        assert exact[0]["system"] and exact[0]["label"]
        assert all(row["status"] == "verified" for row in mappings)

    def test_rights_come_from_the_approved_record_with_the_placeholder_flag(self, reader, published):
        client, _ = reader
        rights = _details(client, published["nd0004"])["rights"]
        assert rights["status"] == "licensed" and rights["disposition"] == "distribute"
        assert rights["licensor"] == "A. Licensor"
        assert rights["creator"] == "The Example project (example.test)"
        assert rights["attributionText"] == ATTRIBUTION
        assert rights["attributionIsPlaceholder"] is True
        assert rights["sourceUrl"] == SOURCE_URL

    def test_provenance_names_the_package_commit_and_source_file(self, reader, published):
        client, _ = reader
        provenance = _details(client, published["nd0004"])["provenance"]
        assert provenance["packCode"] == "disc-dexpi"
        assert provenance["pack"] == "DISC DEXPI symbol library (DISC Profile 0.6.3)"
        assert provenance["sourceUri"] == SOURCE_URL
        assert provenance["sourceCommit"] == COMMIT
        assert provenance["providerEntryIdentifier"] == "ND0004"
        assert provenance["sourcePath"] and provenance["sourcePath"].endswith("ND0004.svg")
        assert provenance["releaseVersion"]

    def test_history_is_revisions_and_approvals_only_newest_first(self, reader, published):
        client, _ = reader
        history = _details(client, published["nd0004"])["history"]
        assert [event["kind"] for event in history] == ["published", "rights_approved", "revision_imported"]
        assert history[2]["label"] == "Revision r1 imported"
        assert history[1]["label"] == "Rights approved (licensed · distribute)"
        assert [event["at"] for event in history] == sorted((event["at"] for event in history), reverse=True)
        assert all(datetime.fromisoformat(event["at"]).tzinfo for event in history)

    def test_a_class_with_no_other_symbol_lists_none_and_counts_itself(self, reader, published):
        client, _ = reader
        details = _details(client, published["nd0004"])
        assert details["sameClass"] == {"className": "CustomOperatedValve", "total": 1, "items": []}
        assert details["sameConcept"] == []

    def test_no_response_names_a_person_or_a_storage_key(self, reader, published, env, actor_id):
        client, _ = reader
        forbidden = [str(actor_id), "disc-importer@example.test", "disc-reader@example.test"]
        for ref in (published["nd0004"], published["nd0136"], published["nd0114"], "TST-1", published["legacy_ref"]):
            details = _details(client, ref)
            _assert_no_people_or_keys(details, forbidden + [str(published["decision_id"]), LEGACY_REASON])


class TestSharedClassAndConcept:
    def test_the_pilot_and_the_disc_symbol_share_a_class_and_a_concept_across_packs(self, reader, published):
        client, _ = reader
        details = _details(client, published["nd0136"])
        assert details["sameClass"]["className"] == "CentrifugalPump"
        assert details["sameClass"]["total"] == 2
        assert [item["catalogSymbolId"] for item in details["sameClass"]["items"]] == ["TST-1"]
        item = details["sameClass"]["items"][0]
        assert set(item) == {"catalogSymbolId", "name", "slug", "packCode", "pack", "previewUrl"}
        assert item["slug"] == PILOT_SLUG and item["packCode"] == "dexpi-ttc-1-2-1-3"
        assert [row["catalogSymbolId"] for row in details["sameConcept"]] == ["TST-1"]
        assert details["sameConcept"][0]["packCode"] != "disc-dexpi"
        # And the other way round: the pilot sees the DISC drawing.
        pilot_details = _details(client, "TST-1")
        assert [row["catalogSymbolId"] for row in pilot_details["sameConcept"]] == [published["nd0136"]]
        assert pilot_details["sameClass"]["total"] == 2

    def test_same_class_total_is_what_the_catalog_returns_for_that_class(self, reader, published):
        client, _ = reader
        for ref in (published["nd0136"], "TST-1", published["nd0004"]):
            details = _details(client, ref)
            result = _search(client, dexpiClass=details["sameClass"]["className"])
            assert details["sameClass"]["total"] == result["total"], ref


class TestPilotAndLegacy:
    def test_the_pilot_has_nulls_and_empty_lists_but_no_error(self, reader, published):
        client, _ = reader
        details = _details(client, "TST-1")
        assert set(details) == DETAIL_KEYS
        assert details["rights"] is None and details["provenance"] is None
        assert details["externalMappings"] == []
        assert any(row["schemeCode"] == "DEXPI-CLASS" for row in details["classifications"])
        assert [event["kind"] for event in details["history"]] == ["published", "revision_created"]

    def test_a_legacy_symbol_without_a_source_package_or_class(self, reader, published):
        client, _ = reader
        details = _details(client, published["legacy_ref"])
        assert set(details) == DETAIL_KEYS
        assert details["rights"] is None and details["provenance"] is None
        assert details["sameClass"] is None
        assert details["classifications"] == [] and details["externalMappings"] == [] and details["sameConcept"] == []

    def test_a_legacy_symbols_history_has_its_review_approval_and_the_demotion_without_its_reason(self, reader, published):
        client, _ = reader
        history = _details(client, published["legacy_ref"])["history"]
        assert [event["kind"] for event in history] == ["demoted", "published", "review_approved", "revision_created"]
        assert history[0]["label"] == "Withdrawn from the public Catalog"
        assert LEGACY_REASON not in json.dumps(history)
        assert history[3]["at"] == T0.isoformat()
        assert history[2]["at"] == (T0 + timedelta(hours=1)).isoformat()


class TestHiddenAssignments:
    def test_rejected_and_retired_classifications_are_left_out(self, env, reader, published, actor_id):
        client, _ = reader
        before = {row["schemeCode"] for row in _details(client, published["nd0050"])["classifications"]}
        assert {"ENGINEERING-DISCIPLINE", "SYMBOL-CATEGORY-FAMILY", "DEXPI-CLASS"} <= before
        with env["engine"].begin() as connection:
            connection.execute(text(
                "UPDATE symbol_revision_classifications SET status='rejected', reviewed_at=now(), reviewed_by_user_id=:u "
                "WHERE symbol_revision_id=(SELECT current_revision_id FROM governed_symbols WHERE slug='disc-dexpi-nd0050') "
                "AND classification_scheme_id=(SELECT id FROM classification_schemes WHERE scheme_code='ENGINEERING-DISCIPLINE')"
            ), {"u": actor_id})
            connection.execute(text(
                "UPDATE symbol_revision_classifications SET status='retired', reviewed_at=now(), reviewed_by_user_id=:u "
                "WHERE symbol_revision_id=(SELECT current_revision_id FROM governed_symbols WHERE slug='disc-dexpi-nd0050') "
                "AND classification_scheme_id=(SELECT id FROM classification_schemes WHERE scheme_code='SYMBOL-CATEGORY-FAMILY')"
            ), {"u": actor_id})
        after = _details(client, published["nd0050"])["classifications"]
        assert {row["schemeCode"] for row in after} == before - {"ENGINEERING-DISCIPLINE", "SYMBOL-CATEGORY-FAMILY"}
        assert all(row["status"] not in {"rejected", "retired"} for row in after)

    def test_an_unverified_mapping_is_not_shown(self, env, reader, published):
        client, _ = reader
        with env["engine"].begin() as connection:
            connection.execute(text(
                "UPDATE concept_external_references SET mapping_status='proposed', reviewed_at=NULL, reviewed_by_user_id=NULL "
                "WHERE external_identifier='http://rdl.example.test/RDS552689'"
            ))
        try:
            identifiers = [row["identifier"] for row in _details(client, published["nd0004"])["externalMappings"]]
            assert "http://rdl.example.test/RDS552689" not in identifiers
        finally:
            with env["engine"].begin() as connection:
                connection.execute(text(
                    "UPDATE concept_external_references SET mapping_status='verified', reviewed_at=now() "
                    "WHERE external_identifier='http://rdl.example.test/RDS552689'"
                ))


class TestVisibilityAndAccess:
    def test_an_organization_private_symbol_is_a_404_to_its_owner_and_to_outsiders(self, env, reader, published):
        engine = env["engine"]
        acme, Session_ = _client(engine)
        other, _ = _client(engine)
        acme_admin = _create_user_with_global_roles(Session_, email="details-acme@example.test", display_name="AcmeAdmin", roles=[])
        _add_membership(Session_, acme_admin, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
        _login(acme, "details-acme@example.test")
        other_admin = _create_user_with_global_roles(Session_, email="details-other@example.test", display_name="OtherAdmin", roles=[])
        _add_membership(Session_, other_admin, code="other", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
        _login(other, "details-other@example.test")
        private_id = _make_organization_wide_symbol(acme, name="Details Private Hydrant")

        # The symbol itself opens for its owner, so 404 is a decision about details.
        assert acme.get(f"{V1}/published/symbols/{private_id}").status_code == 200
        unknown = acme.get(f"{V1}/published/symbols/{uuid.uuid4()}/details")
        assert unknown.status_code == 404
        owner_view = acme.get(f"{V1}/published/symbols/{private_id}/details")
        assert owner_view.status_code == 404, owner_view.text
        assert owner_view.json() == unknown.json()
        outsider_view = other.get(f"{V1}/published/symbols/{private_id}/details")
        assert outsider_view.status_code == 404 and outsider_view.json() == unknown.json()
        # A public symbol still opens for an organization-bound session.
        assert acme.get(f"{V1}/published/symbols/{published['nd0004']}/details").status_code == 200

    def test_an_unknown_reference_is_a_404_and_an_anonymous_caller_is_refused_like_its_siblings(self, env, reader, published):
        client, _ = reader
        unknown = client.get(f"{V1}/published/symbols/NOPE-1/details")
        assert unknown.status_code == 404
        assert unknown.json()["code"] == "catalog_symbol_not_found"
        anonymous, _ = _client(env["engine"])
        sibling = anonymous.get(f"{V1}/published/symbols/{published['nd0004']}")
        details = anonymous.get(f"{V1}/published/symbols/{published['nd0004']}/details")
        assert sibling.status_code in {401, 403}
        assert details.status_code == sibling.status_code

    def test_the_legacy_api_path_serves_the_same_details(self, reader, published):
        client, _ = reader
        legacy = client.get(f"/api/published/symbols/{published['nd0004']}/details")
        assert legacy.status_code == 200
        assert legacy.json() == _details(client, published["nd0004"])


class TestDexpiClassFilter:
    def test_the_filter_returns_only_symbols_of_that_class(self, reader, published):
        client, _ = reader
        result = _search(client, dexpiClass="CentrifugalPump")
        assert result["total"] == 2
        assert sorted(item["catalogSymbolId"] for item in result["items"]) == sorted(["TST-1", published["nd0136"]])
        assert _search(client, dexpiClass="CustomOperatedValve")["total"] == 1
        assert _search(client, dexpiClass="NoSuchClass")["total"] == 0

    def test_blank_is_ignored_and_whitespace_is_trimmed(self, reader):
        client, _ = reader
        everything = _search(client)["total"]
        assert _search(client, dexpiClass="")["total"] == everything
        assert _search(client, dexpiClass="   ")["total"] == everything
        assert _search(client, dexpiClass="  CentrifugalPump  ")["total"] == 2

    def test_it_composes_with_search_text_facets_and_paging(self, reader, published):
        client, _ = reader
        assert _search(client, dexpiClass="CentrifugalPump", q="pump")["total"] <= 2
        by_pack = _search(client, dexpiClass="CentrifugalPump", pack="DISC DEXPI symbol library (DISC Profile 0.6.3)")
        assert by_pack["total"] == 1
        assert [item["catalogSymbolId"] for item in by_pack["items"]] == [published["nd0136"]]
        assert _search(client, dexpiClass="CentrifugalPump", q="zzzz-no-such-word")["total"] == 0
        first = _search(client, dexpiClass="CentrifugalPump", pageSize=1, page=1)
        second = _search(client, dexpiClass="CentrifugalPump", pageSize=1, page=2)
        assert first["total"] == second["total"] == 2
        assert len(first["items"]) == len(second["items"]) == 1
        assert first["items"][0]["catalogSymbolId"] != second["items"][0]["catalogSymbolId"]

    def test_totals_and_facet_counts_agree_under_the_filter(self, reader):
        client, _ = reader
        result = _search(client, dexpiClass="CentrifugalPump")
        for key in ("pack", "catalogDisciplines", "availableFormats"):
            counted = sum(entry["count"] for entry in result["facets"][key])
            assert counted >= 0
        # The pack facet is single-valued, so its counts partition the matches
        # once the pack filter itself is not applied.
        assert sum(entry["count"] for entry in result["facets"]["pack"]) == result["total"]

    def test_it_is_not_a_facet(self, reader):
        client, _ = reader
        assert "dexpiClass" not in _search(client)["facets"]


class TestCommentSubmitters:
    def test_a_comment_never_shows_an_email_address(self, env, reader, published):
        client, _ = reader
        engine = env["engine"]
        Session_ = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        named = _create_user_with_global_roles(Session_, email="named-commenter@example.test", display_name="Dana Engineer", roles=[])
        unnamed = _user(Session_, email="unnamed-commenter@example.test")  # its display name is its email
        item = client.get(f"{V1}/published/symbols/{published['nd0004']}").json()["item"]
        symbol_id, page_id = uuid.UUID(item["symbolId"]), uuid.UUID(item["pageId"])
        with Session(engine) as session, session.begin():
            for user_id, detail in ((named, "Named."), (unnamed, "Unnamed.")):
                session.add(ClarificationRecord(
                    symbol_id=symbol_id, published_page_id=page_id, source="catalog", kind="comment",
                    status="open", submitted_by=user_id, detail=detail, created_at=NOW, updated_at=NOW,
                ))
        response = client.get(f"{V1}/published/symbols/{published['nd0004']}/comments")
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert {entry["detail"]: entry["submittedBy"] for entry in items} == {
            "Named.": "Dana Engineer", "Unnamed.": "Symgov user",
        }
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", json.dumps(response.json()))
