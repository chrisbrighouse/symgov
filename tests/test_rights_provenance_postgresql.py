"""Migration and service rehearsal for SM-P0-06 against a real PostgreSQL server.

Why this file exists: most of what makes SM-P0-06 worth having is
storage-level, and none of it can be proven by reading the migration text.

* `rights_records` has three nullable subject columns and exactly one may be
  set. PostgreSQL accepts a check constraint that evaluates to NULL, so the
  `case`-sum spelling has to be shown refusing zero subjects, refusing each
  pair, refusing all three, and accepting each one alone.
* Section 7.12's two vocabularies and the deployed
  `provenance_assessments.rights_disposition` enumeration have to be shown
  refusing each other's values on a real server, in both directions. That is
  the evidence that the existing table could not have been extended into the
  durable record.
* `approved_disposition_basis` is the one policy-bearing constraint in the
  package. Every one of its eighteen refusing combinations and every one of
  its accepting ones is exercised.
* `decided_by_user_id` is `ON DELETE RESTRICT`, the only such actor key in the
  semantic model. Only a real server shows that an approver cannot be deleted
  out from under an approved rights decision.
* Chain continuity in `record_asset_transformation` is service policy, and the
  per-row half of it is a database constraint. Both halves are exercised.
* The migration must add nothing to any pre-existing table. That is proven by
  taking the complete column inventory at 20260910_0056, downgrading, and
  showing the only difference is the two new tables.

Redaction: this file never prints the disposable container's connection
string, and every seeded identity uses a synthetic `@example.test` email.
Every standard code it registers is synthetic: 20260910_0055 seeded 219 real
CFIHOS standards, so `API SPEC 6D` and its neighbours are already taken.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

from symgov_backend.models import AssetTransformation, RightsRecord  # noqa: E402
from symgov_backend.rights_provenance import (  # noqa: E402
    NON_PERMISSIVE_DISPOSITIONS,
    PERMISSIVE_DISPOSITIONS,
    PERMITTING_RIGHTS_STATUSES,
    RIGHTS_DISPOSITIONS,
    RIGHTS_STATUSES,
    approved_rights_record,
    disposition_is_permitted,
    lineage_reaches_source_package,
    list_rights_records,
    propose_rights_record,
    record_asset_transformation,
    symbol_revision_rights_provenance,
    trace_asset_lineage,
    transition_rights_record,
)
from symgov_backend.source_package_acquisition import (  # noqa: E402
    add_source_package_entry,
    register_source_package,
)
from symgov_backend.standard_sources import (  # noqa: E402
    assert_symbol_standard_link,
    register_standard,
    register_standard_version,
)

# This package's own revision, which is head. A `*_postgresql.py` fixture
# pinned below head is a latent break: the ORM is a single global object that
# always reflects head, so it fails the moment a later migration adds a column
# to a table these models touch.
RIGHTS_REVISION = "20260910_0056"
PREVIOUS_REVISION = "20260910_0055"

NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 9, 10, 13, 0, 0, tzinfo=timezone.utc)

SOURCE_DIGEST = "1" * 64
DERIVED_DIGEST = "2" * 64
THIRD_DIGEST = "3" * 64

NEW_TABLES = ("rights_records", "asset_transformations")

# The deployed `provenance_assessments.rights_disposition` enumeration, minus
# `restricted`, which is a *status* in section 7.12 and a disposition in
# neither vocabulary.
DEPLOYED_ONLY_DISPOSITIONS = ("cleared", "unknown_warning", "conflict", "failed")

RIGHTS_COLUMNS = {
    "id",
    "source_package_id",
    "standard_version_id",
    "symbol_revision_id",
    "rights_status",
    "disposition",
    "licence_reference",
    "determination_method",
    "decision_status",
    "decided_by_user_id",
    "decided_at",
    "decision_reason",
    "evidence_json",
    "proposed_by_user_id",
    "created_at",
    "updated_at",
}

TRANSFORMATION_COLUMNS = {
    "id",
    "symbol_revision_id",
    "step_index",
    "source_package_entry_id",
    "source_asset_sha256",
    "tool_name",
    "tool_version",
    "derived_asset_sha256",
    "performed_at",
    "evidence_json",
    "recorded_by_user_id",
    "created_at",
}

# The five tables SM-P0-05 and the CFIHOS seed extended. SM-P0-06 adds no
# column to any of them, which is why this file's fixture is the only one that
# needed a revision bump.
PRESERVED_TABLES = (
    "standards",
    "standard_versions",
    "symbol_standard_links",
    "source_packages",
    "source_package_entries",
)


@pytest.fixture(scope="module")
def rights_database():
    with _database("symgov-rights-provenance") as (engine, url, raw_url):
        _alembic(url, "upgrade", RIGHTS_REVISION)
        yield engine, url


@pytest.fixture(scope="module")
def author_id(rights_database) -> uuid.UUID:
    engine, _ = rights_database
    identifier = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                "must_change_pin,is_active,created_at,updated_at) "
                "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
            ),
            {"id": identifier, "email": "rights-provenance-author@example.test", "now": NOW},
        )
    return identifier


@pytest.fixture()
def session(rights_database) -> Session:
    engine, _ = rights_database
    with Session(engine) as active_session:
        yield active_session
        active_session.rollback()


def _user(session: Session, label: str) -> uuid.UUID:
    identifier = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
            "must_change_pin,is_active,created_at,updated_at) "
            "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
        ),
        {"id": identifier, "email": f"{label}-{identifier.hex[:8]}@example.test", "now": NOW},
    )
    session.flush()
    return identifier


def _symbol_revision(session: Session, author: uuid.UUID) -> uuid.UUID:
    """Seed a draft governed symbol revision.

    Deliberately draft: 20260826_0031's publication invariant would otherwise
    demand a canonical catalog identifier, which is irrelevant here.
    """
    symbol_id, revision_id = uuid.uuid4(), uuid.uuid4()
    slug = f"rights-provenance-symbol-{uuid.uuid4().hex[:10]}"
    session.execute(
        text(
            "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,"
            "owner_id,created_at,updated_at) "
            "VALUES (:id,:slug,:slug,'Valves','Piping / P&ID',:owner,:now,:now)"
        ),
        {"id": symbol_id, "slug": slug, "owner": author, "now": NOW},
    )
    session.execute(
        text(
            "INSERT INTO symbol_revisions (id,symbol_id,revision_label,lifecycle_state,"
            "payload_json,author_id,created_at) "
            "VALUES (:id,:symbol,'1','draft','{}'::jsonb,:owner,:now)"
        ),
        {"id": revision_id, "symbol": symbol_id, "owner": author, "now": NOW},
    )
    session.flush()
    return revision_id


def _package(session: Session, **kwargs):
    package = register_source_package(
        session,
        package_code=f"RIGHTSPKG-{uuid.uuid4().hex[:8].upper()}",
        title="Illustrative test package",
        registered_at=NOW,
        **kwargs,
    )
    session.flush()
    return package


def _standard_version(session: Session) -> uuid.UUID:
    """Register a synthetic standard and one edition of it.

    The code is synthetic on purpose: 20260910_0055 seeded the real CFIHOS
    register, so every plausible real code is already taken.
    """
    standard = register_standard(
        session,
        standard_code=f"TEST-{uuid.uuid4().hex[:8].upper()}",
        title="Illustrative test standard",
        registered_at=NOW,
    )
    session.flush()
    version = register_standard_version(
        session, standard_id=standard.id, version_label="2012", registered_at=NOW
    )
    session.flush()
    return version.id


def _raw_rights(session: Session, **columns) -> uuid.UUID:
    """Insert a rights record with raw SQL, bypassing the service layer.

    Every constraint test here goes through raw SQL on purpose: the point is
    what the *database* refuses, not what the validators refuse first.
    """
    values = {
        "id": uuid.uuid4(),
        "disposition": "metadata_only",
        "determination_method": "manual",
        "created_at": NOW,
        "updated_at": NOW,
        **columns,
    }
    names = ", ".join(values)
    placeholders = ", ".join(f":{name}" for name in values)
    session.execute(text(f"INSERT INTO rights_records ({names}) VALUES ({placeholders})"), values)
    session.flush()
    return values["id"]


def _raw_transformation(session: Session, revision_id: uuid.UUID, **columns) -> uuid.UUID:
    values = {
        "id": uuid.uuid4(),
        "symbol_revision_id": revision_id,
        "step_index": 1,
        "source_asset_sha256": SOURCE_DIGEST,
        "tool_name": "svgtool",
        "tool_version": "1.2.3",
        "derived_asset_sha256": DERIVED_DIGEST,
        "performed_at": NOW,
        "created_at": NOW,
        **columns,
    }
    names = ", ".join(values)
    placeholders = ", ".join(f":{name}" for name in values)
    session.execute(
        text(f"INSERT INTO asset_transformations ({names}) VALUES ({placeholders})"), values
    )
    session.flush()
    return values["id"]


def _seed_provenance_assessment(session: Session, *, rights_disposition: str) -> None:
    """Insert into the table SM-P0-06 rejected as the durable record.

    Read-only in intent: this file writes it once, inside a transaction that
    is rolled back, purely to show its deployed vocabulary refusing section
    7.12's values. Nothing in `rights_provenance.py` touches it.
    """
    agent_id, queue_id, intake_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO agent_definitions (id,slug,display_name,role,model,status,"
            "queue_family,created_at,updated_at) "
            "VALUES (:id,:slug,'Test agent','tester','test-model','active','test',:now,:now)"
        ),
        {"id": agent_id, "slug": f"rights-test-{agent_id.hex[:8]}", "now": NOW},
    )
    session.execute(
        text(
            "INSERT INTO agent_queue_items (id,agent_id,source_type,source_id,status,priority,"
            "payload_json,created_at) "
            "VALUES (:id,:agent,'intake',:source,'queued','normal','{}'::jsonb,:now)"
        ),
        {"id": queue_id, "agent": agent_id, "source": uuid.uuid4(), "now": NOW},
    )
    session.execute(
        text(
            "INSERT INTO intake_records (id,queue_item_id,source_type,source_ref,submitter,"
            "submission_kind,intake_status,eligibility_status,normalized_submission_json,"
            "routing_recommendation_json,report_json,created_at) "
            "VALUES (:id,:queue,'email','ref','submitter@example.test','single','received',"
            "'eligible','{}'::jsonb,'{}'::jsonb,'{}'::jsonb,:now)"
        ),
        {"id": intake_id, "queue": queue_id, "now": NOW},
    )
    session.execute(
        text(
            "INSERT INTO provenance_assessments (id,queue_item_id,intake_record_id,rights_status,"
            "rights_disposition,processing_outcome,risk_level,confidence,summary,evidence_json,"
            "report_json,assessed_at) "
            "VALUES (:id,:queue,:intake,'unknown',:disposition,'pass','low',0.5,'summary',"
            "'{}'::jsonb,'{}'::jsonb,:now)"
        ),
        {
            "id": uuid.uuid4(),
            "queue": queue_id,
            "intake": intake_id,
            "disposition": rights_disposition,
            "now": NOW,
        },
    )
    session.flush()


# --------------------------------------------------------------------------
# Migration shape
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "columns"),
    [("rights_records", RIGHTS_COLUMNS), ("asset_transformations", TRANSFORMATION_COLUMNS)],
    ids=lambda value: value if isinstance(value, str) else "columns",
)
def test_upgrade_created_the_table_with_exactly_the_declared_columns(
    rights_database, table, columns
):
    engine, _ = rights_database
    with engine.begin() as connection:
        present = set(
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=:table"
                ),
                {"table": table},
            ).scalars()
        )
    assert present == columns, present ^ columns


def test_the_columns_that_are_not_null_are_the_ones_the_orm_declares(rights_database):
    """A new table may carry NOT NULL columns -- unlike SM-P0-05, which
    extended `source_packages` while it held production rows. Which ones are
    required is a design decision, so it is pinned rather than assumed."""
    engine, _ = rights_database
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT table_name, column_name, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name = ANY(:names)"
            ),
            {"names": list(NEW_TABLES)},
        ).all()
    required = {
        (row.table_name, row.column_name) for row in rows if row.is_nullable == "NO"
    }
    assert required == {
        ("rights_records", "id"),
        ("rights_records", "rights_status"),
        ("rights_records", "disposition"),
        ("rights_records", "determination_method"),
        ("rights_records", "decision_status"),
        ("rights_records", "evidence_json"),
        ("rights_records", "created_at"),
        ("rights_records", "updated_at"),
        ("asset_transformations", "id"),
        ("asset_transformations", "symbol_revision_id"),
        ("asset_transformations", "step_index"),
        ("asset_transformations", "tool_name"),
        ("asset_transformations", "tool_version"),
        ("asset_transformations", "derived_asset_sha256"),
        ("asset_transformations", "performed_at"),
        ("asset_transformations", "evidence_json"),
        ("asset_transformations", "created_at"),
    }
    defaults = {
        (row.table_name, row.column_name): row.column_default
        for row in rows
        if row.column_default is not None
    }
    assert defaults[("rights_records", "rights_status")].startswith("'unknown'")
    assert defaults[("rights_records", "decision_status")].startswith("'proposed'")
    assert ("rights_records", "disposition") not in defaults, (
        "a disposition has no honest default; the caller must state one"
    )


def test_every_constraint_name_survived_the_63_character_limit(rights_database):
    """A name PostgreSQL had to truncate comes back shortened or
    hash-suffixed; a name passed already-prefixed comes back doubled. Both
    failure modes show up here."""
    engine, _ = rights_database
    with engine.begin() as connection:
        names = set(
            connection.execute(
                text(
                    "SELECT conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                    "WHERE t.relname = ANY(:names)"
                ),
                {"names": list(NEW_TABLES)},
            ).scalars()
        )
        index_names = set(
            connection.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE schemaname='public' "
                    "AND tablename = ANY(:names)"
                ),
                {"names": list(NEW_TABLES)},
            ).scalars()
        )
    for expected in (
        "ck_rights_records_subject_exactly_one",
        "ck_rights_records_approved_licence_reference",
        "ck_rights_records_approved_not_ai_determined",
        "ck_rights_records_decision_actor",
        "ck_asset_transformations_source_asset_identified",
        "ck_asset_transformations_transformation_changed_asset",
        "fk_rights_records_decided_by_user_id",
        "fk_asset_transformations_source_package_entry_id",
    ):
        assert expected in names, sorted(names)
    assert all(len(name) <= 63 for name in names | index_names)
    for table in NEW_TABLES:
        assert not any(name.startswith(f"ck_{table}_ck_") for name in names)
    assert len("ck_asset_transformations_transformation_changed_asset") == 53


@pytest.mark.parametrize(
    "model", (RightsRecord, AssetTransformation), ids=lambda m: m.__tablename__
)
def test_the_new_tables_carry_exactly_the_orm_constraint_names(rights_database, model):
    """SM-P0-03's parity guard, applied to the two tables this package adds.
    The guard itself runs at 20260909_0053, where neither table exists."""
    from test_semantic_constraint_name_repair_postgresql import (
        _database_names,
        _expected_names,
    )

    engine, _ = rights_database
    with engine.begin() as connection:
        actual = _database_names(connection).get(model.__tablename__, set())
    assert actual == _expected_names(model.__table__)


def test_these_tables_are_not_recorded_as_pre_existing_name_drift():
    """20260909_0053 emptied that allowlist and it must stay empty. A new
    entry would mean this migration introduced name drift instead of passing a
    bare CheckConstraint name."""
    from test_semantic_constraint_name_repair_postgresql import PRE_EXISTING_NAME_DRIFT

    assert PRE_EXISTING_NAME_DRIFT == frozenset()
    assert PRE_EXISTING_NAME_DRIFT.isdisjoint(set(NEW_TABLES))


def test_every_declared_index_exists_with_the_predicate_it_claims(rights_database):
    engine, _ = rights_database
    with engine.begin() as connection:
        indexes = dict(
            connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='public' "
                    "AND tablename = ANY(:names)"
                ),
                {"names": list(NEW_TABLES)},
            ).all()
        )
    for name in (
        "uq_rights_records_approved_source_package",
        "uq_rights_records_approved_standard_version",
        "uq_rights_records_approved_symbol_revision",
    ):
        definition = indexes[name]
        assert "UNIQUE" in definition, name
        assert "decision_status = 'approved'" in definition, name
        assert "IS NOT NULL" in definition, name
    for name in (
        "ix_rights_records_source_package_id",
        "ix_rights_records_standard_version_id",
        "ix_rights_records_symbol_revision_id",
    ):
        assert "UNIQUE" not in indexes[name], name
        assert "IS NOT NULL" in indexes[name], name
    step = indexes["uq_asset_transformations_revision_step"]
    assert "UNIQUE" in step
    assert "step_index" in step
    assert "UNIQUE" not in indexes["ix_asset_transformations_derived_asset_sha256"]
    assert "IS NOT NULL" in indexes["ix_asset_transformations_source_asset_sha256"]
    assert "IS NOT NULL" in indexes["ix_asset_transformations_source_package_entry_id"]


def test_the_preserved_tables_gained_no_column_and_point_at_nothing_new(rights_database):
    """SM-P0-06 adds no column to any of the five tables SM-P0-05 and the
    CFIHOS seed extended: `rights_records` points at the package, not the
    package at the record. That is why only this package's own fixture needed
    a revision bump, and why the four already-pinned `*_postgresql.py`
    constants stay where they are."""
    engine, _ = rights_database
    with engine.begin() as connection:
        columns = set(
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name = ANY(:names)"
                ),
                {"names": list(PRESERVED_TABLES)},
            ).scalars()
        )
        referencing = set(
            connection.execute(
                text(
                    "SELECT r.relname FROM pg_constraint c "
                    "JOIN pg_class r ON r.oid = c.conrelid "
                    "JOIN pg_class f ON f.oid = c.confrelid "
                    "WHERE c.contype = 'f' AND f.relname = ANY(:names)"
                ),
                {"names": list(NEW_TABLES)},
            ).scalars()
        )
    assert "rights_record_id" not in columns
    assert "asset_transformation_id" not in columns
    assert "disposition" not in columns
    assert referencing <= set(NEW_TABLES), sorted(referencing)


# --------------------------------------------------------------------------
# Exactly one subject
# --------------------------------------------------------------------------


def test_a_rights_record_with_no_subject_is_refused(session):
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_rights(session)
    assert "subject_exactly_one" in str(caught.value)


@pytest.mark.parametrize(
    "subject", ["source_package_id", "standard_version_id", "symbol_revision_id"]
)
def test_a_rights_record_with_exactly_one_subject_is_accepted(session, author_id, subject):
    values = {
        "source_package_id": lambda: _package(session).id,
        "standard_version_id": lambda: _standard_version(session),
        "symbol_revision_id": lambda: _symbol_revision(session, author_id),
    }
    _raw_rights(session, **{subject: values[subject]()})


@pytest.mark.parametrize(
    "pair",
    [
        ("source_package_id", "standard_version_id"),
        ("source_package_id", "symbol_revision_id"),
        ("standard_version_id", "symbol_revision_id"),
    ],
)
def test_a_rights_record_with_two_subjects_is_refused(session, author_id, pair):
    """Every branch of the `case`-sum has to be shown rejecting a row: a
    constraint over three nullable columns is exactly where three-valued logic
    hides a hole."""
    values = {
        "source_package_id": lambda: _package(session).id,
        "standard_version_id": lambda: _standard_version(session),
        "symbol_revision_id": lambda: _symbol_revision(session, author_id),
    }
    columns = {name: values[name]() for name in pair}
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_rights(session, **columns)
    assert "subject_exactly_one" in str(caught.value)


def test_a_rights_record_with_all_three_subjects_is_refused(session, author_id):
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_rights(
            session,
            source_package_id=_package(session).id,
            standard_version_id=_standard_version(session),
            symbol_revision_id=_symbol_revision(session, author_id),
        )
    assert "subject_exactly_one" in str(caught.value)


# --------------------------------------------------------------------------
# Section 7.12's vocabularies, and the deployed one
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", sorted(RIGHTS_STATUSES))
def test_all_six_section_7_12_rights_statuses_are_accepted(session, value):
    disposition = "metadata_only" if value not in PERMITTING_RIGHTS_STATUSES else "display"
    _raw_rights(
        session,
        source_package_id=_package(session).id,
        rights_status=value,
        disposition=disposition,
    )


@pytest.mark.parametrize("value", sorted(RIGHTS_DISPOSITIONS))
def test_all_six_section_7_12_dispositions_are_accepted(session, value):
    _raw_rights(session, source_package_id=_package(session).id, disposition=value)


@pytest.mark.parametrize("value", DEPLOYED_ONLY_DISPOSITIONS)
def test_a_disposition_from_the_deployed_assessment_vocabulary_is_refused(session, value):
    """Section 7.12's vocabulary and `provenance_assessments`' share not one
    value. This is the first half of the evidence."""
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_rights(session, source_package_id=_package(session).id, disposition=value)
    assert "ck_rights_records_disposition" in str(caught.value)


@pytest.mark.parametrize("value", DEPLOYED_ONLY_DISPOSITIONS)
def test_a_status_from_the_deployed_assessment_vocabulary_is_refused(session, value):
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_rights(session, source_package_id=_package(session).id, rights_status=value)
    assert "ck_rights_records_rights_status" in str(caught.value)


@pytest.mark.parametrize("value", sorted(RIGHTS_DISPOSITIONS))
def test_the_deployed_assessment_table_refuses_every_section_7_12_disposition(session, value):
    """And the other half. `provenance_assessments` could not have been
    extended into the durable record: its deployed vocabulary refuses every
    value section 7.12 requires, so adopting section 7.12's would mean
    changing a constraint on a table six live modules write."""
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _seed_provenance_assessment(session, rights_disposition=value)
    assert "ck_provenance_assessments_rights_disposition" in str(caught.value)


def test_the_deployed_assessment_table_still_accepts_its_own_vocabulary(session):
    """The premise cuts both ways: the existing table is untouched by this
    package and still works exactly as the intake pipeline expects."""
    _seed_provenance_assessment(session, rights_disposition="cleared")


@pytest.mark.parametrize(
    "value",
    ["source_mapping", "imported", "legacy_backfill", "import_manifest", "contributed", "rule"],
)
def test_a_determination_method_from_another_vocabulary_is_refused(session, value):
    """The sixth `method` vocabulary is deliberately distinct from the five
    before it, and the database says so."""
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_rights(
            session, source_package_id=_package(session).id, determination_method=value
        )
    assert "determination_method" in str(caught.value)


def test_the_governance_status_is_approved_and_not_verified(session):
    """The one deliberate divergence from the model's shared
    `proposed | verified | rejected | retired` shape: section 7.12's own word
    is "approved"."""
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_rights(
            session, source_package_id=_package(session).id, decision_status="verified"
        )
    assert "decision_status" in str(caught.value)


# --------------------------------------------------------------------------
# The governed decision: who, when and why
# --------------------------------------------------------------------------


def test_an_approved_record_without_a_decider_is_refused(session):
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_rights(
            session,
            source_package_id=_package(session).id,
            decision_status="approved",
            decided_at=NOW,
            decision_reason="reviewed the contract",
        )
    assert "decision_actor" in str(caught.value)


def test_an_approved_record_without_a_decision_time_is_refused(session, author_id):
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_rights(
            session,
            source_package_id=_package(session).id,
            decision_status="approved",
            decided_by_user_id=author_id,
            decision_reason="reviewed the contract",
        )
    assert "decision_actor" in str(caught.value)


def test_an_approved_record_without_a_reason_is_refused(session, author_id):
    """Section 7.12 asks for the why as well as the who and the when. No
    other table in this model requires one."""
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_rights(
            session,
            source_package_id=_package(session).id,
            decision_status="approved",
            decided_by_user_id=author_id,
            decided_at=NOW,
        )
    assert "approved_reason" in str(caught.value)


def test_an_approved_record_with_who_when_and_why_is_accepted(session, author_id):
    _raw_rights(
        session,
        source_package_id=_package(session).id,
        rights_status="open",
        disposition="distribute",
        decision_status="approved",
        decided_by_user_id=author_id,
        decided_at=NOW,
        decision_reason="published under an open licence",
    )


def test_an_anonymous_rejection_is_refused(session):
    """Section 7.10 leaves `symbol_standard_links` with no reviewer pair, so a
    rejection there records no actor. `rights_records` has one pair used by
    both decisions, which is why a rejection here cannot be anonymous."""
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_rights(
            session, source_package_id=_package(session).id, decision_status="rejected"
        )
    assert "decision_actor" in str(caught.value)


def test_a_rejection_with_an_actor_is_accepted(session, author_id):
    _raw_rights(
        session,
        source_package_id=_package(session).id,
        decision_status="rejected",
        decided_by_user_id=author_id,
        decided_at=NOW,
    )


@pytest.mark.parametrize("status", ["proposed", "retired"])
def test_a_proposal_and_a_retirement_need_no_actor(session, status):
    """A proposal has taken no decision, and a retirement is supersession --
    the actor of record is the successor's approver."""
    _raw_rights(session, source_package_id=_package(session).id, decision_status=status)


def test_an_ai_assisted_determination_cannot_be_approved(session, author_id):
    """Section 8.4, and stricter than section 7.10's verification: there is no
    controlled-system rights decision."""
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_rights(
            session,
            source_package_id=_package(session).id,
            determination_method="ai_assisted",
            decision_status="approved",
            decided_by_user_id=author_id,
            decided_at=NOW,
            decision_reason="the agent read the terms",
        )
    assert "approved_not_ai_determined" in str(caught.value)


def test_an_ai_assisted_determination_can_be_proposed(session):
    _raw_rights(
        session, source_package_id=_package(session).id, determination_method="ai_assisted"
    )


@pytest.mark.parametrize("status", ["licensed", "restricted"])
def test_approving_a_licence_backed_status_without_a_reference_is_refused(
    session, author_id, status
):
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_rights(
            session,
            source_package_id=_package(session).id,
            rights_status=status,
            disposition="display",
            decision_status="approved",
            decided_by_user_id=author_id,
            decided_at=NOW,
            decision_reason="reviewed",
        )
    assert "approved_licence_reference" in str(caught.value)


@pytest.mark.parametrize("status", ["licensed", "restricted"])
def test_approving_a_licence_backed_status_with_a_reference_is_accepted(
    session, author_id, status
):
    _raw_rights(
        session,
        source_package_id=_package(session).id,
        rights_status=status,
        disposition="display",
        licence_reference="CONTRACT-2026-0031",
        decision_status="approved",
        decided_by_user_id=author_id,
        decided_at=NOW,
        decision_reason="reviewed",
    )


def test_proposing_a_licence_backed_status_without_a_reference_is_allowed(session):
    """Gated on the approval, not on the status: a reviewer can put "this
    looks licensed" forward before the contract has been found."""
    _raw_rights(
        session,
        source_package_id=_package(session).id,
        rights_status="licensed",
        disposition="display",
    )


# --------------------------------------------------------------------------
# The policy-bearing constraint, every combination
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rights_status", "disposition"),
    [
        (status, disposition)
        for status in sorted(RIGHTS_STATUSES - PERMITTING_RIGHTS_STATUSES)
        for disposition in sorted(PERMISSIVE_DISPOSITIONS)
    ],
)
def test_the_database_accepts_a_pair_only_the_service_refuses(
    session, author_id, rights_status, disposition
):
    """Eighteen combinations the *service* refuses and the database accepts.

    That a permissive disposition may only be approved on a status that
    supports it is service policy by decision, not a check constraint: rights
    gating is SM-P0-08's, and a storage-level copy would need a migration to
    loosen. This test pins the split so nobody later reads the missing
    constraint as an oversight -- the row inserts, and
    `test_approving_a_permissive_disposition_on_an_unresolved_status_is_refused`
    shows the service turning it down.
    """
    assert disposition_is_permitted(rights_status, disposition) is False
    _raw_rights(
        session,
        source_package_id=_package(session).id,
        rights_status=rights_status,
        disposition=disposition,
        decision_status="approved",
        decided_by_user_id=author_id,
        decided_at=NOW,
        decision_reason="reviewed",
    )


@pytest.mark.parametrize(
    ("rights_status", "disposition"),
    [
        (status, disposition)
        for status in sorted(RIGHTS_STATUSES - PERMITTING_RIGHTS_STATUSES)
        for disposition in sorted(NON_PERMISSIVE_DISPOSITIONS)
    ],
)
def test_an_unresolved_status_can_still_approve_metadata_only_or_a_rejection(
    session, author_id, rights_status, disposition
):
    """An orphan work is recorded honestly rather than being unrepresentable."""
    _raw_rights(
        session,
        source_package_id=_package(session).id,
        rights_status=rights_status,
        disposition=disposition,
        decision_status="approved",
        decided_by_user_id=author_id,
        decided_at=NOW,
        decision_reason="rights holder could not be identified",
    )


@pytest.mark.parametrize(
    ("rights_status", "disposition"),
    [
        (status, disposition)
        for status in sorted(PERMITTING_RIGHTS_STATUSES)
        for disposition in sorted(PERMISSIVE_DISPOSITIONS)
    ],
)
def test_a_permitting_status_can_approve_any_disposition(
    session, author_id, rights_status, disposition
):
    _raw_rights(
        session,
        source_package_id=_package(session).id,
        rights_status=rights_status,
        disposition=disposition,
        licence_reference="CONTRACT-2026-0031",
        decision_status="approved",
        decided_by_user_id=author_id,
        decided_at=NOW,
        decision_reason="reviewed",
    )


@pytest.mark.parametrize(
    ("rights_status", "disposition"),
    [
        (status, disposition)
        for status in sorted(RIGHTS_STATUSES - PERMITTING_RIGHTS_STATUSES)
        for disposition in sorted(PERMISSIVE_DISPOSITIONS)
    ],
)
def test_a_permissive_disposition_may_still_be_proposed_on_any_status(
    session, rights_status, disposition
):
    _raw_rights(
        session,
        source_package_id=_package(session).id,
        rights_status=rights_status,
        disposition=disposition,
    )


# --------------------------------------------------------------------------
# Bounds
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   ", "x" * 513])
def test_a_licence_reference_that_is_blank_or_a_pasted_licence_is_refused(session, value):
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_rights(
            session, source_package_id=_package(session).id, licence_reference=value
        )
    assert "licence_reference" in str(caught.value)


@pytest.mark.parametrize("value", ["", "   ", "x" * 2001])
def test_a_blank_or_over_long_decision_reason_is_refused(session, value):
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_rights(session, source_package_id=_package(session).id, decision_reason=value)
    assert "decision_reason" in str(caught.value)


@pytest.mark.parametrize("value", ['"text"', "[]", "7"])
def test_rights_evidence_must_be_a_json_object(session, value):
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        session.execute(
            text(
                "INSERT INTO rights_records (id,source_package_id,disposition,"
                "determination_method,evidence_json,created_at,updated_at) "
                "VALUES (:id,:package,'metadata_only','manual',CAST(:evidence AS jsonb),:now,:now)"
            ),
            {
                "id": uuid.uuid4(),
                "package": _package(session).id,
                "evidence": value,
                "now": NOW,
            },
        )
        session.flush()
    assert "evidence_json_object" in str(caught.value)


# --------------------------------------------------------------------------
# One approved record per subject
# --------------------------------------------------------------------------


def test_two_approved_records_for_one_package_cannot_both_exist(session, author_id):
    package_id = _package(session).id
    approved = {
        "rights_status": "open",
        "disposition": "display",
        "decision_status": "approved",
        "decided_by_user_id": author_id,
        "decided_at": NOW,
        "decision_reason": "reviewed",
    }
    _raw_rights(session, source_package_id=package_id, **approved)
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_rights(session, source_package_id=package_id, **approved)
    assert "uq_rights_records_approved_source_package" in str(caught.value)


def test_two_proposals_for_one_package_can_sit_side_by_side(session):
    """Competing candidates are how a review works. Only the approved record
    is unique per subject."""
    package_id = _package(session).id
    _raw_rights(session, source_package_id=package_id, disposition="display")
    _raw_rights(session, source_package_id=package_id, disposition="metadata_only")
    count = session.execute(
        text("SELECT count(*) FROM rights_records WHERE source_package_id = :id"),
        {"id": package_id},
    ).scalar_one()
    assert count == 2


@pytest.mark.parametrize("status", ["rejected", "retired"])
def test_a_rejected_or_retired_record_does_not_block_an_approval(session, author_id, status):
    package_id = _package(session).id
    closed = {"decision_status": status}
    if status == "rejected":
        closed |= {"decided_by_user_id": author_id, "decided_at": NOW}
    _raw_rights(session, source_package_id=package_id, **closed)
    _raw_rights(
        session,
        source_package_id=package_id,
        rights_status="open",
        disposition="display",
        decision_status="approved",
        decided_by_user_id=author_id,
        decided_at=NOW,
        decision_reason="reviewed",
    )


def test_two_approved_records_for_different_subjects_do_not_collide(session, author_id):
    """The three partial unique indexes are per subject column, so a package
    decision and an edition decision are independent."""
    approved = {
        "rights_status": "open",
        "disposition": "display",
        "decision_status": "approved",
        "decided_by_user_id": author_id,
        "decided_at": NOW,
        "decision_reason": "reviewed",
    }
    _raw_rights(session, source_package_id=_package(session).id, **approved)
    _raw_rights(session, standard_version_id=_standard_version(session), **approved)
    _raw_rights(session, symbol_revision_id=_symbol_revision(session, author_id), **approved)


# --------------------------------------------------------------------------
# Retention of the decision (section 14.4)
# --------------------------------------------------------------------------


def test_the_approver_of_a_rights_decision_cannot_be_deleted(session):
    """`ON DELETE RESTRICT`, the only such actor key in the semantic model.
    Section 14.4 keeps the governance decision history with the governed data,
    and `decision_actor` would refuse a SET NULL anyway -- so RESTRICT reports
    the real reason rather than a check violation."""
    decider = _user(session, "rights-decider")
    _raw_rights(
        session,
        source_package_id=_package(session).id,
        rights_status="open",
        disposition="display",
        decision_status="approved",
        decided_by_user_id=decider,
        decided_at=NOW,
        decision_reason="reviewed",
    )
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        session.execute(text("DELETE FROM users WHERE id = :id"), {"id": decider})
        session.flush()
    assert "fk_rights_records_decided_by_user_id" in str(caught.value)


def test_the_proposer_of_a_rights_record_can_still_be_deleted(session):
    """A proposal's author is not the durable record, so that key stays
    `SET NULL` like every other actor column in the model."""
    proposer = _user(session, "rights-proposer")
    record_id = _raw_rights(
        session, source_package_id=_package(session).id, proposed_by_user_id=proposer
    )
    session.execute(text("DELETE FROM users WHERE id = :id"), {"id": proposer})
    session.flush()
    remaining = session.execute(
        text("SELECT proposed_by_user_id FROM rights_records WHERE id = :id"), {"id": record_id}
    ).scalar_one()
    assert remaining is None


def test_a_source_package_with_a_rights_record_cannot_be_deleted(session):
    package_id = _package(session).id
    _raw_rights(session, source_package_id=package_id)
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        session.execute(text("DELETE FROM source_packages WHERE id = :id"), {"id": package_id})
        session.flush()
    assert "fk_rights_records_source_package_id" in str(caught.value)


# --------------------------------------------------------------------------
# Transformation lineage constraints
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "digest", ["a" * 63, "a" * 65, "g" * 64, "d41d8cd98f00b204e9800998ecf8427e", ""]
)
def test_a_derived_digest_that_is_not_sha256_is_refused(session, author_id, digest):
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_transformation(
            session, _symbol_revision(session, author_id), derived_asset_sha256=digest
        )
    assert "derived_asset_sha256" in str(caught.value)


@pytest.mark.parametrize("digest", ["a" * 63, "g" * 64, "d41d8cd98f00b204e9800998ecf8427e"])
def test_a_source_digest_that_is_not_sha256_is_refused(session, author_id, digest):
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_transformation(
            session, _symbol_revision(session, author_id), source_asset_sha256=digest
        )
    assert "source_asset_sha256" in str(caught.value)


def test_a_derived_asset_with_no_identified_source_is_refused(session, author_id):
    """Section 7.12's chain starts somewhere. Either the source digest or the
    package entry it came from must be present."""
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_transformation(
            session,
            _symbol_revision(session, author_id),
            source_asset_sha256=None,
            source_package_entry_id=None,
        )
    assert "source_asset_identified" in str(caught.value)


def test_a_derived_asset_identified_only_by_its_package_entry_is_accepted(session, author_id):
    """Section 7.12 says SHA-256 "where available", and a licensed source that
    may not be stored has none. The entry identifies it instead."""
    revision_id = _symbol_revision(session, author_id)
    package = _package(session)
    entry = add_source_package_entry(
        session,
        source_package_id=package.id,
        symbol_revision_id=revision_id,
        added_at=NOW,
        source_path="symbols/valve.dxf",
    )
    session.flush()
    _raw_transformation(
        session,
        revision_id,
        source_asset_sha256=None,
        source_package_entry_id=entry.id,
    )


def test_a_step_that_produced_an_identical_asset_is_refused(session, author_id):
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_transformation(
            session,
            _symbol_revision(session, author_id),
            source_asset_sha256=SOURCE_DIGEST,
            derived_asset_sha256=SOURCE_DIGEST,
        )
    assert "transformation_changed_asset" in str(caught.value)


@pytest.mark.parametrize("step", [0, -1])
def test_a_step_index_below_one_is_refused(session, author_id, step):
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_transformation(session, _symbol_revision(session, author_id), step_index=step)
    assert "step_index" in str(caught.value)


def test_two_steps_cannot_claim_the_same_position(session, author_id):
    revision_id = _symbol_revision(session, author_id)
    _raw_transformation(session, revision_id, step_index=1)
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_transformation(
            session, revision_id, step_index=1, derived_asset_sha256=THIRD_DIGEST
        )
    assert "uq_asset_transformations_revision_step" in str(caught.value)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("tool_name", "   "),
        ("tool_name", "x" * 129),
        ("tool_version", "   "),
        ("tool_version", "x" * 65),
    ],
)
def test_a_blank_or_over_long_tool_or_version_is_refused(session, author_id, column, value):
    """Section 7.12 asks for the tool *and* its version, because reproducing a
    transformation needs both."""
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        _raw_transformation(session, _symbol_revision(session, author_id), **{column: value})
    assert column in str(caught.value)


def test_a_transformation_evidence_must_be_a_json_object(session, author_id):
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        session.execute(
            text(
                "INSERT INTO asset_transformations (id,symbol_revision_id,step_index,"
                "source_asset_sha256,tool_name,tool_version,derived_asset_sha256,performed_at,"
                "evidence_json,created_at) "
                "VALUES (:id,:revision,1,:source,'svgtool','1.0',:derived,:now,"
                "CAST('[]' AS jsonb),:now)"
            ),
            {
                "id": uuid.uuid4(),
                "revision": _symbol_revision(session, author_id),
                "source": SOURCE_DIGEST,
                "derived": DERIVED_DIGEST,
                "now": NOW,
            },
        )
        session.flush()
    assert "evidence_json_object" in str(caught.value)


def test_a_source_package_entry_with_a_transformation_cannot_be_deleted(session, author_id):
    revision_id = _symbol_revision(session, author_id)
    package = _package(session)
    entry = add_source_package_entry(
        session,
        source_package_id=package.id,
        symbol_revision_id=revision_id,
        added_at=NOW,
        source_path="symbols/valve.dxf",
    )
    session.flush()
    _raw_transformation(session, revision_id, source_package_entry_id=entry.id)
    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        session.execute(
            text("DELETE FROM source_package_entries WHERE id = :id"), {"id": entry.id}
        )
        session.flush()
    assert "fk_asset_transformations_source_package_entry_id" in str(caught.value)


# --------------------------------------------------------------------------
# Service behaviour
# --------------------------------------------------------------------------


def test_a_rights_record_starts_proposed_whatever_produced_it(session):
    package = _package(session)
    record = propose_rights_record(
        session,
        source_package_id=package.id,
        disposition="distribute",
        determination_method="licence_document",
        proposed_at=NOW,
        rights_status="licensed",
        licence_reference="CONTRACT-2026-0031",
    )
    session.flush()
    assert record.decision_status == "proposed"
    assert record.decided_by_user_id is None
    assert record.decided_at is None
    assert approved_rights_record(session, source_package_id=package.id) is None


def test_approving_records_who_when_and_why(session, author_id):
    package = _package(session)
    record = propose_rights_record(
        session,
        source_package_id=package.id,
        disposition="distribute",
        determination_method="licence_document",
        proposed_at=NOW,
        rights_status="licensed",
        licence_reference="CONTRACT-2026-0031",
    )
    session.flush()
    transition_rights_record(
        session,
        record.id,
        target_status="approved",
        occurred_at=LATER,
        decided_by_user_id=author_id,
        decision_reason="counsel confirmed redistribution is permitted",
    )
    session.flush()
    assert record.decision_status == "approved"
    assert record.decided_by_user_id == author_id
    assert record.decided_at == LATER
    assert record.decision_reason == "counsel confirmed redistribution is permitted"
    assert approved_rights_record(session, source_package_id=package.id) is record


def test_approving_without_a_decider_or_a_reason_is_refused_by_the_service(session, author_id):
    package = _package(session)
    record = propose_rights_record(
        session,
        source_package_id=package.id,
        disposition="metadata_only",
        determination_method="manual",
        proposed_at=NOW,
    )
    session.flush()
    with pytest.raises(ValueError, match="named decider"):
        transition_rights_record(
            session, record.id, target_status="approved", occurred_at=LATER
        )
    with pytest.raises(ValueError, match="recorded reason"):
        transition_rights_record(
            session,
            record.id,
            target_status="approved",
            occurred_at=LATER,
            decided_by_user_id=author_id,
        )


def test_an_ai_assisted_record_cannot_be_approved_by_the_service_either(session, author_id):
    record = propose_rights_record(
        session,
        source_package_id=_package(session).id,
        disposition="metadata_only",
        determination_method="ai_assisted",
        proposed_at=NOW,
    )
    session.flush()
    with pytest.raises(ValueError, match="cannot be approved"):
        transition_rights_record(
            session,
            record.id,
            target_status="approved",
            occurred_at=LATER,
            decided_by_user_id=author_id,
            decision_reason="the agent read the terms",
        )


def test_approving_a_permissive_disposition_on_an_unresolved_status_is_refused(
    session, author_id
):
    record = propose_rights_record(
        session,
        source_package_id=_package(session).id,
        disposition="distribute",
        determination_method="manual",
        proposed_at=NOW,
    )
    session.flush()
    with pytest.raises(ValueError, match="cannot be approved while the rights status"):
        transition_rights_record(
            session,
            record.id,
            target_status="approved",
            occurred_at=LATER,
            decided_by_user_id=author_id,
            decision_reason="reviewed",
        )


def test_the_decision_can_establish_the_status_it_was_proposed_without(session, author_id):
    """A record proposed as `unknown` becomes `licensed` when the contract is
    found, which is usually what the decision *is*."""
    record = propose_rights_record(
        session,
        source_package_id=_package(session).id,
        disposition="distribute",
        determination_method="manual",
        proposed_at=NOW,
    )
    session.flush()
    transition_rights_record(
        session,
        record.id,
        target_status="approved",
        occurred_at=LATER,
        decided_by_user_id=author_id,
        decision_reason="contract located",
        rights_status="licensed",
        licence_reference="CONTRACT-2026-0031",
    )
    session.flush()
    assert record.rights_status == "licensed"
    assert record.licence_reference == "CONTRACT-2026-0031"


def test_approving_a_licence_backed_status_without_a_reference_is_refused_by_the_service(
    session, author_id
):
    record = propose_rights_record(
        session,
        source_package_id=_package(session).id,
        disposition="display",
        determination_method="manual",
        proposed_at=NOW,
        rights_status="licensed",
    )
    session.flush()
    with pytest.raises(ValueError, match="without a licence reference"):
        transition_rights_record(
            session,
            record.id,
            target_status="approved",
            occurred_at=LATER,
            decided_by_user_id=author_id,
            decision_reason="reviewed",
        )


def test_approving_a_successor_retires_the_previous_approved_record(session, author_id):
    """The supersession pattern SM-P0-01 through -05 all use: the new decision
    is taken and the old one retires, rather than the new decision being
    refused."""
    package = _package(session)
    first = propose_rights_record(
        session,
        source_package_id=package.id,
        disposition="metadata_only",
        determination_method="manual",
        proposed_at=NOW,
    )
    session.flush()
    transition_rights_record(
        session,
        first.id,
        target_status="approved",
        occurred_at=NOW,
        decided_by_user_id=author_id,
        decision_reason="rights not yet established",
    )
    session.flush()

    second = propose_rights_record(
        session,
        source_package_id=package.id,
        disposition="distribute",
        determination_method="licence_document",
        proposed_at=LATER,
        rights_status="licensed",
        licence_reference="CONTRACT-2026-0031",
    )
    session.flush()
    transition_rights_record(
        session,
        second.id,
        target_status="approved",
        occurred_at=LATER,
        decided_by_user_id=author_id,
        decision_reason="contract located",
    )
    session.flush()

    assert first.decision_status == "retired"
    assert second.decision_status == "approved"
    assert approved_rights_record(session, source_package_id=package.id) is second
    history = list_rights_records(session, source_package_id=package.id)
    assert [record.decision_status for record in history] == ["retired", "approved"]


def test_a_rejected_rights_record_is_terminal(session, author_id):
    record = propose_rights_record(
        session,
        source_package_id=_package(session).id,
        disposition="reject",
        determination_method="manual",
        proposed_at=NOW,
    )
    session.flush()
    transition_rights_record(
        session,
        record.id,
        target_status="rejected",
        occurred_at=LATER,
        decided_by_user_id=author_id,
        decision_reason="the provider forbids redistribution",
    )
    session.flush()
    for target in ("approved", "proposed", "retired"):
        with pytest.raises(ValueError, match="cannot move from rejected"):
            transition_rights_record(
                session,
                record.id,
                target_status=target,
                occurred_at=LATER,
                decided_by_user_id=author_id,
                decision_reason="reconsidered",
            )


def test_the_history_of_a_subject_survives_its_successor(session, author_id):
    """Section 14.4: the governance decision history is retained with the
    governed data, and why a disposition was refused is part of it."""
    package = _package(session)
    rejected = propose_rights_record(
        session,
        source_package_id=package.id,
        disposition="distribute",
        determination_method="manual",
        proposed_at=NOW,
    )
    session.flush()
    transition_rights_record(
        session,
        rejected.id,
        target_status="rejected",
        occurred_at=NOW,
        decided_by_user_id=author_id,
        decision_reason="no evidence of a licence",
    )
    session.flush()
    assert list_rights_records(session, source_package_id=package.id, decision_status="rejected") == [
        rejected
    ]
    assert rejected.decision_reason == "no evidence of a licence"


# --------------------------------------------------------------------------
# The transformation chain
# --------------------------------------------------------------------------


def _entry_with_digest(session: Session, revision_id: uuid.UUID, digest: str | None):
    package = _package(session)
    entry = add_source_package_entry(
        session,
        source_package_id=package.id,
        symbol_revision_id=revision_id,
        added_at=NOW,
        source_path="symbols/valve.dxf",
        original_asset_sha256=digest,
    )
    session.flush()
    return package, entry


def test_the_first_step_adopts_the_package_entrys_original_digest(session, author_id):
    """SM-P0-05 already records the acquired asset's hash, so the caller does
    not restate it and cannot contradict it by accident."""
    revision_id = _symbol_revision(session, author_id)
    _package_row, entry = _entry_with_digest(session, revision_id, SOURCE_DIGEST)
    step = record_asset_transformation(
        session,
        symbol_revision_id=revision_id,
        tool_name="dxf2svg",
        tool_version="0.9.1",
        derived_asset_sha256=DERIVED_DIGEST,
        performed_at=NOW,
        source_package_entry_id=entry.id,
    )
    session.flush()
    assert step.step_index == 1
    assert step.source_asset_sha256 == SOURCE_DIGEST


def test_a_first_step_that_contradicts_the_entrys_digest_is_refused(session, author_id):
    revision_id = _symbol_revision(session, author_id)
    _package_row, entry = _entry_with_digest(session, revision_id, SOURCE_DIGEST)
    with pytest.raises(ValueError, match="disagrees with the source package entry"):
        record_asset_transformation(
            session,
            symbol_revision_id=revision_id,
            tool_name="dxf2svg",
            tool_version="0.9.1",
            derived_asset_sha256=DERIVED_DIGEST,
            performed_at=NOW,
            source_package_entry_id=entry.id,
            source_asset_sha256=THIRD_DIGEST,
        )


def test_an_entry_from_another_revision_is_refused(session, author_id):
    """A composite foreign key would say this in the database, but it would
    need a new unique key on `source_package_entries`, and SM-P0-06 adds no
    constraint to a pre-existing table. So it is service policy, pinned
    here."""
    revision_id = _symbol_revision(session, author_id)
    other_revision_id = _symbol_revision(session, author_id)
    _package_row, entry = _entry_with_digest(session, other_revision_id, SOURCE_DIGEST)
    with pytest.raises(ValueError, match="different symbol revision"):
        record_asset_transformation(
            session,
            symbol_revision_id=revision_id,
            tool_name="dxf2svg",
            tool_version="0.9.1",
            derived_asset_sha256=DERIVED_DIGEST,
            performed_at=NOW,
            source_package_entry_id=entry.id,
        )


def test_a_second_step_continues_from_the_first_steps_output(session, author_id):
    revision_id = _symbol_revision(session, author_id)
    record_asset_transformation(
        session,
        symbol_revision_id=revision_id,
        tool_name="dxf2svg",
        tool_version="0.9.1",
        derived_asset_sha256=DERIVED_DIGEST,
        performed_at=NOW,
        source_asset_sha256=SOURCE_DIGEST,
    )
    session.flush()
    second = record_asset_transformation(
        session,
        symbol_revision_id=revision_id,
        tool_name="svgo",
        tool_version="3.0.2",
        derived_asset_sha256=THIRD_DIGEST,
        performed_at=LATER,
    )
    session.flush()
    assert second.step_index == 2
    assert second.source_asset_sha256 == DERIVED_DIGEST


def test_a_step_that_does_not_continue_the_chain_is_refused(session, author_id):
    """Section 7.12 asks for the chain. Continuity is a cross-row property, so
    no check constraint can carry it and the service must."""
    revision_id = _symbol_revision(session, author_id)
    record_asset_transformation(
        session,
        symbol_revision_id=revision_id,
        tool_name="dxf2svg",
        tool_version="0.9.1",
        derived_asset_sha256=DERIVED_DIGEST,
        performed_at=NOW,
        source_asset_sha256=SOURCE_DIGEST,
    )
    session.flush()
    with pytest.raises(ValueError, match="digest the previous step produced"):
        record_asset_transformation(
            session,
            symbol_revision_id=revision_id,
            tool_name="svgo",
            tool_version="3.0.2",
            derived_asset_sha256=THIRD_DIGEST,
            performed_at=LATER,
            source_asset_sha256="9" * 64,
        )


def test_only_the_first_step_may_name_a_package_entry(session, author_id):
    revision_id = _symbol_revision(session, author_id)
    _package_row, entry = _entry_with_digest(session, revision_id, SOURCE_DIGEST)
    record_asset_transformation(
        session,
        symbol_revision_id=revision_id,
        tool_name="dxf2svg",
        tool_version="0.9.1",
        derived_asset_sha256=DERIVED_DIGEST,
        performed_at=NOW,
        source_package_entry_id=entry.id,
    )
    session.flush()
    with pytest.raises(ValueError, match="only the first step"):
        record_asset_transformation(
            session,
            symbol_revision_id=revision_id,
            tool_name="svgo",
            tool_version="3.0.2",
            derived_asset_sha256=THIRD_DIGEST,
            performed_at=LATER,
            source_package_entry_id=entry.id,
        )


def test_a_chain_cannot_revisit_an_asset(session, author_id):
    revision_id = _symbol_revision(session, author_id)
    record_asset_transformation(
        session,
        symbol_revision_id=revision_id,
        tool_name="dxf2svg",
        tool_version="0.9.1",
        derived_asset_sha256=DERIVED_DIGEST,
        performed_at=NOW,
        source_asset_sha256=SOURCE_DIGEST,
    )
    session.flush()
    with pytest.raises(ValueError, match="cannot revisit an asset"):
        record_asset_transformation(
            session,
            symbol_revision_id=revision_id,
            tool_name="svgo",
            tool_version="3.0.2",
            derived_asset_sha256=SOURCE_DIGEST,
            performed_at=LATER,
        )


def test_the_chain_is_traced_in_order_and_reports_whether_it_reaches_a_package(
    session, author_id
):
    revision_id = _symbol_revision(session, author_id)
    _package_row, entry = _entry_with_digest(session, revision_id, SOURCE_DIGEST)
    record_asset_transformation(
        session,
        symbol_revision_id=revision_id,
        tool_name="dxf2svg",
        tool_version="0.9.1",
        derived_asset_sha256=DERIVED_DIGEST,
        performed_at=NOW,
        source_package_entry_id=entry.id,
    )
    session.flush()
    record_asset_transformation(
        session,
        symbol_revision_id=revision_id,
        tool_name="svgo",
        tool_version="3.0.2",
        derived_asset_sha256=THIRD_DIGEST,
        performed_at=LATER,
    )
    session.flush()

    chain = trace_asset_lineage(session, revision_id)
    assert [step["step_index"] for step in chain] == [1, 2]
    assert chain[0]["source_asset_sha256"] == SOURCE_DIGEST
    assert chain[0]["derived_asset_sha256"] == DERIVED_DIGEST
    assert chain[1]["source_asset_sha256"] == DERIVED_DIGEST
    assert chain[1]["derived_asset_sha256"] == THIRD_DIGEST
    assert chain[0]["tool_name"] == "dxf2svg"
    assert lineage_reaches_source_package(session, revision_id) is True


def test_a_chain_that_starts_from_a_bare_digest_does_not_reach_a_package(session, author_id):
    revision_id = _symbol_revision(session, author_id)
    record_asset_transformation(
        session,
        symbol_revision_id=revision_id,
        tool_name="dxf2svg",
        tool_version="0.9.1",
        derived_asset_sha256=DERIVED_DIGEST,
        performed_at=NOW,
        source_asset_sha256=SOURCE_DIGEST,
    )
    session.flush()
    assert lineage_reaches_source_package(session, revision_id) is False


def test_tracing_an_untransformed_revision_returns_nothing_rather_than_a_guess(
    session, author_id
):
    revision_id = _symbol_revision(session, author_id)
    assert trace_asset_lineage(session, revision_id) == []
    assert lineage_reaches_source_package(session, revision_id) is False


# --------------------------------------------------------------------------
# Read-only reporting
# --------------------------------------------------------------------------


def test_the_provenance_report_names_every_subject_that_still_needs_a_decision(
    session, author_id
):
    """Section 16.1's rights half, and the read-only wiring of
    `requires_licence_reference`. It reports; it does not gate -- the
    publication gate is SM-P0-08."""
    revision_id = _symbol_revision(session, author_id)
    package = _package(
        session, acquired_at=NOW, acquisition_method="licensed_download"
    )
    add_source_package_entry(
        session,
        source_package_id=package.id,
        symbol_revision_id=revision_id,
        added_at=NOW,
        source_path="symbols/valve.dxf",
        original_asset_sha256=SOURCE_DIGEST,
    )
    session.flush()
    version_id = _standard_version(session)
    assert_symbol_standard_link(
        session,
        symbol_revision_id=revision_id,
        standard_version_id=version_id,
        relationship_type="normative_definition",
        asserted_at=NOW,
        source_symbol_identifier="A-123",
    )
    session.flush()

    report = symbol_revision_rights_provenance(session, revision_id)
    assert report["revision_rights"] is None
    assert len(report["source_packages"]) == 1
    assert report["source_packages"][0]["requires_licence_reference"] is True
    assert report["source_packages"][0]["approved_rights"] is None
    assert len(report["standard_versions"]) == 1
    assert report["standard_versions"][0]["relationship_type"] == "normative_definition"
    subjects = {(item["subject"], item["subject_id"]) for item in report["unresolved_subjects"]}
    assert subjects == {
        ("source_package", package.id),
        ("standard_version", version_id),
        ("symbol_revision", revision_id),
    }
    assert report["lineage_steps"] == []
    assert report["lineage_reaches_source_package"] is False


def test_the_provenance_report_resolves_an_approved_decision(session, author_id):
    revision_id = _symbol_revision(session, author_id)
    package = _package(session, acquired_at=NOW, acquisition_method="public_download")
    entry = add_source_package_entry(
        session,
        source_package_id=package.id,
        symbol_revision_id=revision_id,
        added_at=NOW,
        source_path="symbols/valve.dxf",
        original_asset_sha256=SOURCE_DIGEST,
    )
    session.flush()
    record = propose_rights_record(
        session,
        source_package_id=package.id,
        disposition="distribute",
        determination_method="licence_document",
        proposed_at=NOW,
        rights_status="open",
    )
    session.flush()
    transition_rights_record(
        session,
        record.id,
        target_status="approved",
        occurred_at=LATER,
        decided_by_user_id=author_id,
        decision_reason="published under CC BY 4.0",
    )
    session.flush()
    record_asset_transformation(
        session,
        symbol_revision_id=revision_id,
        tool_name="dxf2svg",
        tool_version="0.9.1",
        derived_asset_sha256=DERIVED_DIGEST,
        performed_at=LATER,
        source_package_entry_id=entry.id,
    )
    session.flush()

    report = symbol_revision_rights_provenance(session, revision_id)
    package_report = report["source_packages"][0]
    assert package_report["requires_licence_reference"] is False
    assert package_report["approved_rights"]["disposition"] == "distribute"
    assert package_report["approved_rights"]["rights_status"] == "open"
    assert package_report["approved_rights"]["decided_by_user_id"] == author_id
    assert package_report["approved_rights"]["decision_reason"] == "published under CC BY 4.0"
    assert report["lineage_reaches_source_package"] is True
    assert [step["step_index"] for step in report["lineage_steps"]] == [1]
    # The revision itself still has no decision of its own, and no precedence
    # between the three subjects is invented here -- that is SM-P0-08's call.
    assert report["revision_rights"] is None
    assert {item["subject"] for item in report["unresolved_subjects"]} == {"symbol_revision"}


# --------------------------------------------------------------------------
# Reversibility
# --------------------------------------------------------------------------


def test_downgrade_removes_the_two_new_tables_and_nothing_else():
    """The strongest available proof that the migration is purely additive:
    take the complete column inventory at 20260910_0056, downgrade, and show
    the only difference is the two new tables."""
    with _database("symgov-rights-downgrade") as (engine, url, _raw):
        _alembic(url, "upgrade", RIGHTS_REVISION)
        with engine.begin() as connection:
            after = set(
                connection.execute(
                    text(
                        "SELECT table_name, column_name FROM information_schema.columns "
                        "WHERE table_schema='public'"
                    )
                ).all()
            )
        _alembic(url, "downgrade", PREVIOUS_REVISION)
        with engine.begin() as connection:
            before = set(
                connection.execute(
                    text(
                        "SELECT table_name, column_name FROM information_schema.columns "
                        "WHERE table_schema='public'"
                    )
                ).all()
            )
            remaining_indexes = set(
                connection.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE schemaname='public' "
                        "AND (indexname LIKE '%rights_records%' "
                        "OR indexname LIKE '%asset_transformations%')"
                    )
                ).scalars()
            )
            head = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    assert before < after
    assert {table for table, _column in after - before} == set(NEW_TABLES)
    assert (after - before) == {
        (table, column)
        for table, columns in (
            ("rights_records", RIGHTS_COLUMNS),
            ("asset_transformations", TRANSFORMATION_COLUMNS),
        )
        for column in columns
    }
    assert remaining_indexes == set()
    assert head == PREVIOUS_REVISION


def test_a_downgrade_and_re_upgrade_lands_in_the_same_place():
    with _database("symgov-rights-cycle") as (engine, url, _raw):
        _alembic(url, "upgrade", RIGHTS_REVISION)
        _alembic(url, "downgrade", PREVIOUS_REVISION)
        _alembic(url, "upgrade", RIGHTS_REVISION)
        with engine.begin() as connection:
            columns = set(
                connection.execute(
                    text(
                        "SELECT table_name, column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name = ANY(:names)"
                    ),
                    {"names": list(NEW_TABLES)},
                ).all()
            )
            constraints = set(
                connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                        "WHERE t.relname = ANY(:names) AND c.contype = 'c'"
                    ),
                    {"names": list(NEW_TABLES)},
                ).scalars()
            )
    assert {table for table, _column in columns} == set(NEW_TABLES)
    assert len(constraints) == 20, sorted(constraints)
    assert all(len(name) <= 63 for name in constraints)
