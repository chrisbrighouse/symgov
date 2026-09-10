"""Migration and service rehearsal for SM-P0-05 against a real PostgreSQL server.

Why this file exists: almost everything that makes SM-P0-05 worth having is
storage-level, and none of it can be proven by reading the migration text.

* `symbol_standard_links`'s original unique index includes the nullable
  `clause_reference`, and PostgreSQL treats NULLs as distinct -- so duplicate
  assertions insert freely today. Only a real server shows both that the hole
  was real and that the replacement closes it.
* PostgreSQL accepts a check constraint that evaluates to NULL. Every branch
  of the eight optional-pair constraints here has to be shown *rejecting* a
  row rather than merely being present in the DDL.
* `source_packages` already holds production rows written by the
  submission-intake path. That the migration applies to a table that is not
  empty, leaving those rows intact, is rehearsed by upgrading to 20260909_0053
  first, writing an intake-shaped row, and only then upgrading.
* 20260409_0001's own downgrade drops the replaced index by name, so this
  file cycles a database through upgrade, downgrade and upgrade to prove the
  restore.

Redaction: this file never prints the disposable container's connection string
and every seeded identity uses a synthetic `@example.test` email.
"""

from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

from symgov_backend.models import (  # noqa: E402
    SourcePackage,
    SourcePackageEntry,
    SymbolStandardLink,
)
from symgov_backend.source_package_acquisition import (  # noqa: E402
    add_source_package_entry,
    find_entries_by_provider_identifier,
    get_source_package,
    list_source_package_entries,
    record_package_acquisition,
    register_source_package,
    trace_symbol_revision_sources,
)
from symgov_backend.standard_sources import (  # noqa: E402
    SOURCE_RELATIONSHIP_TYPES,
    assert_symbol_standard_link,
    find_links_by_source_symbol_identifier,
    get_standard,
    list_standard_versions,
    list_symbol_standard_links,
    register_standard,
    register_standard_version,
    set_standard_status,
    set_standard_version_status,
    transition_symbol_standard_link,
    verified_normative_definition,
)

PRECISION_REVISION = "20260910_0054"
PREVIOUS_REVISION = "20260909_0053"

NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)

DIGEST = "b" * 64
OTHER_DIGEST = "c" * 64

PRECISION_TABLES = ("symbol_standard_links", "source_packages", "source_package_entries")
PRECISION_MODELS = (SymbolStandardLink, SourcePackage, SourcePackageEntry)

LEGACY_LINK_INDEX = "uq_symbol_standard_links_revision_standard_relationship_clause"

# Every column SM-P0-05 adds, by table. Each must be nullable or carry a
# server default, because `source_packages` is not an empty table in
# production.
ADDED_COLUMNS = {
    "symbol_standard_links": (
        "source_symbol_identifier",
        "figure_reference",
        "table_reference",
        "source_uri",
        "assertion_status",
        "verification_method",
        "source_asset_sha256",
        "verified_by_user_id",
        "verified_at",
        "evidence_json",
    ),
    "source_packages": (
        "provider_package_identifier",
        "source_uri",
        "release_version",
        "release_date",
        "acquired_at",
        "acquisition_method",
        "licence_reference",
        "package_sha256",
        "ingestion_profile",
        "metadata_json",
    ),
    "source_package_entries": (
        "provider_entry_identifier",
        "source_path",
        "original_asset_sha256",
    ),
}

# The columns 20260409_0001 created, which must survive both directions.
ORIGINAL_COLUMNS = {
    "symbol_standard_links": (
        "id",
        "symbol_revision_id",
        "standard_version_id",
        "relationship_type",
        "clause_reference",
        "notes",
        "created_at",
    ),
    "source_packages": (
        "id",
        "package_code",
        "title",
        "provider",
        "package_type",
        "status",
        "created_at",
        "updated_at",
    ),
    "source_package_entries": (
        "id",
        "source_package_id",
        "symbol_revision_id",
        "sort_order",
        "source_label",
        "created_at",
    ),
}


@pytest.fixture(scope="module")
def precision_database():
    with _database("symgov-source-precision") as (engine, url, raw_url):
        _alembic(url, "upgrade", PRECISION_REVISION)
        yield engine, url


@pytest.fixture(scope="module")
def author_id(precision_database) -> uuid.UUID:
    engine, _ = precision_database
    identifier = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                "must_change_pin,is_active,created_at,updated_at) "
                "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
            ),
            {"id": identifier, "email": "source-precision-author@example.test", "now": NOW},
        )
    return identifier


@pytest.fixture()
def session(precision_database) -> Session:
    engine, _ = precision_database
    with Session(engine) as active_session:
        yield active_session
        active_session.rollback()


def _symbol_revision(session: Session, author: uuid.UUID) -> uuid.UUID:
    """Seed a draft governed symbol revision.

    Deliberately draft: 20260826_0031's publication invariant would otherwise
    demand a canonical catalog identifier, which is irrelevant here.
    """
    symbol_id, revision_id = uuid.uuid4(), uuid.uuid4()
    slug = f"source-precision-symbol-{uuid.uuid4().hex[:10]}"
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


def _standard_version(session: Session, *, label: str = "2012") -> uuid.UUID:
    standard = register_standard(
        session,
        standard_code=f"TEST-{uuid.uuid4().hex[:8].upper()}",
        title="Illustrative test standard",
        issuing_body="Illustrative issuing body",
        registered_at=NOW,
    )
    session.flush()
    version = register_standard_version(
        session, standard_id=standard.id, version_label=label, registered_at=NOW
    )
    session.flush()
    return version.id


def _package(session: Session, **kwargs) -> SourcePackage:
    package = register_source_package(
        session,
        package_code=f"TESTPKG-{uuid.uuid4().hex[:8].upper()}",
        title="Illustrative test package",
        registered_at=NOW,
        **kwargs,
    )
    session.flush()
    return package


def _raw_link(session: Session, revision_id: uuid.UUID, version_id: uuid.UUID, **columns) -> None:
    """Insert a link row with raw SQL, bypassing the service layer.

    Every constraint test here goes through raw SQL on purpose: the point is
    what the *database* refuses, not what the validators refuse first.
    """
    values = {
        "id": uuid.uuid4(),
        "symbol_revision_id": revision_id,
        "standard_version_id": version_id,
        "relationship_type": "derived_from",
        "created_at": NOW,
        **columns,
    }
    names = ", ".join(values)
    placeholders = ", ".join(f":{name}" for name in values)
    session.execute(text(f"INSERT INTO symbol_standard_links ({names}) VALUES ({placeholders})"), values)
    session.flush()


# --------------------------------------------------------------------------
# Migration shape
# --------------------------------------------------------------------------


@pytest.mark.parametrize("table", PRECISION_TABLES)
def test_upgrade_added_every_specified_column(precision_database, table):
    engine, _ = precision_database
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
    assert set(ADDED_COLUMNS[table]) <= present
    assert set(ORIGINAL_COLUMNS[table]) <= present


@pytest.mark.parametrize("table", PRECISION_TABLES)
def test_every_added_column_is_nullable_or_server_defaulted(precision_database, table):
    """`source_packages` is not empty in production. A bare NOT NULL column
    would have failed the migration outright there."""
    engine, _ = precision_database
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT column_name, is_nullable, column_default FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:table AND column_name = ANY(:names)"
            ),
            {"table": table, "names": list(ADDED_COLUMNS[table])},
        ).all()
    assert len(rows) == len(ADDED_COLUMNS[table])
    for row in rows:
        assert row.is_nullable == "YES" or row.column_default is not None, row.column_name


def test_the_two_not_null_additions_carry_the_defaults_they_claim(precision_database):
    engine, _ = precision_database
    with engine.begin() as connection:
        rows = dict(
            connection.execute(
                text(
                    "SELECT column_name, column_default FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='symbol_standard_links' "
                    "AND column_name IN ('assertion_status','evidence_json')"
                )
            ).all()
        )
    assert rows["assertion_status"].startswith("'proposed'")
    assert rows["evidence_json"].startswith("'{}'")


def test_the_section_14_3_index_exists(precision_database):
    """Section 14.3: "Index extended SymbolStandardLink by standard_version_id
    + source_symbol_identifier"."""
    engine, _ = precision_database
    with engine.begin() as connection:
        definition = connection.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
                "AND indexname='ix_symbol_standard_links_version_source_symbol_identifier'"
            )
        ).scalar_one()
    assert "standard_version_id" in definition
    assert "source_symbol_identifier" in definition


def test_the_toothless_index_is_gone_and_the_replacement_is_partial(precision_database):
    engine, _ = precision_database
    with engine.begin() as connection:
        indexes = dict(
            connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='public' "
                    "AND tablename='symbol_standard_links'"
                )
            ).all()
        )
    assert LEGACY_LINK_INDEX not in indexes
    active = indexes["uq_symbol_standard_links_active_assertion"]
    assert "UNIQUE" in active
    assert "COALESCE(clause_reference" in active
    assert "WHERE (assertion_status" in active
    definition = indexes["uq_symbol_standard_links_verified_definition"]
    assert "UNIQUE" in definition
    assert "normative_definition" in definition


def test_every_constraint_name_survived_the_63_character_limit(precision_database):
    """A name PostgreSQL had to truncate comes back shortened or
    hash-suffixed; a name passed already-prefixed comes back doubled. Both
    failure modes show up here."""
    engine, _ = precision_database
    with engine.begin() as connection:
        names = set(
            connection.execute(
                text(
                    "SELECT conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                    "WHERE t.relname = ANY(:names)"
                ),
                {"names": list(PRECISION_TABLES)},
            ).scalars()
        )
        index_names = set(
            connection.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE schemaname='public' "
                    "AND tablename = ANY(:names)"
                ),
                {"names": list(PRECISION_TABLES)},
            ).scalars()
        )
    for expected in (
        "ck_symbol_standard_links_relationship_type",
        "ck_symbol_standard_links_verified_decision",
        "ck_symbol_standard_links_source_asset_provenance",
        "ck_source_packages_acquisition_pairing",
        "ck_source_packages_package_integrity_context",
        "ck_source_package_entries_original_asset_context",
        "fk_symbol_standard_links_verified_by_user_id",
    ):
        assert expected in names, sorted(names)
    assert all(len(name) <= 63 for name in names | index_names)
    for table in PRECISION_TABLES:
        assert not any(name.startswith(f"ck_{table}_ck_") for name in names)


def test_these_tables_are_not_recorded_as_pre_existing_name_drift():
    """20260909_0053 emptied that allowlist and it must stay empty."""
    from test_semantic_constraint_name_repair_postgresql import PRE_EXISTING_NAME_DRIFT

    assert PRE_EXISTING_NAME_DRIFT == frozenset()
    assert PRE_EXISTING_NAME_DRIFT.isdisjoint(set(PRECISION_TABLES))


@pytest.mark.parametrize("model", PRECISION_MODELS, ids=lambda m: m.__tablename__)
def test_the_extended_tables_carry_exactly_the_orm_constraint_names(precision_database, model):
    """SM-P0-03's parity guard, applied to these three tables at the revision
    that extends them. The guard itself runs at 20260909_0053, where these
    tables carry no check constraint at all and are therefore skipped."""
    from test_semantic_constraint_name_repair_postgresql import (
        _database_names,
        _expected_names,
    )

    engine, _ = precision_database
    with engine.begin() as connection:
        actual = _database_names(connection).get(model.__tablename__, set())
    assert actual == _expected_names(model.__table__)


def test_the_two_preserved_entities_gained_nothing(precision_database):
    """Section 7.10 preserves Standard and StandardVersion and extends
    neither. Their status vocabularies are service policy, deliberately not
    database constraints -- the specification names none."""
    engine, _ = precision_database
    with engine.begin() as connection:
        columns = dict(
            connection.execute(
                text(
                    "SELECT table_name, count(*) FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name IN ('standards','standard_versions') "
                    "GROUP BY table_name"
                )
            ).all()
        )
        checks = connection.execute(
            text(
                "SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE t.relname IN ('standards','standard_versions') AND c.contype='c'"
            )
        ).scalar_one()
    assert columns == {"standards": 7, "standard_versions": 7}
    assert checks == 0


# --------------------------------------------------------------------------
# Applying to a table that already holds rows
# --------------------------------------------------------------------------


def test_the_migration_applies_to_a_source_packages_table_that_is_not_empty():
    """`runtime.ensure_source_package_for_intake` has been writing these rows
    since 20260409_0001. The migration has to land on top of them, leave them
    exactly as they were, and default the columns it adds."""
    with _database("symgov-source-precision-rows") as (engine, url, _raw):
        _alembic(url, "upgrade", PREVIOUS_REVISION)
        package_id = uuid.uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO source_packages (id,package_code,title,provider,package_type,"
                    "status,created_at,updated_at) "
                    "VALUES (:id,'A1B2','Submitted sheet.xlsx','a submitter',"
                    "'submission_sheet','active',:now,:now)"
                ),
                {"id": package_id, "now": NOW},
            )
        _alembic(url, "upgrade", PRECISION_REVISION)
        with engine.begin() as connection:
            row = connection.execute(
                text("SELECT * FROM source_packages WHERE id=:id"), {"id": package_id}
            ).one()
        # The intake path's own values are untouched...
        assert row.package_code == "A1B2"
        assert row.package_type == "submission_sheet"
        assert row.status == "active"
        assert row.provider == "a submitter"
        # ...and every added column is unset rather than invented.
        assert row.provider_package_identifier is None
        assert row.acquisition_method is None
        assert row.acquired_at is None
        assert row.package_sha256 is None
        assert row.licence_reference is None
        assert row.metadata_json == {}


# --------------------------------------------------------------------------
# Check constraints: what the database actually refuses
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relationship_type", sorted(SOURCE_RELATIONSHIP_TYPES))
def test_all_eight_section_8_3_relationship_types_are_accepted(session, author_id, relationship_type):
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    _raw_link(session, revision_id, version_id, relationship_type=relationship_type)


@pytest.mark.parametrize(
    "relationship_type", ["standard_associated", "normative", "NORMATIVE_DEFINITION", ""]
)
def test_a_ninth_relationship_type_is_refused(session, author_id, relationship_type):
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    with pytest.raises(IntegrityError):
        _raw_link(session, revision_id, version_id, relationship_type=relationship_type)


def test_a_verified_assertion_cannot_exist_without_a_verification_time(session, author_id):
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    with pytest.raises(IntegrityError):
        _raw_link(
            session,
            revision_id,
            version_id,
            assertion_status="verified",
            verification_method="manual",
        )


def test_a_verified_assertion_cannot_exist_without_a_verification_method(session, author_id):
    """The NULL branch that PostgreSQL would otherwise accept: with
    `verification_method` NULL the naive spelling evaluates to NULL, and a
    check constraint that evaluates to NULL passes."""
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    with pytest.raises(IntegrityError):
        _raw_link(session, revision_id, version_id, assertion_status="verified", verified_at=NOW)


def test_a_verified_assertion_with_both_is_accepted(session, author_id):
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    _raw_link(
        session,
        revision_id,
        version_id,
        assertion_status="verified",
        verified_at=NOW,
        verification_method="import_manifest",
    )


@pytest.mark.parametrize("status", ["approved", "pending", "PROPOSED", ""])
def test_an_invented_assertion_status_is_refused(session, author_id, status):
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    with pytest.raises(IntegrityError):
        _raw_link(session, revision_id, version_id, assertion_status=status)


@pytest.mark.parametrize("method", ["imported", "source_mapping", "legacy_backfill", "MANUAL"])
def test_a_method_from_another_vocabulary_is_refused(session, author_id, method):
    """The four `method` vocabularies are deliberately not unified. A value
    from one must not be storable in another."""
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    with pytest.raises(IntegrityError):
        _raw_link(
            session,
            revision_id,
            version_id,
            assertion_status="verified",
            verified_at=NOW,
            verification_method=method,
        )


@pytest.mark.parametrize(
    "digest",
    ["a" * 63, "a" * 65, "g" * 64, "A" * 64, "d41d8cd98f00b204e9800998ecf8427e", ""],
)
def test_a_source_asset_hash_that_is_not_sha256_is_refused(session, author_id, digest):
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    with pytest.raises(IntegrityError):
        _raw_link(
            session,
            revision_id,
            version_id,
            source_asset_sha256=digest,
            source_uri="https://example.test/a",
        )


def test_a_source_asset_hash_without_a_locator_is_refused(session, author_id):
    """A hash of an artifact with no record of where the artifact came from
    cannot be re-verified, which is the only reason to store it."""
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    with pytest.raises(IntegrityError):
        _raw_link(session, revision_id, version_id, source_asset_sha256=DIGEST)


def test_a_source_asset_hash_with_a_locator_is_accepted(session, author_id):
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    _raw_link(
        session,
        revision_id,
        version_id,
        source_asset_sha256=DIGEST,
        source_uri="https://example.test/a",
    )


@pytest.mark.parametrize("uri", ["ftp://example.test/a", "example.test/a", "s3://bucket/key"])
def test_a_locator_that_is_not_http_is_refused(session, author_id, uri):
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    with pytest.raises(IntegrityError):
        _raw_link(session, revision_id, version_id, source_uri=uri)


@pytest.mark.parametrize(
    "column", ["clause_reference", "source_symbol_identifier", "figure_reference", "table_reference"]
)
def test_a_blank_reference_is_refused(session, author_id, column):
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    with pytest.raises(IntegrityError):
        _raw_link(session, revision_id, version_id, **{column: "   "})


def test_evidence_must_be_a_json_object(session, author_id):
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    with pytest.raises(IntegrityError):
        _raw_link(session, revision_id, version_id, evidence_json='["a"]')


def test_an_acquisition_time_without_a_method_is_refused(session):
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO source_packages (id,package_code,title,package_type,status,"
                "created_at,updated_at,acquired_at) "
                "VALUES (:id,:code,'t','authoritative_library','active',:now,:now,:now)"
            ),
            {"id": uuid.uuid4(), "code": uuid.uuid4().hex[:12].upper(), "now": NOW},
        )
        session.flush()


def test_an_acquisition_method_without_a_time_is_refused(session):
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO source_packages (id,package_code,title,package_type,status,"
                "created_at,updated_at,acquisition_method) "
                "VALUES (:id,:code,'t','authoritative_library','active',:now,:now,'public_download')"
            ),
            {"id": uuid.uuid4(), "code": uuid.uuid4().hex[:12].upper(), "now": NOW},
        )
        session.flush()


def test_a_package_hash_without_an_acquisition_is_refused(session):
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO source_packages (id,package_code,title,package_type,status,"
                "created_at,updated_at,package_sha256) "
                "VALUES (:id,:code,'t','authoritative_library','active',:now,:now,:digest)"
            ),
            {"id": uuid.uuid4(), "code": uuid.uuid4().hex[:12].upper(), "now": NOW, "digest": DIGEST},
        )
        session.flush()


@pytest.mark.parametrize("method", ["manual", "rule", "imported", "PUBLIC_DOWNLOAD"])
def test_an_acquisition_method_from_another_vocabulary_is_refused(session, method):
    """Section 7.11's six values overlap none of the other four vocabularies,
    and the database enforces that boundary."""
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO source_packages (id,package_code,title,package_type,status,"
                "created_at,updated_at,acquired_at,acquisition_method) "
                "VALUES (:id,:code,'t','authoritative_library','active',:now,:now,:now,:method)"
            ),
            {
                "id": uuid.uuid4(),
                "code": uuid.uuid4().hex[:12].upper(),
                "now": NOW,
                "method": method,
            },
        )
        session.flush()


def test_an_original_asset_hash_that_names_no_asset_is_refused(session, author_id):
    revision_id = _symbol_revision(session, author_id)
    package = _package(session)
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO source_package_entries (id,source_package_id,symbol_revision_id,"
                "created_at,original_asset_sha256) VALUES (:id,:package,:revision,:now,:digest)"
            ),
            {
                "id": uuid.uuid4(),
                "package": package.id,
                "revision": revision_id,
                "now": NOW,
                "digest": DIGEST,
            },
        )
        session.flush()


@pytest.mark.parametrize("naming_column", ["source_path", "provider_entry_identifier"])
def test_an_original_asset_hash_named_by_either_column_is_accepted(session, author_id, naming_column):
    revision_id = _symbol_revision(session, author_id)
    package = _package(session)
    session.execute(
        text(
            f"INSERT INTO source_package_entries (id,source_package_id,symbol_revision_id,"
            f"created_at,original_asset_sha256,{naming_column}) "
            f"VALUES (:id,:package,:revision,:now,:digest,'symbols/a-123.dwg')"
        ),
        {
            "id": uuid.uuid4(),
            "package": package.id,
            "revision": revision_id,
            "now": NOW,
            "digest": DIGEST,
        },
    )
    session.flush()


def test_an_entry_hash_that_is_not_sha256_is_refused(session, author_id):
    revision_id = _symbol_revision(session, author_id)
    package = _package(session)
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO source_package_entries (id,source_package_id,symbol_revision_id,"
                "created_at,original_asset_sha256,source_path) "
                "VALUES (:id,:package,:revision,:now,'not-a-digest','symbols/a.dwg')"
            ),
            {"id": uuid.uuid4(), "package": package.id, "revision": revision_id, "now": NOW},
        )
        session.flush()


# --------------------------------------------------------------------------
# The duplicate-assertion hole (trap 2)
# --------------------------------------------------------------------------


def test_two_assertions_with_a_null_clause_can_no_longer_both_insert(session, author_id):
    """The defect this package closes. Under 20260409_0001's index both rows
    inserted freely, because PostgreSQL treats NULLs as distinct and
    `clause_reference` is nullable."""
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    _raw_link(session, revision_id, version_id, relationship_type="normative_definition")
    with pytest.raises(IntegrityError):
        _raw_link(session, revision_id, version_id, relationship_type="normative_definition")


def test_the_defect_was_real_at_the_previous_revision():
    """A regression guard on the premise, not on the fix: if the original
    index had enforced what its name claims, replacing it would have been
    unnecessary churn against a deployed object."""
    with _database("symgov-source-precision-hole") as (engine, url, _raw):
        _alembic(url, "upgrade", PREVIOUS_REVISION)
        with engine.begin() as connection:
            author = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                    "must_change_pin,is_active,created_at,updated_at) "
                    "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
                ),
                {"id": author, "email": "hole-probe@example.test", "now": NOW},
            )
            symbol_id, revision_id = uuid.uuid4(), uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,"
                    "owner_id,created_at,updated_at) "
                    "VALUES (:id,'hole-probe','hole-probe','Valves','Piping / P&ID',:owner,:now,:now)"
                ),
                {"id": symbol_id, "owner": author, "now": NOW},
            )
            connection.execute(
                text(
                    "INSERT INTO symbol_revisions (id,symbol_id,revision_label,lifecycle_state,"
                    "payload_json,author_id,created_at) "
                    "VALUES (:id,:symbol,'1','draft','{}'::jsonb,:owner,:now)"
                ),
                {"id": revision_id, "symbol": symbol_id, "owner": author, "now": NOW},
            )
            standard_id, version_id = uuid.uuid4(), uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO standards (id,standard_code,title,status,created_at,updated_at) "
                    "VALUES (:id,'HOLE-PROBE','Hole probe','active',:now,:now)"
                ),
                {"id": standard_id, "now": NOW},
            )
            connection.execute(
                text(
                    "INSERT INTO standard_versions (id,standard_id,version_label,status,"
                    "created_at,updated_at) VALUES (:id,:standard,'1','active',:now,:now)"
                ),
                {"id": version_id, "standard": standard_id, "now": NOW},
            )
            for _ in range(2):
                connection.execute(
                    text(
                        "INSERT INTO symbol_standard_links (id,symbol_revision_id,"
                        "standard_version_id,relationship_type,created_at) "
                        "VALUES (:id,:revision,:version,'normative_definition',:now)"
                    ),
                    {"id": uuid.uuid4(), "revision": revision_id, "version": version_id, "now": NOW},
                )
            duplicates = connection.execute(
                text("SELECT count(*) FROM symbol_standard_links")
            ).scalar_one()
        assert duplicates == 2, (
            "the nullable-clause unique index turned out to enforce uniqueness after all"
        )


def test_a_rejected_assertion_does_not_block_re_proposing_the_same_one(session, author_id):
    """Why the replacement index is partial. Without the predicate a rejected
    assertion would occupy the key permanently."""
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    first = assert_symbol_standard_link(
        session,
        symbol_revision_id=revision_id,
        standard_version_id=version_id,
        relationship_type="normative_definition",
        source_symbol_identifier="A-123",
        asserted_at=NOW,
    )
    session.flush()
    transition_symbol_standard_link(session, first.id, target_status="rejected", occurred_at=NOW)
    session.flush()
    assert_symbol_standard_link(
        session,
        symbol_revision_id=revision_id,
        standard_version_id=version_id,
        relationship_type="normative_definition",
        source_symbol_identifier="A-124",
        asserted_at=NOW,
    )
    session.flush()


def test_two_different_clauses_are_still_two_assertions(session, author_id):
    """COALESCE collapses NULL onto the empty string, and the empty string is
    forbidden -- so distinct clauses stay distinct."""
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    _raw_link(session, revision_id, version_id, clause_reference="6.2")
    _raw_link(session, revision_id, version_id, clause_reference="6.3")
    _raw_link(session, revision_id, version_id)


def test_a_null_clause_and_an_empty_clause_cannot_collide(session, author_id):
    """The collapse would be ambiguous if `''` were storable. It is not."""
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    _raw_link(session, revision_id, version_id)
    with pytest.raises(IntegrityError):
        _raw_link(session, revision_id, version_id, clause_reference="")


# --------------------------------------------------------------------------
# Service layer
# --------------------------------------------------------------------------


def test_a_standard_and_its_editions_round_trip(session):
    standard = register_standard(
        session,
        standard_code="api spec 6d",
        title="Specification for Valves",
        issuing_body="API",
        registered_at=NOW,
    )
    session.flush()
    assert standard.standard_code == "API SPEC 6D"
    assert get_standard(session, "API Spec 6D").id == standard.id

    register_standard_version(
        session,
        standard_id=standard.id,
        version_label="2014",
        effective_date=date(2014, 8, 1),
        registered_at=NOW,
    )
    register_standard_version(
        session,
        standard_id=standard.id,
        version_label="2021",
        effective_date=date(2021, 4, 1),
        registered_at=NOW,
    )
    session.flush()
    labels = [version.version_label for version in list_standard_versions(session, standard.id)]
    assert labels == ["2021", "2014"], "newest effective date first"


def test_a_withdrawn_standard_accepts_no_new_editions(session):
    standard = register_standard(
        session, standard_code="ISO 10628-2", title="Diagrams", registered_at=NOW
    )
    session.flush()
    set_standard_status(session, standard.id, target_status="withdrawn", occurred_at=NOW)
    session.flush()
    with pytest.raises(ValueError, match="withdrawn"):
        register_standard_version(
            session, standard_id=standard.id, version_label="2012", registered_at=NOW
        )


def test_a_withdrawn_edition_accepts_no_new_assertions(session, author_id):
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    set_standard_version_status(session, version_id, target_status="withdrawn", occurred_at=NOW)
    session.flush()
    with pytest.raises(ValueError, match="withdrawn"):
        assert_symbol_standard_link(
            session,
            symbol_revision_id=revision_id,
            standard_version_id=version_id,
            relationship_type="derived_from",
            asserted_at=NOW,
        )


def test_an_assertion_starts_proposed_whatever_produced_it(session, author_id):
    """Principle P-07: nothing reaches the public record verified by default."""
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    link = assert_symbol_standard_link(
        session,
        symbol_revision_id=revision_id,
        standard_version_id=version_id,
        relationship_type="normative_definition",
        source_symbol_identifier="A-123",
        figure_reference="Figure 6.2",
        table_reference="Table 4",
        clause_reference="6.2",
        source_uri="https://example.test/spec",
        source_asset_sha256=DIGEST,
        evidence={"observed_in": "provider manifest"},
        asserted_at=NOW,
    )
    session.flush()
    assert link.assertion_status == "proposed"
    assert link.verification_method is None
    assert link.verified_at is None
    assert link.verified_by_user_id is None
    assert link.source_asset_sha256 == DIGEST


def test_verifying_requires_a_method_and_records_who_and_when(session, author_id):
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    link = assert_symbol_standard_link(
        session,
        symbol_revision_id=revision_id,
        standard_version_id=version_id,
        relationship_type="normative_definition",
        source_symbol_identifier="A-123",
        asserted_at=NOW,
    )
    session.flush()
    with pytest.raises(ValueError, match="verification method"):
        transition_symbol_standard_link(session, link.id, target_status="verified", occurred_at=NOW)

    transition_symbol_standard_link(
        session,
        link.id,
        target_status="verified",
        occurred_at=NOW,
        verification_method="manual",
        verified_by_user_id=author_id,
    )
    session.flush()
    assert link.assertion_status == "verified"
    assert link.verified_by_user_id == author_id
    assert link.verified_at == NOW


def test_an_ai_assisted_assertion_cannot_verify_itself(session, author_id):
    """Section 8.4. A deterministic import may, on an authoritative artifact;
    a model's opinion may not, however confident."""
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    link = assert_symbol_standard_link(
        session,
        symbol_revision_id=revision_id,
        standard_version_id=version_id,
        relationship_type="normative_equivalent",
        source_symbol_identifier="A-200",
        asserted_at=NOW,
    )
    session.flush()
    with pytest.raises(ValueError, match="requires a named verifier"):
        transition_symbol_standard_link(
            session,
            link.id,
            target_status="verified",
            occurred_at=NOW,
            verification_method="ai_assisted",
        )
    transition_symbol_standard_link(
        session,
        link.id,
        target_status="verified",
        occurred_at=NOW,
        verification_method="import_manifest",
    )
    session.flush()
    assert link.verified_by_user_id is None


def test_a_normative_assertion_needs_somewhere_precise_to_point(session, author_id):
    """Section 9.2 rules out the ambiguous "standard-associated" label, and
    section 7.10 exists to make the precise alternative representable. Service
    policy, not a check constraint -- the columns are individually optional
    because a `comparison_only` link legitimately has none of them."""
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    vague = assert_symbol_standard_link(
        session,
        symbol_revision_id=revision_id,
        standard_version_id=version_id,
        relationship_type="normative_definition",
        asserted_at=NOW,
    )
    session.flush()
    with pytest.raises(ValueError, match="symbol identifier, figure, table or clause"):
        transition_symbol_standard_link(
            session,
            vague.id,
            target_status="verified",
            occurred_at=NOW,
            verification_method="manual",
            verified_by_user_id=author_id,
        )

    loose = assert_symbol_standard_link(
        session,
        symbol_revision_id=revision_id,
        standard_version_id=version_id,
        relationship_type="comparison_only",
        asserted_at=NOW,
    )
    session.flush()
    transition_symbol_standard_link(
        session,
        loose.id,
        target_status="verified",
        occurred_at=NOW,
        verification_method="manual",
        verified_by_user_id=author_id,
    )
    session.flush()
    assert loose.assertion_status == "verified"


def test_verifying_a_successor_retires_the_previous_definition(session, author_id):
    """Supersession rather than refusal, the pattern SM-P0-02 through -04 all
    use. Two clauses of one edition cannot both formally define the symbol."""
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    first = assert_symbol_standard_link(
        session,
        symbol_revision_id=revision_id,
        standard_version_id=version_id,
        relationship_type="normative_definition",
        clause_reference="6.2",
        source_symbol_identifier="A-123",
        asserted_at=NOW,
    )
    second = assert_symbol_standard_link(
        session,
        symbol_revision_id=revision_id,
        standard_version_id=version_id,
        relationship_type="normative_definition",
        clause_reference="7.4",
        source_symbol_identifier="A-124",
        asserted_at=NOW,
    )
    session.flush()
    for link in (first, second):
        assert link.assertion_status == "proposed"

    transition_symbol_standard_link(
        session,
        first.id,
        target_status="verified",
        occurred_at=NOW,
        verification_method="manual",
        verified_by_user_id=author_id,
    )
    session.flush()
    assert verified_normative_definition(session, revision_id, version_id).id == first.id

    transition_symbol_standard_link(
        session,
        second.id,
        target_status="verified",
        occurred_at=NOW,
        verification_method="manual",
        verified_by_user_id=author_id,
    )
    session.flush()
    assert first.assertion_status == "retired"
    assert second.assertion_status == "verified"
    assert verified_normative_definition(session, revision_id, version_id).id == second.id


def test_a_rejected_assertion_is_terminal(session, author_id):
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    link = assert_symbol_standard_link(
        session,
        symbol_revision_id=revision_id,
        standard_version_id=version_id,
        relationship_type="derived_from",
        asserted_at=NOW,
    )
    session.flush()
    transition_symbol_standard_link(session, link.id, target_status="rejected", occurred_at=NOW)
    session.flush()
    with pytest.raises(ValueError, match="cannot move from rejected"):
        transition_symbol_standard_link(
            session,
            link.id,
            target_status="verified",
            occurred_at=NOW,
            verification_method="manual",
            verified_by_user_id=author_id,
        )


def test_the_section_14_3_reverse_lookup_finds_every_revision(session, author_id):
    version_id = _standard_version(session)
    first = _symbol_revision(session, author_id)
    second = _symbol_revision(session, author_id)
    for revision_id in (first, second):
        assert_symbol_standard_link(
            session,
            symbol_revision_id=revision_id,
            standard_version_id=version_id,
            relationship_type="normative_definition",
            source_symbol_identifier="A-123",
            asserted_at=NOW,
        )
    session.flush()
    found = find_links_by_source_symbol_identifier(session, version_id, "A-123")
    assert {link.symbol_revision_id for link in found} == {first, second}
    assert find_links_by_source_symbol_identifier(session, version_id, "A-999") == []


def test_listing_a_revisions_assertions_puts_the_normative_pair_first(session, author_id):
    revision_id = _symbol_revision(session, author_id)
    version_id = _standard_version(session)
    for relationship_type in ("comparison_only", "normative_definition", "informative_example"):
        assert_symbol_standard_link(
            session,
            symbol_revision_id=revision_id,
            standard_version_id=version_id,
            relationship_type=relationship_type,
            asserted_at=NOW,
        )
    session.flush()
    ordered = [link.relationship_type for link in list_symbol_standard_links(session, revision_id)]
    assert ordered[0] == "normative_definition"


# --------------------------------------------------------------------------
# Acquisition provenance and Appendix B.2's chain
# --------------------------------------------------------------------------


def test_acquisition_provenance_round_trips(session):
    package = _package(
        session,
        provider="Illustrative provider",
        provider_package_identifier="PKG-2.0",
        source_uri="https://example.test/releases/2.0.zip",
        release_version="2.0",
        release_date=date(2023, 6, 1),
        acquired_at=NOW,
        acquisition_method="licensed_download",
        licence_reference="contract://illustrative/2023-06",
        package_sha256=DIGEST,
        ingestion_profile="illustrative-importer 1.4.0",
        metadata={"illustrative": True},
    )
    stored = get_source_package(session, package.package_code)
    assert stored.acquisition_method == "licensed_download"
    assert stored.package_sha256 == DIGEST
    assert stored.release_date == date(2023, 6, 1)
    assert stored.metadata_json == {"illustrative": True}


def test_acquisition_can_be_recorded_after_the_package_exists(session):
    """The path a submission-created package takes when someone afterwards
    establishes where the content came from."""
    package = _package(session)
    assert package.acquisition_method is None
    record_package_acquisition(
        session,
        package.id,
        recorded_at=NOW,
        acquired_at=NOW,
        acquisition_method="public_download",
        source_uri="https://example.test/a.zip",
        package_sha256=OTHER_DIGEST,
    )
    session.flush()
    assert package.acquisition_method == "public_download"
    assert package.package_sha256 == OTHER_DIGEST
    assert package.updated_at == NOW


def test_recording_an_acquisition_replaces_the_record_rather_than_patching_it(session):
    """One package was obtained once, from one place. An omitted argument
    clears the column, because a half-updated acquisition record would claim
    a provenance that never happened. `metadata_json` is the exception."""
    package = _package(
        session,
        source_uri="https://example.test/first.zip",
        acquired_at=NOW,
        acquisition_method="public_download",
        package_sha256=DIGEST,
        metadata={"illustrative": True},
    )
    record_package_acquisition(
        session,
        package.id,
        recorded_at=NOW,
        source_uri="https://example.test/second.zip",
    )
    session.flush()
    assert package.source_uri == "https://example.test/second.zip"
    assert package.acquisition_method is None
    assert package.acquired_at is None
    assert package.package_sha256 is None
    assert package.metadata_json == {"illustrative": True}


def test_a_package_hash_with_no_acquisition_is_refused_by_the_service_too(session):
    package = _package(session)
    with pytest.raises(ValueError, match="when the package was acquired"):
        record_package_acquisition(
            session, package.id, recorded_at=NOW, package_sha256=DIGEST
        )


def test_entries_trace_a_symbol_back_to_the_exact_provider_entry(session, author_id):
    """Appendix B.2's chain, minus rights: catalogue symbol -> source package
    -> release/version -> exact provider entry/symbol ID -> hashes."""
    revision_id = _symbol_revision(session, author_id)
    package = _package(
        session,
        provider="Illustrative provider",
        provider_package_identifier="PKG-2.0",
        release_version="2.0",
        release_date=date(2023, 6, 1),
        acquired_at=NOW,
        acquisition_method="licensed_download",
        licence_reference="contract://illustrative/2023-06",
        source_uri="https://example.test/releases/2.0.zip",
        package_sha256=DIGEST,
        ingestion_profile="illustrative-importer 1.4.0",
    )
    add_source_package_entry(
        session,
        source_package_id=package.id,
        symbol_revision_id=revision_id,
        added_at=NOW,
        source_label="Gate valve",
        provider_entry_identifier="A-123",
        source_path="symbols/valves/a-123.dwg",
        original_asset_sha256=OTHER_DIGEST,
        sort_order=10,
    )
    session.flush()

    chain = trace_symbol_revision_sources(session, revision_id)
    assert len(chain) == 1
    step = chain[0]
    assert step["package_code"] == package.package_code
    assert step["release_version"] == "2.0"
    assert step["release_date"] == date(2023, 6, 1)
    assert step["provider_entry_identifier"] == "A-123"
    assert step["source_path"] == "symbols/valves/a-123.dwg"
    assert step["package_sha256"] == DIGEST
    assert step["original_asset_sha256"] == OTHER_DIGEST
    # Rights is SM-P0-06. All SM-P0-05 carries is the forward reference.
    assert step["licence_reference"] == "contract://illustrative/2023-06"
    assert "rights_disposition" not in step


def test_tracing_an_unsourced_revision_returns_nothing_rather_than_a_guess(session, author_id):
    revision_id = _symbol_revision(session, author_id)
    assert trace_symbol_revision_sources(session, revision_id) == []


def test_entries_are_found_by_provider_identifier_and_listed_in_shipped_order(session, author_id):
    package = _package(session)
    first = _symbol_revision(session, author_id)
    second = _symbol_revision(session, author_id)
    add_source_package_entry(
        session,
        source_package_id=package.id,
        symbol_revision_id=second,
        added_at=NOW,
        provider_entry_identifier="A-124",
        sort_order=20,
    )
    add_source_package_entry(
        session,
        source_package_id=package.id,
        symbol_revision_id=first,
        added_at=NOW,
        provider_entry_identifier="A-123",
        sort_order=10,
    )
    session.flush()
    assert [entry.provider_entry_identifier for entry in list_source_package_entries(session, package.id)] == [
        "A-123",
        "A-124",
    ]
    found = find_entries_by_provider_identifier(session, package.id, "A-123")
    assert [entry.symbol_revision_id for entry in found] == [first]


def test_the_package_unique_key_still_holds(session, author_id):
    """20260409_0001's `uq_source_package_entries_package_revision` is
    untouched: one entry per (package, revision)."""
    package = _package(session)
    revision_id = _symbol_revision(session, author_id)
    add_source_package_entry(
        session, source_package_id=package.id, symbol_revision_id=revision_id, added_at=NOW
    )
    session.flush()
    add_source_package_entry(
        session, source_package_id=package.id, symbol_revision_id=revision_id, added_at=NOW
    )
    with pytest.raises((IntegrityError, DBAPIError)):
        session.flush()


# --------------------------------------------------------------------------
# Downgrade
# --------------------------------------------------------------------------


def test_downgrade_removes_only_what_upgrade_added_and_restores_the_old_index():
    """20260409_0001's own downgrade drops the replaced index by name, so
    leaving the replacement in its place would break every rollback past the
    initial schema. 20260909_0050's no-op downgrade is not a precedent."""
    with _database("symgov-source-precision-down") as (engine, url, _raw):
        _alembic(url, "upgrade", PRECISION_REVISION)
        _alembic(url, "downgrade", PREVIOUS_REVISION)
        with engine.begin() as connection:
            for table, added in ADDED_COLUMNS.items():
                present = set(
                    connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema='public' AND table_name=:table"
                        ),
                        {"table": table},
                    ).scalars()
                )
                assert present.isdisjoint(added), sorted(present & set(added))
                assert set(ORIGINAL_COLUMNS[table]) <= present

            indexes = set(
                connection.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE schemaname='public' "
                        "AND tablename='symbol_standard_links'"
                    )
                ).scalars()
            )
            assert LEGACY_LINK_INDEX in indexes, "20260409_0001's index was not restored"
            assert "uq_symbol_standard_links_active_assertion" not in indexes
            assert "ix_symbol_standard_links_version_source_symbol_identifier" not in indexes

            checks = connection.execute(
                text(
                    "SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                    "WHERE t.relname = ANY(:names) AND c.contype='c'"
                ),
                {"names": list(PRECISION_TABLES)},
            ).scalar_one()
        assert checks == 0, "a check constraint survived the downgrade"


# The chain cannot be rolled back below this revision: 20260720_0023 raises
# deliberately, because the approved cutover permanently removed non-owner
# roles. It is therefore the furthest a rollback can reach, and the deepest
# this package's downgrade can be rehearsed.
DEEPEST_REACHABLE_ROLLBACK = "20260720_0023"


def test_rolling_back_as_far_as_the_chain_allows_still_works():
    """The restore in `downgrade()` exists because 20260409_0001's own
    downgrade drops the replaced index by name.

    That particular path cannot currently be walked end to end -- 20260720_0023
    is intentionally irreversible and stops any rollback well above the initial
    schema -- so the restore guards a path that is correct rather than one that
    is exercised. What *is* exercised here is that this migration's downgrade
    survives being run as the first of thirty, which is the realistic rollback.
    """
    with _database("symgov-source-precision-deep") as (engine, url, _raw):
        _alembic(url, "upgrade", PRECISION_REVISION)
        result = _alembic(url, "downgrade", DEEPEST_REACHABLE_ROLLBACK, check=False)
        assert result.returncode == 0, result.stderr
        with engine.begin() as connection:
            for table, added in ADDED_COLUMNS.items():
                present = set(
                    connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema='public' AND table_name=:table"
                        ),
                        {"table": table},
                    ).scalars()
                )
                # The tables themselves survive: 20260409_0001 created them and
                # sits below the irreversible revision, so its own downgrade
                # never runs. What must be gone is everything SM-P0-05 added.
                assert set(ORIGINAL_COLUMNS[table]) <= present, table
                assert present.isdisjoint(added), sorted(present & set(added))


def test_the_restored_index_is_the_one_the_initial_schema_drops_by_name():
    """The premise of the restore, pinned so it cannot rot: if 20260409_0001
    stopped naming this index in its own downgrade, restoring it would be
    pointless churn against a deployed object."""
    initial = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "alembic"
        / "versions"
        / "20260409_0001_initial_symgov_schema.py"
    ).read_text(encoding="utf-8")
    assert f'op.drop_index("{LEGACY_LINK_INDEX}"' in initial


def test_a_downgrade_and_re_upgrade_lands_in_the_same_place():
    with _database("symgov-source-precision-cycle") as (engine, url, _raw):
        _alembic(url, "upgrade", PRECISION_REVISION)
        _alembic(url, "downgrade", PREVIOUS_REVISION)
        _alembic(url, "upgrade", PRECISION_REVISION)
        with engine.begin() as connection:
            indexes = set(
                connection.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE schemaname='public' "
                        "AND tablename='symbol_standard_links'"
                    )
                ).scalars()
            )
            columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='symbol_standard_links'"
                    )
                ).scalars()
            )
        assert LEGACY_LINK_INDEX not in indexes
        assert "uq_symbol_standard_links_active_assertion" in indexes
        assert set(ADDED_COLUMNS["symbol_standard_links"]) <= columns
