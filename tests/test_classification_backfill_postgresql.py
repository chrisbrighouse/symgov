"""Real-PostgreSQL cover for SM-P0-10: the legacy classification backfill.

The matching rules are proved DB-free in `test_classification_backfill.py`.
What needs a real server is everything about the sweep: that a dry run writes
nothing, that an applied run creates `proposed`/`legacy_backfill` rows against
the nodes migration `20260909_0051` seeded, that such a row is refused
verification by the check constraint section 12.3 asks for, that a rerun adds
nothing, that a revision already carrying a promotion-time primary is left
alone, and that the target set reaches a revision still on a published page
after the symbol has moved on.

Migration head is unchanged by SM-P0-10: no table, column or constraint is
added, so no `*_postgresql.py` fixture constant moves and no sole-head
assertion changes. Section 15.1 calls this package *utilities*, and the tables
it writes to arrived with SM-P0-04.

Revisions are seeded `lifecycle_state='draft'`: `20260826_0031`'s invariant
requires a canonical catalog identifier for a published revision, which is not
what this package is about.
"""

from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.classification_assignments import (  # noqa: E402
    propose_symbol_revision_classification,
    transition_symbol_revision_classification,
)
from symgov_backend.classification_backfill import (  # noqa: E402
    BACKFILL_METHOD,
    BACKFILLER_VERSION,
    backfilled_assignments,
    run_legacy_classification_backfill,
    select_backfill_targets,
)
from symgov_backend.classification_mapping import MAPPING_METHOD  # noqa: E402
from symgov_backend.classification_schemes import get_classification_scheme  # noqa: E402
from symgov_backend.models import (  # noqa: E402
    ClassificationNode,
    GovernedSymbol,
    PublicationPack,
    PublishedPage,
    SymbolRevision,
    SymbolRevisionClassificationAssignment,
)

NOW = datetime(2026, 9, 11, 14, 30, tzinfo=timezone.utc)
DISCIPLINE_SCHEME = "ENGINEERING-DISCIPLINE"
CATEGORY_SCHEME = "SYMBOL-CATEGORY-FAMILY"


@pytest.fixture(scope="module")
def session_factory():
    with _database("symgov-sm-p0-10") as (engine, url, _raw):
        _alembic(url, "upgrade", "head")
        yield sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def session(session_factory):
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _user(session, label: str) -> uuid.UUID:
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


def _symbol(
    session,
    owner: uuid.UUID,
    *,
    category: str,
    discipline: str,
    created_at: datetime | None = None,
    make_current: bool = True,
) -> tuple[GovernedSymbol, SymbolRevision]:
    """Seed one governed symbol and the draft revision the catalogue shows."""
    slug = f"backfill-{uuid.uuid4().hex[:12]}"
    symbol = GovernedSymbol(
        id=uuid.uuid4(),
        slug=slug,
        canonical_name=slug,
        category=category,
        discipline=discipline,
        owner_id=owner,
        created_at=created_at or NOW,
        updated_at=created_at or NOW,
    )
    session.add(symbol)
    session.flush()
    revision = SymbolRevision(
        id=uuid.uuid4(),
        symbol_id=symbol.id,
        revision_label="1",
        lifecycle_state="draft",
        payload_json={},
        author_id=owner,
        created_at=created_at or NOW,
    )
    session.add(revision)
    session.flush()
    if make_current:
        symbol.current_revision_id = revision.id
        session.flush()
    return symbol, revision


def _node(session, *, scheme_code: str, label: str) -> ClassificationNode:
    scheme = get_classification_scheme(session, scheme_code)
    assert scheme is not None, f"{scheme_code} is seeded by 20260909_0051"
    node = (
        session.query(ClassificationNode)
        .filter_by(scheme_id=scheme.id, preferred_label=label)
        .one()
    )
    return node


def _assignments(session, revision_id: uuid.UUID) -> list[SymbolRevisionClassificationAssignment]:
    return (
        session.query(SymbolRevisionClassificationAssignment)
        .filter_by(symbol_revision_id=revision_id)
        .all()
    )


def _sweep(session, *, apply: bool, **kwargs):
    return run_legacy_classification_backfill(
        session, backfilled_at=NOW, apply=apply, **kwargs
    )


def test_the_seeded_nodes_are_the_hard_coded_catalogue_lists(session):
    """The premise the whole package rests on. If the seed ever diverges from
    `catalog_taxonomy`, deriving a legacy column back from an assignment
    stops being byte-identical and every rewrite count below is wrong."""
    from symgov_backend.catalog_taxonomy import (
        CATALOG_CATEGORY_ORDER,
        CATALOG_DISCIPLINE_ORDER,
    )

    for scheme_code, labels in (
        (DISCIPLINE_SCHEME, CATALOG_DISCIPLINE_ORDER),
        (CATEGORY_SCHEME, CATALOG_CATEGORY_ORDER),
    ):
        scheme = get_classification_scheme(session, scheme_code)
        seeded = {
            node.preferred_label
            for node in session.query(ClassificationNode).filter_by(scheme_id=scheme.id)
        }
        assert seeded == set(labels)


def test_a_dry_run_writes_nothing_and_still_reports_every_rewrite(session):
    owner = _user(session, "dry-run")
    symbol, revision = _symbol(session, owner, category="door", discipline="piping")

    report = _sweep(session, apply=False, symbol_slug=symbol.slug)

    assert report.applied is False
    assert report.targets_examined == 1
    assert len(report.planned) == 2
    assert report.written_assignment_ids == []
    assert _assignments(session, revision.id) == []
    # Both values move, and the dry run says so before anything is written.
    assert {
        (item.field, item.raw_value, item.node_label) for item in report.label_differences
    } == {
        ("category", "door", "Doors"),
        ("discipline", "piping", "Piping / P&ID"),
    }
    # Only the trailing-S one is allowed to reach the column; `piping` is a
    # legacy-taxonomy match and stays out of it.
    assert [item.field for item in report.expected_column_changes] == ["category"]


def test_the_sweep_writes_proposed_legacy_backfill_rows(session):
    owner = _user(session, "apply")
    symbol, revision = _symbol(session, owner, category="Pumps", discipline="Process")

    report = _sweep(session, apply=True, symbol_slug=symbol.slug)

    assert len(report.written_assignment_ids) == 2
    written = _assignments(session, revision.id)
    assert len(written) == 2
    for assignment in written:
        assert assignment.status == "proposed"
        assert assignment.method == BACKFILL_METHOD
        assert assignment.assignment_role == "primary"
        assert assignment.confidence is None
        assert assignment.reviewed_by_user_id is None
        assert assignment.proposed_by_user_id is None
        assert assignment.evidence_json["source"] == "legacy_governed_symbol_columns"
        assert assignment.evidence_json["backfiller_version"] == BACKFILLER_VERSION
        assert assignment.evidence_json["governed_symbol_slug"] == symbol.slug

    # An exact value derives back byte-identical, so nothing visibly changes.
    assert report.label_differences == []
    assert report.expected_column_changes == []
    by_scheme = {
        assignment.classification_scheme_id: assignment for assignment in written
    }
    assert len(by_scheme) == 2, "one primary per scheme, not two in one"


def test_the_service_refuses_to_verify_a_backfilled_assignment(session):
    """Section 12.3: existing published symbols must not be silently
    reclassified as verified. The service refuses the transition and names
    the way forward -- propose afresh with a real method."""
    owner = _user(session, "verify")
    symbol, revision = _symbol(session, owner, category="Valves", discipline="Process")
    _sweep(session, apply=True, symbol_slug=symbol.slug)
    assignment = _assignments(session, revision.id)[0]
    reviewer = _user(session, "reviewer")

    with pytest.raises(ValueError, match="cannot be verified; propose it afresh"):
        transition_symbol_revision_classification(
            session,
            assignment_id=assignment.id,
            target_status="verified",
            occurred_at=NOW + timedelta(minutes=5),
            reviewed_by_user_id=reviewer,
        )
    assert assignment.status == "proposed"


def test_the_constraint_refuses_it_too_not_only_the_service(session):
    """The same refusal one layer down, so a future writer that bypasses
    `transition_symbol_revision_classification` still cannot produce a
    verified backfill row."""
    owner = _user(session, "constraint")
    symbol, revision = _symbol(session, owner, category="Pumps", discipline="Process")
    _sweep(session, apply=True, symbol_slug=symbol.slug)
    assignment = _assignments(session, revision.id)[0]

    table = SymbolRevisionClassificationAssignment.__tablename__
    with pytest.raises(IntegrityError) as raised:
        session.execute(
            text(f"UPDATE {table} SET status='verified' WHERE id = :id"),
            {"id": assignment.id},
        )
    assert "backfill_not_verified" in str(raised.value)


def test_a_backfilled_assignment_can_still_be_rejected(session):
    """Refusing verification is not the same as freezing the row. A reviewer
    who disagrees must be able to close it, which is what leaves the scheme
    open for a proposal with a real method."""
    owner = _user(session, "reject")
    symbol, revision = _symbol(session, owner, category="Valves", discipline="Process")
    _sweep(session, apply=True, symbol_slug=symbol.slug)
    assignment = _assignments(session, revision.id)[0]
    reviewer = _user(session, "rejecter")

    transition_symbol_revision_classification(
        session,
        assignment_id=assignment.id,
        target_status="rejected",
        occurred_at=NOW + timedelta(minutes=5),
        reviewed_by_user_id=reviewer,
    )
    session.flush()
    assert assignment.status == "rejected"


def test_a_rerun_adds_nothing(session):
    owner = _user(session, "rerun")
    symbol, revision = _symbol(session, owner, category="door", discipline="piping")

    first = _sweep(session, apply=True, symbol_slug=symbol.slug)
    second = _sweep(session, apply=True, symbol_slug=symbol.slug)

    assert len(first.written_assignment_ids) == 2
    assert second.written_assignment_ids == []
    assert all(item.reused for item in second.planned)
    assert len(_assignments(session, revision.id)) == 2


def test_a_revision_that_already_has_a_primary_is_skipped(session):
    """A symbol promoted since SM-P0-07 carries a `source_mapping` proposal
    from its actual review record. That is better evidence than a column, so
    the backfill stays out of the scheme entirely -- even though its own rule
    would have chosen a different node."""
    owner = _user(session, "occupied")
    symbol, revision = _symbol(session, owner, category="door", discipline="Process")
    propose_symbol_revision_classification(
        session,
        symbol_revision_id=revision.id,
        classification_node_id=_node(session, scheme_code=CATEGORY_SCHEME, label="Valves").id,
        assignment_role="primary",
        method=MAPPING_METHOD,
        proposed_at=NOW,
    )
    session.flush()

    report = _sweep(session, apply=True, symbol_slug=symbol.slug)

    skipped = {item.field: item for item in report.skipped}
    assert skipped["category"].reason == "already_assigned"
    assert "source_mapping" in skipped["category"].detail
    # The discipline scheme was untouched by the promotion mapping, so it is
    # still backfilled.
    assert [item.field for item in report.planned] == ["discipline"]
    methods = {assignment.method for assignment in _assignments(session, revision.id)}
    assert methods == {MAPPING_METHOD, BACKFILL_METHOD}


def test_a_rejected_primary_leaves_the_scheme_open(session):
    owner = _user(session, "rejected-primary")
    symbol, revision = _symbol(session, owner, category="Valves", discipline="Process")
    assignment = propose_symbol_revision_classification(
        session,
        symbol_revision_id=revision.id,
        classification_node_id=_node(session, scheme_code=CATEGORY_SCHEME, label="Pumps").id,
        assignment_role="primary",
        method=MAPPING_METHOD,
        proposed_at=NOW,
    )
    session.flush()
    transition_symbol_revision_classification(
        session,
        assignment_id=assignment.id,
        target_status="rejected",
        occurred_at=NOW + timedelta(minutes=1),
        reviewed_by_user_id=_user(session, "rejector"),
    )
    session.flush()

    report = _sweep(session, apply=True, symbol_slug=symbol.slug)

    assert {item.field for item in report.planned} == {"category", "discipline"}
    assert "already_assigned" not in {item.reason for item in report.skipped}


def test_a_normalising_value_lands_on_the_seeded_node(session):
    owner = _user(session, "normalise")
    symbol, revision = _symbol(session, owner, category="door", discipline="Process")

    report = _sweep(session, apply=True, symbol_slug=symbol.slug)

    category = next(item for item in report.planned if item.field == "category")
    assert category.node_label == "Doors"
    assert category.match_basis == "plural_variant"
    assert category.label_differs_from_column is True
    assert category.would_change_column is True
    written = next(
        assignment
        for assignment in _assignments(session, revision.id)
        if assignment.classification_node_id == category.classification_node_id
    )
    assert written.evidence_json["raw_value"] == "door"
    assert written.evidence_json["match_basis"] == "plural_variant"
    assert written.evidence_json["node_code"] == "DOORS"


def test_a_legacy_short_form_reaches_the_node_the_first_two_rules_cannot(session):
    """`piping` is the case that made the third matching rule worth adding:
    `PIPING` is not `PIPING_P_ID`, so before it this symbol got no discipline
    assignment at all."""
    owner = _user(session, "legacy-form")
    symbol, revision = _symbol(session, owner, category="Valves", discipline="piping")

    report = _sweep(session, apply=True, symbol_slug=symbol.slug)

    discipline = next(item for item in report.planned if item.field == "discipline")
    assert discipline.node_label == "Piping / P&ID"
    assert discipline.match_basis == "legacy_taxonomy"
    assert discipline.label_differs_from_column is True
    # A legacy-taxonomy match earns its assignment but not the column.
    assert discipline.would_change_column is False


def test_the_promotion_fallbacks_produce_no_assignment(session):
    """`category='symbol'`, `discipline='general'` is what a promotion with no
    classification record leaves behind. Both are placeholders, so the sweep
    proposes nothing and the legacy columns keep the values they have."""
    owner = _user(session, "fallbacks")
    symbol, revision = _symbol(session, owner, category="symbol", discipline="general")

    report = _sweep(session, apply=True, symbol_slug=symbol.slug)

    assert report.planned == []
    assert {item.reason for item in report.skipped} == {"placeholder_value"}
    assert _assignments(session, revision.id) == []
    session.refresh(symbol)
    assert symbol.category == "symbol"
    assert symbol.discipline == "general"


def test_a_value_matching_nothing_leaves_the_column_alone(session):
    owner = _user(session, "unmappable")
    symbol, revision = _symbol(
        session, owner, category="brontosaurus", discipline="astrology"
    )

    report = _sweep(session, apply=True, symbol_slug=symbol.slug)

    assert report.planned == []
    assert {item.reason for item in report.skipped} == {"no_node_match"}
    assert _assignments(session, revision.id) == []
    session.refresh(symbol)
    assert symbol.category == "brontosaurus"
    assert symbol.discipline == "astrology"


def test_a_published_page_revision_is_swept_even_when_it_is_not_current(session):
    """The catalogue joins `published_pages.current_symbol_revision_id`, so a
    revision still on a published page is displayed even after the symbol has
    moved on. Both revisions are targets."""
    owner = _user(session, "published")
    symbol, published_revision = _symbol(
        session, owner, category="Valves", discipline="Process", make_current=False
    )
    newer = SymbolRevision(
        id=uuid.uuid4(),
        symbol_id=symbol.id,
        revision_label="2",
        lifecycle_state="draft",
        payload_json={},
        author_id=owner,
        created_at=NOW + timedelta(days=1),
    )
    session.add(newer)
    session.flush()
    symbol.current_revision_id = newer.id

    pack = PublicationPack(
        id=uuid.uuid4(),
        pack_code=f"pack-{uuid.uuid4().hex[:8]}",
        title="Backfill pack",
        audience="public",
        effective_date=date(2026, 9, 1),
        status="published",
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(pack)
    session.flush()
    session.add(
        PublishedPage(
            id=uuid.uuid4(),
            page_code=f"page-{uuid.uuid4().hex[:8]}",
            title="Backfill page",
            pack_id=pack.id,
            current_symbol_revision_id=published_revision.id,
            effective_date=date(2026, 9, 1),
            publication_state="active",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()

    targets = select_backfill_targets(session, symbol_slug=symbol.slug)

    assert {target.symbol_revision_id for target in targets} == {
        published_revision.id,
        newer.id,
    }
    report = _sweep(session, apply=True, symbol_slug=symbol.slug)
    assert report.targets_examined == 2
    assert len(report.written_assignment_ids) == 4


def test_a_symbol_with_no_current_revision_is_not_a_target(session):
    owner = _user(session, "no-current")
    symbol, _revision = _symbol(
        session, owner, category="Valves", discipline="Process", make_current=False
    )
    assert select_backfill_targets(session, symbol_slug=symbol.slug) == []


def test_the_limit_bounds_the_sweep_and_a_bad_limit_is_refused(session):
    owner = _user(session, "limit")
    for index in range(3):
        _symbol(
            session,
            owner,
            category="Valves",
            discipline="Process",
            created_at=NOW + timedelta(minutes=index),
        )

    assert len(select_backfill_targets(session, limit=2)) == 2
    with pytest.raises(ValueError, match="at least 1"):
        select_backfill_targets(session, limit=0)


def test_the_module_reports_only_its_own_rows(session):
    owner = _user(session, "listing")
    symbol, revision = _symbol(session, owner, category="Valves", discipline="Process")
    propose_symbol_revision_classification(
        session,
        symbol_revision_id=revision.id,
        classification_node_id=_node(
            session, scheme_code=CATEGORY_SCHEME, label="Drawing Symbols"
        ).id,
        assignment_role="secondary",
        method=MAPPING_METHOD,
        proposed_at=NOW,
    )
    session.flush()
    _sweep(session, apply=True, symbol_slug=symbol.slug)

    listed = backfilled_assignments(session, symbol_revision_id=revision.id)
    assert len(listed) == 2
    assert {assignment.method for assignment in listed} == {BACKFILL_METHOD}
