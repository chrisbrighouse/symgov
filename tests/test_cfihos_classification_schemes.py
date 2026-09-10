"""Contract cover for the CFIHOS-sourced classification schemes (20260909_0052).

Scope note: this file is deliberately DB-free, and deliberately does not read
`docs/cfihos/`. Those source archives are not committed, so a test that
parsed them would pass locally and fail in CI. The migration's frozen extract
is the contract; this file pins its shape, and
`test_cfihos_classification_schemes_postgresql.py` proves it lands.
"""

from __future__ import annotations

import importlib.util
import re
import uuid
from pathlib import Path

import pytest

from symgov_backend.classification_schemes import (
    NODE_CODE_PATTERN,
    SEED_CLASSIFICATION_SCHEMES,
    classification_node_seed_id,
    classification_scheme_seed_id,
    derive_classification_node_code,
    normalize_classification_scheme_code,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "backend" / "alembic" / "versions" / "20260909_0052_cfihos_classification_schemes.py"
)

# The published release this extract came from.
SOURCE_SHA256 = "a69b98012d9e4a46495a3aed48eef8ce75a69d001eaa28b4c38cb0f3c921909d"
SOURCE_URL = "https://www.jip36-cfihos.org/cfihos-standards/"


def _module():
    spec = importlib.util.spec_from_file_location("cfihos_schemes_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration():
    return _module()


# --------------------------------------------------------------------------
# Migration chaining and provenance
# --------------------------------------------------------------------------


def test_0052_chains_from_the_classification_data_model():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION.name}"
    source = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'revision(?:\s*:\s*str)?\s*=\s*"20260909_0052"', source)
    assert re.search(r'down_revision(?:\s*:\s*[^=]+)?\s*=\s*"20260909_0051"', source)


def test_0052_records_the_source_release_and_its_hash():
    """The archive is not committed, so the hash in the docstring is what
    makes this extract auditable against the published release."""
    source = MIGRATION.read_text(encoding="utf-8")
    assert SOURCE_SHA256 in source
    assert SOURCE_URL in source
    assert "CORE-CFIHOS-CSV-v2.0.zip" in source


def test_0052_labels_both_schemes_with_the_source_release(migration):
    assert migration._SEED_VERSION_LABEL == "CFIHOS v2.0"


def test_0052_creates_no_tables_and_no_assignments():
    """This is seed data only -- the storage landed in 20260909_0051."""
    source = MIGRATION.read_text(encoding="utf-8")
    for forbidden in (
        "op.create_table(", "op.add_column(", "op.alter_column(", "op.drop_column(",
        "op.create_index(",
    ):
        assert forbidden not in source, f"seed migration must not change storage: {forbidden}"
    for table in ("concept_classification_assignments", "symbol_revision_classifications"):
        assert table not in source


def test_0052_only_touches_the_two_schemes_it_seeds(migration):
    """A DELETE with no scheme filter would take SM-P0-04's three catalogue
    schemes with it."""
    source = MIGRATION.read_text(encoding="utf-8")
    compact = " ".join(source.split())
    assert "DELETE FROM classification_nodes WHERE scheme_id = :id" in compact
    assert "DELETE FROM classification_schemes WHERE id = :id" in compact
    assert "DELETE FROM classification_nodes\"" not in compact
    seeded = {migration._DOCUMENT_TYPE_SCHEME[0], migration._REPRESENTATION_TYPE_SCHEME[0]}
    catalogue = {definition["scheme_code"] for definition in SEED_CLASSIFICATION_SCHEMES}
    assert seeded.isdisjoint(catalogue)


def test_0052_downgrade_deletes_nodes_before_schemes():
    source = MIGRATION.read_text(encoding="utf-8")
    downgrade = source[source.index("def downgrade()"):]
    assert downgrade.index("DELETE FROM classification_nodes") < downgrade.index(
        "DELETE FROM classification_schemes"
    )


# --------------------------------------------------------------------------
# Document Type
# --------------------------------------------------------------------------


def test_document_type_seeds_the_whole_cfihos_vocabulary(migration):
    """329 nodes, not a hand-cut drawing subset. Choosing which entries count
    as a "drawing" would mean inventing the boundary."""
    assert len(migration._DOCUMENT_TYPES) == 329


def test_document_type_scheme_is_named_after_its_source_vocabulary(migration):
    """CFIHOS calls this `document type`, and the list includes entries like
    "audit plan". Calling the scheme "Drawing Type" would be a misnomer;
    specification section 7.6's drawing-type facet is served by filtering it."""
    scheme_code, name, description = migration._DOCUMENT_TYPE_SCHEME
    assert scheme_code == "DOCUMENT-TYPE"
    assert name == "Document Type"
    assert normalize_classification_scheme_code(scheme_code) == scheme_code
    assert "CFIHOS" in description
    assert "Drawing Type" not in name


def test_document_type_node_codes_are_the_cfihos_short_codes(migration):
    """Four digits, unique across all 329. Minting our own codes where the
    source has a governed code system would create reconciliation debt."""
    codes = [code for code, _label, _description in migration._DOCUMENT_TYPES]
    assert len(set(codes)) == len(codes)
    for code in codes:
        assert len(code) == 4, code
        assert code.isdigit(), code
        assert NODE_CODE_PATTERN.match(code), code


def test_document_type_carries_the_facet_the_specification_asked_for(migration):
    """Specification section 7.6 names Drawing Type as desirable. These are
    the entries that serve it -- real, citable, and not invented."""
    by_code = {code: label for code, label, _description in migration._DOCUMENT_TYPES}
    assert by_code["2365"] == "piping and instrumentation diagram"
    assert by_code["0980"] == "block diagram"
    assert by_code["4018"] == "general arrangement diagram"
    assert by_code["4780"] == "logic diagram"


def test_document_type_labels_and_descriptions_are_present_and_bounded(migration):
    labels = [label for _code, label, _description in migration._DOCUMENT_TYPES]
    assert len(set(labels)) == len(labels)
    for code, label, description in migration._DOCUMENT_TYPES:
        assert label.strip() == label and label, code
        assert len(label) <= 256, code
        # Every CFIHOS entry carries a definition; that is the most valuable
        # part of the source, so a blank one means the extract went wrong.
        assert description.strip() == description and description, code
        assert len(description) <= 4000, code


def test_document_type_order_is_the_source_order(migration):
    """The CFIHOS file is ordered alphabetically by name, and sort_order
    reproduces it."""
    labels = [label for _code, label, _description in migration._DOCUMENT_TYPES]
    assert labels == sorted(labels)


# --------------------------------------------------------------------------
# Representation Type
# --------------------------------------------------------------------------


def test_representation_type_seeds_the_five_real_values(migration):
    """The most directly section 7.8 facet in the source: what form the
    representation actually takes."""
    assert migration._REPRESENTATION_TYPES == (
        ("INTELLIGENT_VECTOR_DRAWING_CAD", "Intelligent vector drawing (CAD)"),
        ("MULTI_MEDIA", "Multi media"),
        ("RASTER_IMAGE", "Raster Image"),
        ("STRUCTURED_DATA", "Structured Data"),
        ("TEXT", "Text"),
    )


def test_representation_type_excludes_the_absence_of_a_value(migration):
    """`not specified` appears on 94 source rows. It is the absence of a
    value, not a value, so it is not a node."""
    labels = {label.lower() for _code, label in migration._REPRESENTATION_TYPES}
    assert "not specified" not in labels
    assert "" not in labels


def test_representation_type_folds_the_source_casing_inconsistency(migration):
    """The source carries both `Text` (607 rows) and `text` (1 row)."""
    labels = [label for _code, label in migration._REPRESENTATION_TYPES]
    assert labels.count("Text") == 1
    assert len({label.lower() for label in labels}) == len(labels)


def test_representation_type_node_codes_are_derived_from_the_labels(migration):
    """Unlike Document Type, these values have no CFIHOS code -- they are free
    text in a column -- so the codes are derived, as SM-P0-04's three schemes
    are. node_code is unique per scheme, so a scheme carrying its own code
    system is exactly what the column is for."""
    for code, label in migration._REPRESENTATION_TYPES:
        assert code == derive_classification_node_code(label)
        assert NODE_CODE_PATTERN.match(code), code


def test_representation_type_scheme_is_platform_scoped(migration):
    scheme_code, name, description = migration._REPRESENTATION_TYPE_SCHEME
    assert scheme_code == "REPRESENTATION-TYPE"
    assert name == "Representation Type"
    assert normalize_classification_scheme_code(scheme_code) == scheme_code
    assert "CFIHOS" in description
    source = MIGRATION.read_text(encoding="utf-8")
    assert "'platform'" in source


# --------------------------------------------------------------------------
# Identifiers and scope boundary
# --------------------------------------------------------------------------


def test_seed_identifiers_are_the_same_urn_hashes_sm_p0_04_uses(migration):
    for scheme_code, nodes in (
        (migration._DOCUMENT_TYPE_SCHEME[0], migration._DOCUMENT_TYPES),
        (migration._REPRESENTATION_TYPE_SCHEME[0], migration._REPRESENTATION_TYPES),
    ):
        assert classification_scheme_seed_id(scheme_code) == uuid.uuid5(
            uuid.NAMESPACE_URL, f"urn:symgov:classification-scheme:{scheme_code}"
        )
        for row in nodes:
            assert classification_node_seed_id(scheme_code, row[0]) == uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"urn:symgov:classification-node:{scheme_code}:{row[0]}",
            )


def test_industry_application_is_still_not_invented(migration):
    """CFIHOS is a single-industry standard and supplies no industry facet.
    Its nearest column, `asset type reference`, describes asset granularity.
    The section 7.6 gap stands rather than being filled with an invention."""
    source = MIGRATION.read_text(encoding="utf-8")
    seeded = {migration._DOCUMENT_TYPE_SCHEME[0], migration._REPRESENTATION_TYPE_SCHEME[0]}
    for absent in ("INDUSTRY", "INDUSTRY-APPLICATION", "ASSET-TYPE", "DRAWING-TYPE"):
        assert absent not in seeded
    assert "INSERT INTO classification_schemes" in source
    assert source.count("_seed_scheme(") == 3  # one definition, two calls


def test_bulk_cfihos_reference_data_stays_out_of_this_package(migration):
    """The same archive carries 832 equipment classes, 875 tag classes, 11890
    RDL objects and 3091 cross-scheme mappings. Those are a versioned external
    dependency and belong to SM-P2-02 through the SM-P0-03 scheme/version
    machinery, not to a migration."""
    source = MIGRATION.read_text(encoding="utf-8")
    for table in (
        "semantic_concepts",
        "concept_external_references",
        "external_semantic_schemes",
        "external_semantic_scheme_versions",
    ):
        assert f"INSERT INTO {table}" not in source
    assert len(migration._DOCUMENT_TYPES) + len(migration._REPRESENTATION_TYPES) == 334
