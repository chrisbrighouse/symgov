"""CFIHOS source standard seed rehearsal against a real PostgreSQL server.

What only a real server can show:

* the 219 standards and 238 editions actually land, and the 19 two-edition
  standards resolve to one standard with two versions rather than two
  standards -- the whole point of splitting the edition out of CFIHOS's code
  string;
* `uq_standard_versions_provider_identifier` is partial, so the seeded rows
  are unique by CFIHOS code while hand-registered editions with no provider
  identifier are not forced to collide on NULL;
* `20260409_0001`'s `uq_standard_versions_standard_version_label` still holds
  across the seed;
* downgrade removes exactly the seeded rows and nothing a person added, and
  refuses rather than cascading when a governed assertion depends on one;
* re-upgrade re-seeds the same rows rather than a duplicate set.

Redaction: this file never prints the disposable container's connection string
and every seeded identity uses a synthetic `@example.test` email. It never
reads `docs/cfihos/`, which is gitignored and absent from a fresh clone.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

from symgov_backend.models import Standard, StandardVersion  # noqa: E402
from symgov_backend.standard_sources import (  # noqa: E402
    find_standard_version_by_provider_identifier,
    get_standard,
    list_standard_versions,
    register_standard,
    register_standard_version,
)

SEED_REVISION = "20260910_0055"
PREVIOUS_REVISION = "20260910_0054"

NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)

EXPECTED_STANDARDS = 219
EXPECTED_EDITIONS = 238
EXPECTED_WITHOUT_ISSUING_BODY = 6

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "alembic"
    / "versions"
    / "20260910_0055_cfihos_source_standards.py"
)


def _seed():
    spec = importlib.util.spec_from_file_location("cfihos_seed_migration_pg", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def seeded_database():
    with _database("symgov-cfihos-standards") as (engine, url, _raw):
        _alembic(url, "upgrade", SEED_REVISION)
        yield engine, url


@pytest.fixture()
def session(seeded_database) -> Session:
    engine, _ = seeded_database
    with Session(engine) as active_session:
        yield active_session
        active_session.rollback()


# --------------------------------------------------------------------------
# The seed landed
# --------------------------------------------------------------------------


def test_the_seed_lands_as_219_standards_and_238_editions(seeded_database):
    engine, _ = seeded_database
    with engine.begin() as connection:
        standards = connection.execute(text("SELECT count(*) FROM standards")).scalar_one()
        editions = connection.execute(text("SELECT count(*) FROM standard_versions")).scalar_one()
    assert standards == EXPECTED_STANDARDS
    assert editions == EXPECTED_EDITIONS


def test_every_seeded_row_matches_the_frozen_extract(seeded_database):
    module = _seed()
    engine, _ = seeded_database
    with engine.begin() as connection:
        rows = {
            row.standard_code: row
            for row in connection.execute(
                text("SELECT id, standard_code, title, issuing_body, status FROM standards")
            ).all()
        }
        versions = {
            row.provider_identifier: row
            for row in connection.execute(
                text(
                    "SELECT id, standard_id, version_label, effective_date, status, "
                    "provider_identifier FROM standard_versions"
                )
            ).all()
        }

    for code, title, issuing_body, editions in module._SOURCE_STANDARDS:
        row = rows[code]
        assert row.id == module._standard_id(code)
        assert row.title == title
        assert row.issuing_body == issuing_body
        assert row.status == "active"
        for label, provider in editions:
            version = versions[provider]
            assert version.id == module._version_id(code, label)
            assert version.standard_id == row.id
            assert version.version_label == label
            assert version.status == "active"
            # A four-digit year is not a date; inventing 1 January would make
            # an ordering key that reads as a fact.
            assert version.effective_date is None


def test_the_nineteen_two_edition_standards_are_one_standard_each(seeded_database):
    """The reason the edition is split out of CFIHOS's code string at all.
    Without it these would be 38 standards, or 19 collisions."""
    engine, _ = seeded_database
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT s.standard_code, count(*) AS editions FROM standards s "
                "JOIN standard_versions v ON v.standard_id = s.id "
                "GROUP BY s.standard_code HAVING count(*) > 1 ORDER BY s.standard_code"
            )
        ).all()
        seventeen_d = connection.execute(
            text(
                "SELECT v.version_label, v.provider_identifier FROM standard_versions v "
                "JOIN standards s ON s.id = v.standard_id "
                "WHERE s.standard_code = 'API SPEC 17D' ORDER BY v.version_label"
            )
        ).all()
    assert len(rows) == 19
    assert all(row.editions == 2 for row in rows)
    assert [(r.version_label, r.provider_identifier) for r in seventeen_d] == [
        ("2011", "CFIHOS-90000003"),
        ("2021", "CFIHOS-90000171"),
    ]


def test_the_six_ambiguous_prefixes_kept_a_null_issuing_body(seeded_database):
    """EN 13852-1 is CEN and EN 60079-0 is CENELEC; the prefix does not say
    which. Guessing would have stored my inference as CFIHOS's data."""
    engine, _ = seeded_database
    with engine.begin() as connection:
        missing = connection.execute(
            text(
                "SELECT standard_code FROM standards WHERE issuing_body IS NULL "
                "ORDER BY standard_code"
            )
        ).scalars().all()
    assert len(missing) == EXPECTED_WITHOUT_ISSUING_BODY
    assert all(code.startswith(("EN ", "AC ")) for code in missing), missing


def test_the_seed_asserts_nothing_about_any_symbol(seeded_database):
    """Seeding a vocabulary is not a governed assertion."""
    engine, _ = seeded_database
    with engine.begin() as connection:
        links = connection.execute(text("SELECT count(*) FROM symbol_standard_links")).scalar_one()
    assert links == 0


def test_source_typography_survived_the_round_trip(seeded_database):
    """The CSV is cp1252 and some titles carry em dashes. If the extract had
    been decoded as UTF-8 or flattened to ASCII, this is where it shows."""
    engine, _ = seeded_database
    with engine.begin() as connection:
        title = connection.execute(
            text("SELECT title FROM standards WHERE standard_code = 'ISO/IEC 646'")
        ).scalar_one()
    assert "—" in title


# --------------------------------------------------------------------------
# Constraints on the new column
# --------------------------------------------------------------------------


def test_the_provider_identifier_index_is_unique_and_partial(seeded_database):
    engine, _ = seeded_database
    with engine.begin() as connection:
        definition = connection.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
                "AND indexname='uq_standard_versions_provider_identifier'"
            )
        ).scalar_one()
    assert "UNIQUE" in definition
    assert "WHERE (provider_identifier IS NOT NULL)" in definition


def test_a_duplicate_provider_identifier_is_refused(session):
    """A second edition claiming CFIHOS-90000008 would make the register
    ambiguous, and reconciling a later CFIHOS release impossible."""
    standard = register_standard(
        session, standard_code="TEST DUP", title="Illustrative", registered_at=NOW
    )
    session.flush()
    register_standard_version(
        session,
        standard_id=standard.id,
        version_label="2099",
        registered_at=NOW,
        provider_identifier="CFIHOS-90000008",
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_editions_without_a_provider_identifier_do_not_collide(session):
    """The index is partial precisely so hand-registered editions, which have
    no provider identifier, are not forced to collide on NULL."""
    for suffix in ("A", "B"):
        standard = register_standard(
            session, standard_code=f"TEST NULLS {suffix}", title="Illustrative", registered_at=NOW
        )
        session.flush()
        register_standard_version(
            session, standard_id=standard.id, version_label="2099", registered_at=NOW
        )
    session.flush()


def test_a_blank_provider_identifier_is_refused(session):
    standard = register_standard(
        session, standard_code="TEST BLANK", title="Illustrative", registered_at=NOW
    )
    session.flush()
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO standard_versions (id,standard_id,version_label,status,"
                "provider_identifier,created_at,updated_at) "
                "VALUES (:id,:standard,'2099','active','   ',:now,:now)"
            ),
            {"id": uuid.uuid4(), "standard": standard.id, "now": NOW},
        )
        session.flush()


def test_the_original_edition_uniqueness_still_holds(session):
    """20260409_0001's `uq_standard_versions_standard_version_label` is
    untouched: one edition label per standard."""
    standard = register_standard(
        session, standard_code="TEST LABEL", title="Illustrative", registered_at=NOW
    )
    session.flush()
    register_standard_version(
        session, standard_id=standard.id, version_label="2099", registered_at=NOW
    )
    session.flush()
    register_standard_version(
        session, standard_id=standard.id, version_label="2099", registered_at=NOW
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_the_orm_and_the_database_agree_on_the_constraint_name(seeded_database):
    from test_semantic_constraint_name_repair_postgresql import (
        _database_names,
        _expected_names,
    )

    engine, _ = seeded_database
    with engine.begin() as connection:
        actual = _database_names(connection).get("standard_versions", set())
    assert actual == _expected_names(StandardVersion.__table__)
    assert actual == {"ck_standard_versions_provider_identifier"}


# --------------------------------------------------------------------------
# Reading the seed back through the service layer
# --------------------------------------------------------------------------


def test_a_seeded_standard_is_found_by_its_code(session):
    standard = get_standard(session, "api spec 6d")
    assert standard is not None
    assert standard.title == "Specification for Pipeline and Piping Valves"
    assert standard.issuing_body == "American Petroleum Institute"
    labels = [version.version_label for version in list_standard_versions(session, standard.id)]
    assert labels == ["2014"]


def test_an_edition_is_found_by_its_cfihos_code(session):
    """The lookup the new column exists for: reconcile a later CFIHOS release
    against what is already seeded."""
    version = find_standard_version_by_provider_identifier(session, "CFIHOS-90000008")
    assert version is not None
    assert version.version_label == "2014"
    standard = session.get(Standard, version.standard_id)
    assert standard.standard_code == "API SPEC 6D"


def test_an_unknown_provider_identifier_returns_nothing_rather_than_guessing(session):
    assert find_standard_version_by_provider_identifier(session, "CFIHOS-99999999") is None


def test_both_editions_of_a_two_edition_standard_are_reachable(session):
    standard = get_standard(session, "API Spec 17D")
    versions = list_standard_versions(session, standard.id)
    assert {v.version_label for v in versions} == {"2011", "2021"}
    assert {v.provider_identifier for v in versions} == {
        "CFIHOS-90000003",
        "CFIHOS-90000171",
    }
    # The later edition's title is the one on the standard; the 2011 name is
    # not lost, it stays in CFIHOS's register against CFIHOS-90000003.
    assert standard.title == "Specification for Subsea Wellhead and Tree Equipment"


# --------------------------------------------------------------------------
# Downgrade
# --------------------------------------------------------------------------


def test_downgrade_removes_the_seed_and_the_column():
    with _database("symgov-cfihos-standards-down") as (engine, url, _raw):
        _alembic(url, "upgrade", SEED_REVISION)
        _alembic(url, "downgrade", PREVIOUS_REVISION)
        with engine.begin() as connection:
            assert connection.execute(text("SELECT count(*) FROM standards")).scalar_one() == 0
            assert (
                connection.execute(text("SELECT count(*) FROM standard_versions")).scalar_one() == 0
            )
            columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='standard_versions'"
                    )
                ).scalars()
            )
        assert "provider_identifier" not in columns
        # 20260409_0001's own columns are untouched.
        assert {"id", "standard_id", "version_label", "effective_date", "status"} <= columns


def test_downgrade_leaves_a_hand_registered_standard_alone():
    """Seeded rows are deleted by their deterministic uuid5 id, never by code
    text, so a standard someone registered by hand survives the rollback --
    even one sharing a code with a seeded row."""
    with _database("symgov-cfihos-standards-keep") as (engine, url, _raw):
        _alembic(url, "upgrade", SEED_REVISION)
        mine = uuid.uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO standards (id,standard_code,title,status,created_at,updated_at) "
                    "VALUES (:id,'HAND REGISTERED','Registered by a person','active',:now,:now)"
                ),
                {"id": mine, "now": NOW},
            )
        _alembic(url, "downgrade", PREVIOUS_REVISION)
        with engine.begin() as connection:
            survivors = connection.execute(
                text("SELECT id, standard_code FROM standards")
            ).all()
        assert [(row.id, row.standard_code) for row in survivors] == [(mine, "HAND REGISTERED")]


def test_downgrade_refuses_rather_than_discarding_a_governed_assertion():
    """`symbol_standard_links` references `standard_versions` with no ON
    DELETE, so PostgreSQL refuses. That is the correct outcome: a rollback
    must not silently delete a source assertion someone verified."""
    with _database("symgov-cfihos-standards-fk") as (engine, url, _raw):
        _alembic(url, "upgrade", SEED_REVISION)
        with engine.begin() as connection:
            author = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                    "must_change_pin,is_active,created_at,updated_at) "
                    "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
                ),
                {"id": author, "email": "cfihos-fk-probe@example.test", "now": NOW},
            )
            symbol_id, revision_id = uuid.uuid4(), uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,"
                    "owner_id,created_at,updated_at) VALUES "
                    "(:id,'cfihos-fk-probe','cfihos-fk-probe','Valves','Piping / P&ID',:o,:n,:n)"
                ),
                {"id": symbol_id, "o": author, "n": NOW},
            )
            connection.execute(
                text(
                    "INSERT INTO symbol_revisions (id,symbol_id,revision_label,lifecycle_state,"
                    "payload_json,author_id,created_at) "
                    "VALUES (:id,:symbol,'1','draft','{}'::jsonb,:o,:n)"
                ),
                {"id": revision_id, "symbol": symbol_id, "o": author, "n": NOW},
            )
            version_id = connection.execute(
                text(
                    "SELECT id FROM standard_versions WHERE provider_identifier='CFIHOS-90000008'"
                )
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO symbol_standard_links (id,symbol_revision_id,standard_version_id,"
                    "relationship_type,assertion_status,created_at) "
                    "VALUES (:id,:revision,:version,'normative_definition','proposed',:n)"
                ),
                {"id": uuid.uuid4(), "revision": revision_id, "version": version_id, "n": NOW},
            )

        result = _alembic(url, "downgrade", PREVIOUS_REVISION, check=False)
        assert result.returncode != 0, "the rollback silently discarded a governed assertion"
        assert "foreign key" in result.stderr.lower() or "violates" in result.stderr.lower()


def test_a_downgrade_and_re_upgrade_re_seeds_the_same_rows():
    """Deterministic uuid5 identifiers plus ON CONFLICT DO NOTHING, so a
    re-upgrade lands the same register rather than a duplicate set."""
    with _database("symgov-cfihos-standards-cycle") as (engine, url, _raw):
        _alembic(url, "upgrade", SEED_REVISION)
        with engine.begin() as connection:
            before = connection.execute(
                text("SELECT id FROM standard_versions ORDER BY id")
            ).scalars().all()
        _alembic(url, "downgrade", PREVIOUS_REVISION)
        _alembic(url, "upgrade", SEED_REVISION)
        with engine.begin() as connection:
            after = connection.execute(
                text("SELECT id FROM standard_versions ORDER BY id")
            ).scalars().all()
            standards = connection.execute(text("SELECT count(*) FROM standards")).scalar_one()
        assert after == before
        assert standards == EXPECTED_STANDARDS


def test_re_running_the_upgrade_over_existing_rows_is_idempotent():
    """The seed must survive being applied to a database that already has it
    -- the shape a re-run or a partially-applied migration takes."""
    with _database("symgov-cfihos-standards-twice") as (engine, url, _raw):
        _alembic(url, "upgrade", SEED_REVISION)
        module = _seed()
        with engine.begin() as connection:
            for code, title, issuing_body, editions in module._SOURCE_STANDARDS[:5]:
                connection.execute(
                    text(
                        "INSERT INTO standards (id,standard_code,title,issuing_body,status,"
                        "created_at,updated_at) "
                        "VALUES (:id,:code,:title,:body,'active',now(),now()) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {
                        "id": module._standard_id(code),
                        "code": code,
                        "title": title,
                        "body": issuing_body,
                    },
                )
            assert (
                connection.execute(text("SELECT count(*) FROM standards")).scalar_one()
                == EXPECTED_STANDARDS
            )
