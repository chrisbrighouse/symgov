"""Real-PostgreSQL cover for SM-P0-09: the legacy dual write and read.

The derivation rules and the flag-off byte-identity are proved DB-free in
`test_legacy_classification_sync.py`. What needs a real server is the write --
that promoting a symbol whose reviewed category is `door` leaves `Doors` in
`governed_symbols.category`, that a value matching no node leaves the column
exactly as it was, that a verified primary outranks a proposed one and a
`manual` proposal outranks a `legacy_backfill` one against real rows -- and
that the M5 filter SQL both *executes* against the real schema and *matches*
the right revisions.

On the scope of the M5 cover: this file proves the composed catalogue query
runs (which is where a wrong table name would show, and
`symbol_revision_classifications` is not the name the specification's logical
model suggests) and proves the `EXISTS` predicate matches and refuses the
right rows against seeded assignments. It does **not** build a published
public catalogue end to end -- pack, page, pack entry and a published revision
with a canonical catalog identifier -- because that machinery is exercised by
`test_wp73_promotion_publication_handoff_postgresql.py` and the flag is off by
default. An end-to-end assertion with the flag on is the natural follow-up
when the rollout actually begins.

Migration head is unchanged by SM-P0-09: no table, column or constraint is
added, so no `*_postgresql.py` fixture constant moves and no sole-head
assertion changes. Section 12.2 keeps both columns exactly as they are and
changes only what is written into them.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.catalog_search import catalog_symbol_filters  # noqa: E402
from symgov_backend.classification_assignments import (  # noqa: E402
    propose_symbol_revision_classification,
    transition_symbol_revision_classification,
)
from symgov_backend.classification_backfill import (  # noqa: E402
    BACKFILL_METHOD,
    backfilled_assignments,
    run_legacy_classification_backfill,
)
from symgov_backend.classification_mapping import MAPPING_METHOD  # noqa: E402
from symgov_backend.classification_schemes import get_classification_scheme  # noqa: E402
from symgov_backend.legacy_classification_sync import (  # noqa: E402
    derivable_primary_assignment,
    plan_legacy_sync,
    sync_legacy_symbol_columns,
)
from symgov_backend.models import (  # noqa: E402
    ClassificationNode,
    GovernedSymbol,
    SymbolRevision,
)
from symgov_backend.published_catalog import PUBLISHED_SYMBOLS_SQL  # noqa: E402

NOW = datetime(2026, 9, 11, 15, 30, tzinfo=timezone.utc)
DISCIPLINE_SCHEME = "ENGINEERING-DISCIPLINE"
CATEGORY_SCHEME = "SYMBOL-CATEGORY-FAMILY"

FILTER_KWARGS = dict(
    q=None,
    use_case=None,
    format_=None,
    pack=None,
    symbol_family=None,
    has_preview=None,
    updated_since=None,
)


@pytest.fixture(scope="module")
def session_factory():
    with _database("symgov-sm-p0-09") as (engine, url, _raw):
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
    session, owner: uuid.UUID, *, category: str, discipline: str
) -> tuple[GovernedSymbol, SymbolRevision]:
    slug = f"sync-{uuid.uuid4().hex[:12]}"
    symbol = GovernedSymbol(
        id=uuid.uuid4(),
        slug=slug,
        canonical_name=slug,
        category=category,
        discipline=discipline,
        owner_id=owner,
        created_at=NOW,
        updated_at=NOW,
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
        created_at=NOW,
    )
    session.add(revision)
    session.flush()
    symbol.current_revision_id = revision.id
    session.flush()
    return symbol, revision


def _node(session, *, scheme_code: str, label: str) -> ClassificationNode:
    scheme = get_classification_scheme(session, scheme_code)
    return (
        session.query(ClassificationNode)
        .filter_by(scheme_id=scheme.id, preferred_label=label)
        .one()
    )


def _propose(session, revision, *, label, scheme_code, method, role="primary", at=None):
    return propose_symbol_revision_classification(
        session,
        symbol_revision_id=revision.id,
        classification_node_id=_node(session, scheme_code=scheme_code, label=label).id,
        assignment_role=role,
        method=method,
        proposed_at=at or NOW,
    )


# --- the dual write ----------------------------------------------------------


def test_a_normalised_value_is_rewritten_onto_the_seeded_label(session):
    """The case where the SM-P0-09 line actually moves. `door` is what a
    workspace classification record holds; `Doors` is what the catalogue
    facet list offers and what `gs.category ILIKE '%Doors%'` needs."""
    owner = _user(session, "rewrite")
    symbol, revision = _symbol(session, owner, category="door", discipline="piping")
    _propose(session, revision, label="Doors", scheme_code=CATEGORY_SCHEME, method=MAPPING_METHOD)
    _propose(
        session,
        revision,
        label="Piping / P&ID",
        scheme_code=DISCIPLINE_SCHEME,
        method=MAPPING_METHOD,
    )
    session.flush()

    report = sync_legacy_symbol_columns(
        session, symbol=symbol, symbol_revision_id=revision.id, synced_at=NOW
    )

    assert set(report.changed_columns) == {"category", "discipline"}
    assert report.applied is True
    session.refresh(symbol)
    assert symbol.category == "Doors"
    assert symbol.discipline == "Piping / P&ID"


def test_a_seeded_label_is_left_byte_identical(session):
    owner = _user(session, "identical")
    symbol, revision = _symbol(session, owner, category="Pumps", discipline="Process")
    _propose(session, revision, label="Pumps", scheme_code=CATEGORY_SCHEME, method=MAPPING_METHOD)
    _propose(
        session, revision, label="Process", scheme_code=DISCIPLINE_SCHEME, method=MAPPING_METHOD
    )
    session.flush()

    report = sync_legacy_symbol_columns(
        session, symbol=symbol, symbol_revision_id=revision.id, synced_at=NOW
    )

    assert report.changed_columns == ()
    assert report.applied is False
    assert [item.reason for item in report.derivations] == ["value_unchanged", "value_unchanged"]
    session.refresh(symbol)
    assert (symbol.category, symbol.discipline) == ("Pumps", "Process")


def test_a_revision_with_no_assignment_keeps_both_columns(session):
    """No assignment, no derivation, no write. Both columns are
    `nullable=False` and this package never empties one."""
    owner = _user(session, "no-assignment")
    symbol, revision = _symbol(session, owner, category="symbol", discipline="general")

    report = sync_legacy_symbol_columns(
        session, symbol=symbol, symbol_revision_id=revision.id, synced_at=NOW
    )

    assert report.changed_columns == ()
    assert {item.reason for item in report.derivations} == {"no_derivable_assignment"}
    session.refresh(symbol)
    assert (symbol.category, symbol.discipline) == ("symbol", "general")


def test_only_the_facet_with_an_assignment_moves(session):
    owner = _user(session, "one-facet")
    symbol, revision = _symbol(session, owner, category="door", discipline="brontosaurus")
    _propose(session, revision, label="Doors", scheme_code=CATEGORY_SCHEME, method=MAPPING_METHOD)
    session.flush()

    report = sync_legacy_symbol_columns(
        session, symbol=symbol, symbol_revision_id=revision.id, synced_at=NOW
    )

    assert report.changed_columns == ("category",)
    session.refresh(symbol)
    assert symbol.category == "Doors"
    assert symbol.discipline == "brontosaurus"


def test_a_rejected_primary_drives_nothing(session):
    owner = _user(session, "rejected")
    symbol, revision = _symbol(session, owner, category="door", discipline="Process")
    assignment = _propose(
        session, revision, label="Doors", scheme_code=CATEGORY_SCHEME, method=MAPPING_METHOD
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

    report = sync_legacy_symbol_columns(
        session, symbol=symbol, symbol_revision_id=revision.id, synced_at=NOW
    )

    assert report.changed_columns == ()
    session.refresh(symbol)
    assert symbol.category == "door"


def test_a_secondary_assignment_drives_nothing(session):
    owner = _user(session, "secondary")
    symbol, revision = _symbol(session, owner, category="door", discipline="Process")
    _propose(
        session,
        revision,
        label="Drawing Symbols",
        scheme_code=CATEGORY_SCHEME,
        method=MAPPING_METHOD,
        role="secondary",
    )
    session.flush()

    report = sync_legacy_symbol_columns(
        session, symbol=symbol, symbol_revision_id=revision.id, synced_at=NOW
    )

    assert report.changed_columns == ()
    session.refresh(symbol)
    assert symbol.category == "door"


def test_a_verified_primary_outranks_a_proposed_one(session):
    owner = _user(session, "verified-wins")
    symbol, revision = _symbol(session, owner, category="door", discipline="Process")
    _propose(session, revision, label="Doors", scheme_code=CATEGORY_SCHEME, method=MAPPING_METHOD)
    reviewed = _propose(
        session,
        revision,
        label="Valves",
        scheme_code=CATEGORY_SCHEME,
        method="manual",
        at=NOW + timedelta(minutes=1),
    )
    session.flush()
    transition_symbol_revision_classification(
        session,
        assignment_id=reviewed.id,
        target_status="verified",
        occurred_at=NOW + timedelta(minutes=2),
        reviewed_by_user_id=_user(session, "verifier"),
    )
    session.flush()

    chosen = derivable_primary_assignment(
        session,
        symbol_revision_id=revision.id,
        classification_scheme_id=reviewed.classification_scheme_id,
    )
    assert chosen.id == reviewed.id
    assert chosen.status == "verified"

    sync_legacy_symbol_columns(
        session, symbol=symbol, symbol_revision_id=revision.id, synced_at=NOW
    )
    session.refresh(symbol)
    assert symbol.category == "Valves"


def test_a_manual_proposal_outranks_a_backfilled_one(session):
    """Both proposed, so the method decides. A `legacy_backfill` row was
    copied out of the very column being derived, which makes it the weakest
    possible evidence about that column."""
    owner = _user(session, "method-order")
    symbol, revision = _symbol(session, owner, category="door", discipline="Process")
    run_legacy_classification_backfill(
        session, backfilled_at=NOW, apply=True, symbol_slug=symbol.slug
    )
    manual = _propose(
        session,
        revision,
        label="Valves",
        scheme_code=CATEGORY_SCHEME,
        method="manual",
        at=NOW + timedelta(minutes=1),
    )
    session.flush()

    chosen = derivable_primary_assignment(
        session,
        symbol_revision_id=revision.id,
        classification_scheme_id=manual.classification_scheme_id,
    )
    assert chosen.method == "manual"

    sync_legacy_symbol_columns(
        session, symbol=symbol, symbol_revision_id=revision.id, synced_at=NOW
    )
    session.refresh(symbol)
    assert symbol.category == "Valves"


def test_the_backfill_and_the_sync_compose_but_only_the_normalisation_shows(session):
    """The two packages together, end to end, and the coarseness rule with
    them. `door` is matched by the trailing-S rule, so the column normalises
    to `Doors`. `piping` is only reachable through the legacy taxonomy table,
    so it earns its `Piping / P&ID` *assignment* -- which is what makes the
    symbol findable under that facet once M5 is on -- while the column keeps
    `piping`.

    This is the production measurement in miniature: the backfill reports two
    rewrites, and exactly one of them reaches a catalogue field."""
    owner = _user(session, "compose")
    symbol, revision = _symbol(session, owner, category="door", discipline="piping")

    backfill = run_legacy_classification_backfill(
        session, backfilled_at=NOW, apply=True, symbol_slug=symbol.slug
    )
    assert len(backfill.rewrites) == 2
    assert {item.field: item.match_basis for item in backfill.planned} == {
        "category": "plural_variant",
        "discipline": "legacy_taxonomy",
    }

    sync = sync_legacy_symbol_columns(
        session, symbol=symbol, symbol_revision_id=revision.id, synced_at=NOW
    )

    assert sync.changed_columns == ("category",)
    session.refresh(symbol)
    assert (symbol.category, symbol.discipline) == ("Doors", "piping")
    by_column = {item.column: item for item in sync.derivations}
    assert by_column["category"].assignment_method == BACKFILL_METHOD
    assert by_column["discipline"].reason == "coarse_match_basis"
    assert by_column["discipline"].derived_value is None

    # The assignment the column declined to display is still there, governed.
    assignments = backfilled_assignments(session, symbol_revision_id=revision.id)
    assert len(assignments) == 2

    # And it settles: a second round changes nothing.
    again = sync_legacy_symbol_columns(
        session, symbol=symbol, symbol_revision_id=revision.id, synced_at=NOW
    )
    assert again.changed_columns == ()


def test_planning_the_sync_writes_nothing(session):
    owner = _user(session, "plan-only")
    symbol, revision = _symbol(session, owner, category="door", discipline="Process")
    _propose(session, revision, label="Doors", scheme_code=CATEGORY_SCHEME, method=MAPPING_METHOD)
    session.flush()

    derivations = plan_legacy_sync(session, symbol=symbol, symbol_revision_id=revision.id)

    assert [item.derived_value for item in derivations if item.changes] == ["Doors"]
    session.refresh(symbol)
    assert symbol.category == "door"


def test_apply_false_reports_without_writing(session):
    owner = _user(session, "no-apply")
    symbol, revision = _symbol(session, owner, category="door", discipline="Process")
    _propose(session, revision, label="Doors", scheme_code=CATEGORY_SCHEME, method=MAPPING_METHOD)
    session.flush()

    report = sync_legacy_symbol_columns(
        session,
        symbol=symbol,
        symbol_revision_id=revision.id,
        synced_at=NOW,
        apply=False,
    )

    assert report.changed_columns == ("category",)
    assert report.applied is False
    session.refresh(symbol)
    assert symbol.category == "door"


# --- M5: the catalogue read --------------------------------------------------


def _catalogue_sql(*, assignments_enabled: bool, **facets):
    filters, params, _ = catalog_symbol_filters(
        assignments_enabled=assignments_enabled, **{**FILTER_KWARGS, **facets}
    )
    where = (" AND " + " AND ".join(filters)) if filters else ""
    return PUBLISHED_SYMBOLS_SQL + where + " ORDER BY gs.canonical_name LIMIT 5", params


@pytest.mark.parametrize("assignments_enabled", [False, True])
def test_the_composed_catalogue_query_executes(session, assignments_enabled):
    """Where a wrong table name shows up. The assignment table is
    `symbol_revision_classifications`, not the specification's logical
    `symbol_revision_classification_assignments`, which is 42 characters and
    breaks PostgreSQL's identifier limit on its foreign keys."""
    sql, params = _catalogue_sql(
        assignments_enabled=assignments_enabled, discipline="Process", category="Doors"
    )
    rows = session.execute(text(sql), params).all()
    assert rows == []


def test_the_exists_predicate_matches_a_symbol_by_its_assignment(session):
    """The match logic itself, against real rows and without the publication
    machinery: the same `EXISTS` the catalogue filter appends, applied to a
    revision whose legacy column does *not* contain the facet value."""
    owner = _user(session, "m5-match")
    _symbol_with_assignment = _symbol(session, owner, category="door", discipline="Process")
    symbol, revision = _symbol_with_assignment
    _propose(session, revision, label="Doors", scheme_code=CATEGORY_SCHEME, method=MAPPING_METHOD)
    session.flush()

    # The legacy column alone cannot find it: 'door' does not contain 'doors'.
    legacy_only = session.execute(
        text("SELECT 1 FROM governed_symbols gs WHERE gs.id = :id AND gs.category ILIKE :category"),
        {"id": symbol.id, "category": "%Doors%"},
    ).all()
    assert legacy_only == []

    matched = session.execute(
        text(
            """
            SELECT 1 FROM symbol_revisions sr
            WHERE sr.id = :revision_id
              AND EXISTS (
                    SELECT 1
                    FROM symbol_revision_classifications src
                    JOIN classification_nodes cn ON cn.id = src.classification_node_id
                    JOIN classification_schemes cs ON cs.id = src.classification_scheme_id
                    WHERE src.symbol_revision_id = sr.id
                      AND src.assignment_role = 'primary'
                      AND src.status IN ('verified', 'proposed')
                      AND cs.scheme_code = :category_scheme
                      AND cn.preferred_label ILIKE :category
              )
            """
        ),
        {
            "revision_id": revision.id,
            "category_scheme": CATEGORY_SCHEME,
            "category": "%Doors%",
        },
    ).all()
    assert len(matched) == 1


def test_the_exists_predicate_refuses_a_rejected_assignment(session):
    owner = _user(session, "m5-rejected")
    _symbol_pair = _symbol(session, owner, category="door", discipline="Process")
    _symbol_row, revision = _symbol_pair
    assignment = _propose(
        session, revision, label="Doors", scheme_code=CATEGORY_SCHEME, method=MAPPING_METHOD
    )
    session.flush()
    transition_symbol_revision_classification(
        session,
        assignment_id=assignment.id,
        target_status="rejected",
        occurred_at=NOW + timedelta(minutes=1),
        reviewed_by_user_id=_user(session, "m5-rejector"),
    )
    session.flush()

    matched = session.execute(
        text(
            """
            SELECT 1 FROM symbol_revisions sr
            WHERE sr.id = :revision_id
              AND EXISTS (
                    SELECT 1
                    FROM symbol_revision_classifications src
                    JOIN classification_nodes cn ON cn.id = src.classification_node_id
                    JOIN classification_schemes cs ON cs.id = src.classification_scheme_id
                    WHERE src.symbol_revision_id = sr.id
                      AND src.assignment_role = 'primary'
                      AND src.status IN ('verified', 'proposed')
                      AND cs.scheme_code = :category_scheme
                      AND cn.preferred_label ILIKE :category
              )
            """
        ),
        {
            "revision_id": revision.id,
            "category_scheme": CATEGORY_SCHEME,
            "category": "%Doors%",
        },
    ).all()
    assert matched == []


# --- the coarseness rule against real rows -----------------------------------


def test_a_coarse_assignment_is_created_but_does_not_touch_the_column(session):
    """The `Cylinder` case, which is 19 of the 96 production symbols. The
    legacy taxonomy table folds `Cylinder` into `Equipment` for browse
    bucketing; writing that into the durable column would destroy a
    distinction an operator recorded."""
    owner = _user(session, "coarse")
    symbol, revision = _symbol(session, owner, category="Cylinder", discipline="Process")

    backfill = run_legacy_classification_backfill(
        session, backfilled_at=NOW, apply=True, symbol_slug=symbol.slug
    )
    planned = {item.field: item for item in backfill.planned}
    assert planned["category"].node_label == "Equipment"
    assert planned["category"].match_basis == "legacy_taxonomy"

    report = sync_legacy_symbol_columns(
        session, symbol=symbol, symbol_revision_id=revision.id, synced_at=NOW
    )

    assert report.changed_columns == ()
    by_column = {item.column: item for item in report.derivations}
    assert by_column["category"].reason == "coarse_match_basis"
    assert by_column["discipline"].reason == "value_unchanged"
    session.refresh(symbol)
    assert symbol.category == "Cylinder"

    # The governed assignment exists regardless -- M5 finds the symbol under
    # the Equipment facet through it, which is the point.
    assignment = backfilled_assignments(session, symbol_revision_id=revision.id)
    nodes = {
        session.get(ClassificationNode, item.classification_node_id).preferred_label
        for item in assignment
    }
    assert "Equipment" in nodes


def test_a_finer_eligible_assignment_still_wins_over_a_coarse_one(session):
    """Filtered before ranking, so a coarse row that outranks by method does
    not block a finer one from displaying."""
    owner = _user(session, "coarse-vs-fine")
    symbol, revision = _symbol(session, owner, category="Cylinder", discipline="Process")
    # A coarse source_mapping row, which outranks legacy_backfill by method...
    propose_symbol_revision_classification(
        session,
        symbol_revision_id=revision.id,
        classification_node_id=_node(
            session, scheme_code=CATEGORY_SCHEME, label="Equipment"
        ).id,
        assignment_role="primary",
        method=MAPPING_METHOD,
        proposed_at=NOW,
        evidence={"match_basis": "legacy_taxonomy"},
    )
    # ...and a finer backfilled one, which is eligible to display.
    propose_symbol_revision_classification(
        session,
        symbol_revision_id=revision.id,
        classification_node_id=_node(session, scheme_code=CATEGORY_SCHEME, label="Valves").id,
        assignment_role="primary",
        method=BACKFILL_METHOD,
        proposed_at=NOW,
        evidence={"match_basis": "exact"},
    )
    session.flush()

    chosen = derivable_primary_assignment(
        session,
        symbol_revision_id=revision.id,
        classification_scheme_id=_node(
            session, scheme_code=CATEGORY_SCHEME, label="Valves"
        ).scheme_id,
    )
    assert chosen.evidence_json["match_basis"] == "exact"

    sync_legacy_symbol_columns(
        session, symbol=symbol, symbol_revision_id=revision.id, synced_at=NOW
    )
    session.refresh(symbol)
    assert symbol.category == "Valves"


def test_no_primary_at_all_is_reported_differently_from_a_coarse_one(session):
    owner = _user(session, "reasons")
    bare, bare_revision = _symbol(session, owner, category="door", discipline="Process")
    report = sync_legacy_symbol_columns(
        session, symbol=bare, symbol_revision_id=bare_revision.id, synced_at=NOW
    )
    assert {item.column: item.reason for item in report.derivations}["category"] == (
        "no_derivable_assignment"
    )

    coarse, coarse_revision = _symbol(session, owner, category="Cylinder", discipline="Process")
    propose_symbol_revision_classification(
        session,
        symbol_revision_id=coarse_revision.id,
        classification_node_id=_node(
            session, scheme_code=CATEGORY_SCHEME, label="Equipment"
        ).id,
        assignment_role="primary",
        method=MAPPING_METHOD,
        proposed_at=NOW,
        evidence={"match_basis": "legacy_taxonomy"},
    )
    session.flush()
    report = sync_legacy_symbol_columns(
        session, symbol=coarse, symbol_revision_id=coarse_revision.id, synced_at=NOW
    )
    assert {item.column: item.reason for item in report.derivations}["category"] == (
        "coarse_match_basis"
    )
