"""Contract cover for the CFIHOS source standard seed (follow-on to SM-P0-05).

Scope note: this file is deliberately DB-free, and it never reads the CFIHOS
archive. `docs/cfihos/` is gitignored and absent from a fresh clone, so the
migration *is* the extract and these tests pin the extract's shape and the
decisions behind it. Behaviour only a real PostgreSQL server can prove -- the
rows landing, the partial unique index on `provider_identifier`, downgrade
removing exactly the seeded rows and re-upgrade being idempotent -- lives in
`test_cfihos_source_standards_postgresql.py`.
"""

from __future__ import annotations

import importlib.util
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint

from symgov_backend.models import Standard, StandardVersion
from symgov_backend.standard_sources import (
    PROVIDER_IDENTIFIER_MAX_LENGTH,
    STANDARD_CODE_MAX_LENGTH,
    TITLE_MAX_LENGTH,
    normalize_standard_code,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "backend" / "alembic" / "versions" / "20260910_0055_cfihos_source_standards.py"
)

# The published archive the extract came from. Pinned so a re-extract against
# a different release is a conscious act; nothing here opens the file.
SOURCE_ARCHIVE_SHA256 = "a69b98012d9e4a46495a3aed48eef8ce75a69d001eaa28b4c38cb0f3c921909d"

EXPECTED_STANDARDS = 219
EXPECTED_EDITIONS = 238
EXPECTED_MULTI_EDITION = 19
EXPECTED_WITHOUT_ISSUING_BODY = 6


def _migration_module():
    """Import the migration by path, to inspect its frozen extract."""
    spec = importlib.util.spec_from_file_location("cfihos_source_standards_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def seed():
    return _migration_module()._SOURCE_STANDARDS


def _sql_statements(source: str | None = None) -> list[str]:
    """Every SQL string the migration hands to `sa.text`, docstrings and
    comments excluded."""
    if source is None:
        source = MIGRATION.read_text(encoding="utf-8")
    return [
        " ".join(match.split())
        for match in re.findall(r'sa\.text\(\s*((?:"[^"]*"\s*)+)\)', source)
        for match in [" ".join(re.findall(r'"([^"]*)"', match))]
    ]


def _editions(seed):
    return [(code, label, provider) for code, _t, _b, versions in seed for label, provider in versions]


# --------------------------------------------------------------------------
# Migration shape
# --------------------------------------------------------------------------


def test_0055_chains_from_the_source_precision_head():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION.name}"
    migration = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'revision(?:\s*:\s*str)?\s*=\s*"20260910_0055"', migration)
    assert re.search(r'down_revision(?:\s*:\s*[^=]+)?\s*=\s*"20260910_0054"', migration)


def test_0055_records_the_published_archive_it_extracted():
    """The extract is only auditable against the release if the release is
    named. The archive is gitignored, so the hash is the only link."""
    migration = MIGRATION.read_text(encoding="utf-8")
    assert SOURCE_ARCHIVE_SHA256 in migration
    assert "jip36-cfihos.org" in migration


def test_0055_never_reads_the_gitignored_archive():
    """`docs/cfihos/` is absent from a fresh clone. A migration that opened it
    would apply on this box and nowhere else."""
    migration = MIGRATION.read_text(encoding="utf-8")
    for forbidden in ("zipfile", "docs/cfihos", "open(", "csv."):
        assert forbidden not in migration, forbidden


def test_0055_adds_the_provider_identifier_column_and_its_index():
    migration = MIGRATION.read_text(encoding="utf-8")
    assert 'op.add_column(\n        "standard_versions", sa.Column("provider_identifier", sa.Text(), nullable=True)' in migration
    assert '"uq_standard_versions_provider_identifier"' in migration
    assert 'unique=True' in migration
    assert 'postgresql_where=sa.text("provider_identifier is not null")' in migration


def test_0055_check_constraint_name_is_bare():
    migration = MIGRATION.read_text(encoding="utf-8")
    names = re.findall(r"op\.create_check_constraint\(\s*\"([a-z0-9_]+)\"", migration)
    assert names == ["provider_identifier"]


def test_0055_identifiers_fit_the_postgresql_limit():
    migration = MIGRATION.read_text(encoding="utf-8")
    identifiers = re.findall(
        r'(?:op\.create_index\(\s*|op\.drop_index\(\s*|name\s*=\s*)"([A-Za-z_][A-Za-z0-9_]*)"', migration
    )
    assert [name for name in identifiers if len(name) > 63] == []
    assert len("uq_standard_versions_provider_identifier") == 40
    assert len("ck_standard_versions_provider_identifier") == 40


def test_0055_creates_no_symbol_standard_link():
    """Seeding a vocabulary asserts nothing about any symbol. Every link is a
    governed assertion and starts at `proposed`."""
    statements = _sql_statements()
    assert [s for s in statements if s.startswith("INSERT")], "no INSERT statements found"
    assert all("symbol_standard_links" not in statement for statement in statements)


def test_0055_downgrade_deletes_only_its_own_rows_by_deterministic_id():
    """A standard registered by hand that happens to share a code must
    survive a rollback."""
    downgrade = MIGRATION.read_text(encoding="utf-8").split("def downgrade")[1]
    deletes = [s for s in _sql_statements(downgrade) if s.startswith("DELETE")]
    assert deletes == [
        "DELETE FROM standard_versions WHERE id = :id",
        "DELETE FROM standards WHERE id = :id",
    ]
    # Addressed by deterministic uuid5 id, never by code text -- so a
    # hand-registered standard sharing a code survives the rollback.
    assert all("standard_code" not in statement for statement in deletes)
    assert 'op.drop_column("standard_versions", "provider_identifier")' in downgrade


# --------------------------------------------------------------------------
# The frozen extract
# --------------------------------------------------------------------------


def test_the_extract_has_the_expected_shape(seed):
    assert len(seed) == EXPECTED_STANDARDS
    assert len(_editions(seed)) == EXPECTED_EDITIONS


def test_every_standard_code_is_unique_and_already_canonical(seed):
    codes = [code for code, _t, _b, _v in seed]
    assert len(set(codes)) == len(codes)
    for code in codes:
        assert normalize_standard_code(code) == code, code


def test_every_provider_identifier_is_unique_and_cfihos_shaped(seed):
    """`provider_identifier` carries a partial UNIQUE index, so a duplicate
    would fail the migration rather than seed a second row."""
    providers = [provider for _c, _l, provider in _editions(seed)]
    assert len(set(providers)) == len(providers)
    for provider in providers:
        assert re.fullmatch(r"CFIHOS-9000\d{4}", provider), provider


def test_every_edition_label_is_a_four_digit_year(seed):
    """Only `CODE:YYYY` was treated as a split. Anything else would be a
    guessed edition."""
    for _code, label, _provider in _editions(seed):
        assert re.fullmatch(r"\d{4}", label), label


def test_edition_labels_are_unique_within_a_standard(seed):
    """`uq_standard_versions_standard_version_label` would reject a repeat."""
    for code, _t, _b, versions in seed:
        labels = [label for label, _p in versions]
        assert len(set(labels)) == len(labels), code


def test_the_nineteen_multi_edition_standards_are_why_the_split_exists(seed):
    """Without splitting the edition out of the code, these 19 would collide
    or duplicate. They are the evidence the split is load-bearing."""
    multi = {code: versions for code, _t, _b, versions in seed if len(versions) > 1}
    assert len(multi) == EXPECTED_MULTI_EDITION
    assert multi["API SPEC 17D"] == (("2011", "CFIHOS-90000003"), ("2021", "CFIHOS-90000171"))
    for code, versions in multi.items():
        assert len(versions) == 2, code


def test_the_later_edition_supplies_the_title_where_they_disagree(seed):
    """API Spec 17D was renamed between 2011 and 2021. `standards.title` holds
    one name and the current one is the useful one; the superseded name stays
    in CFIHOS's register against its own code."""
    titles = {code: title for code, title, _b, _v in seed}
    assert titles["API SPEC 17D"] == "Specification for Subsea Wellhead and Tree Equipment"
    assert titles["API STD 681"].startswith("Liquid Ring Compressors and Vacuum Pumps")


def test_bsi_amendment_notation_survived_the_split(seed):
    """`BS EN 15804:2012+A2:2019` splits at the *last* colon: `+A2` means
    "incorporating Amendment 2" and is part of the standard's identity."""
    codes = {code for code, _t, _b, _v in seed}
    assert "BS EN 15804:2012+A2" in codes
    assert "BS 8004:2015+A1" in codes
    assert "BS EN 13445-1:2002+A3" in codes
    assert "BS 7608+A1" in codes


def test_issuing_body_is_derived_only_where_the_prefix_names_one(seed):
    bodies = {code: body for code, _t, body, _v in seed}
    missing = sorted(code for code, body in bodies.items() if body is None)
    assert len(missing) == EXPECTED_WITHOUT_ISSUING_BODY
    # EN 13852-1 is CEN and EN 60079-0 is CENELEC; the prefix does not say
    # which. "AC" is a document class, not a body.
    assert all(code.startswith("EN ") or code.startswith("AC ") for code in missing), missing
    assert bodies["API SPEC 6D"] == "American Petroleum Institute"
    assert bodies["IEC 62271-200"] == "International Electrotechnical Commission"
    assert bodies["BS 7608+A1"] == "British Standards Institution"


def test_no_derived_issuing_body_contradicts_its_prefix(seed):
    expected = {
        "API ": "American Petroleum Institute",
        "IOGP ": "International Association of Oil & Gas Producers",
        "IEC ": "International Electrotechnical Commission",
        "NFPA ": "National Fire Protection Association",
        "BS ": "British Standards Institution",
    }
    for code, _title, body, _versions in seed:
        for prefix, name in expected.items():
            if code.startswith(prefix):
                assert body == name, (code, body)


def test_the_extract_stays_inside_the_service_layer_bounds(seed):
    for code, title, body, _versions in seed:
        assert len(code) <= STANDARD_CODE_MAX_LENGTH, code
        assert title.strip() == title and title, code
        assert len(title) <= TITLE_MAX_LENGTH, (code, len(title))
        assert body is None or body.strip() == body
    for _c, _l, provider in _editions(seed):
        assert len(provider) <= PROVIDER_IDENTIFIER_MAX_LENGTH


def test_titles_keep_the_source_typography(seed):
    """The CSV is cp1252 and 57 titles carry en dashes, em dashes or
    ellipses. Flattening them to ASCII would quietly edit the register."""
    non_ascii = [title for _c, title, _b, _v in seed if not title.isascii()]
    assert non_ascii, "the typographic characters were stripped somewhere"
    assert any("—" in title for title in non_ascii)


def test_seed_identifiers_are_reproducible_from_the_codes(seed):
    module = _migration_module()
    code, _title, _body, versions = seed[0]
    assert module._standard_id(code) == uuid.uuid5(
        uuid.NAMESPACE_URL, f"urn:symgov:standard:{code}"
    )
    label = versions[0][0]
    assert module._version_id(code, label) == uuid.uuid5(
        uuid.NAMESPACE_URL, f"urn:symgov:standard-version:{code}:{label}"
    )


def test_every_seeded_identifier_is_distinct(seed):
    module = _migration_module()
    standard_ids = {module._standard_id(code) for code, _t, _b, _v in seed}
    version_ids = {module._version_id(code, label) for code, label, _p in _editions(seed)}
    assert len(standard_ids) == EXPECTED_STANDARDS
    assert len(version_ids) == EXPECTED_EDITIONS
    assert standard_ids.isdisjoint(version_ids)


def test_the_excluded_conventions_are_absent(seed):
    """The 67 rows whose edition cannot be derived are omitted, not guessed
    at. If one appears here, a regex invented its edition."""
    codes = {code for code, _t, _b, _v in seed}
    for excluded in (
        "ASME BPVC SECTION VIII",
        "MACHINERY DIRECTIVE 2006/42/EC",
        "ASME B31.3 - 2020",
        "GCRT5033 ISS 2",
        "CFIHOS",
    ):
        assert excluded not in codes, excluded


# --------------------------------------------------------------------------
# ORM
# --------------------------------------------------------------------------


def test_the_orm_carries_provider_identifier():
    column = StandardVersion.__table__.c["provider_identifier"]
    assert column.nullable
    assert "provider_identifier" not in Standard.__table__.c, (
        "the identifier belongs on the edition: CFIHOS numbers each edition separately"
    )


def test_the_orm_declares_the_matching_check_constraint():
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in StandardVersion.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert set(checks) == {"ck_standard_versions_provider_identifier"}
    assert "512" in checks["ck_standard_versions_provider_identifier"]
    # Standard is still extended by nothing at all.
    assert not [
        c for c in Standard.__table__.constraints if isinstance(c, CheckConstraint)
    ]


def test_the_orm_declares_the_partial_unique_index():
    indexes = {index.name: index for index in StandardVersion.__table__.indexes}
    index = indexes["uq_standard_versions_provider_identifier"]
    assert index.unique
    predicate = " ".join(str(index.dialect_options["postgresql"]["where"]).split())
    assert predicate == "provider_identifier is not null"


# --------------------------------------------------------------------------
# The widened code grammar
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code", ["BS 7608+A1", "BS EN 15804:2012+A2", "BS 8004:2015+A1", "BS EN 13445-1:2002+A3"]
)
def test_the_grammar_accepts_bsi_amendment_notation(code):
    assert normalize_standard_code(code) == code


def test_a_non_breaking_space_is_normalised_rather_than_rejected():
    """It is separator noise from a spreadsheet export, not a character the
    code carries. Reporting it as a non-ASCII error described the wrong
    defect."""
    assert normalize_standard_code("IEC 60537:1976\xa0Withdrawn") == "IEC 60537:1976 WITHDRAWN"


def test_the_grammar_still_refuses_what_is_not_a_code():
    for value in ["", "   ", "+LEADING", "TRAILING+", "café 6D", "x" * 65]:
        with pytest.raises(ValueError):
            normalize_standard_code(value)
