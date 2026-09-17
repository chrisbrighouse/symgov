"""The section 14.2 tenant matrix's completeness guard (SM-P1-01 WP1.6).

The live sweep lives in `test_semantic_review_tenant_matrix_postgresql.py`,
because a tenant predicate can only be *exercised* against real joined rows.
This file is the other half: the guard that makes the sweep's coverage
**exhaustive rather than sampled**, and it is deliberately DB-free so it runs
even where PostgreSQL does not.

Three existing files already prove the section 14.2 rule on a handful of
routes (`test_semantic_review_queries_postgresql.py`,
`test_semantic_review_routes_postgresql.py`,
`test_rights_review_routes_postgresql.py`). None of them can fail when
somebody adds a *new* symbol-scoped route and forgets the predicate, because
each names the routes it probes. That is the gap this file closes.

**The router carries twenty route decorators, not eighteen.**
`SEMANTIC_REVIEW_ROUTES` in `test_semantic_review_routes.py` holds eighteen
-- seventeen from SM-P1-01 plus WP3.1's concept-classification decision --
and `RIGHTS_REVIEW_ROUTES` in `test_rights_review_routes.py` holds the other
two; WP1.3's rights writes share this router but have their own policy
matrix. Prose in the implementation plan says "sixteenth route" and
"seventeenth route" -- both count entries in that first matrix, not
decorators. This file counts decorators, off the router object itself.

**Every route is in exactly one of two tables.** `TENANT_SCOPED` names the
ten routes that resolve a symbol-scoped row and the predicate each must use;
`UNSCOPED_BY_DECISION` names the ten that carry no tenant predicate *by
decision*, each with the argument for why. The union must equal the router's
own route set, so a new route fails this file until somebody classifies it --
which is the point. "Unscoped" is then always a recorded decision and never
an omission.

**Why a source check rather than a behavioural one.** A behavioural probe
needs a foreign row to exist, which needs a migrated database, which is the
PostgreSQL file's job. What is checked here is narrower and complementary: a
route declared scoped must actually *call* one of the four resolvers. A route
that quietly stopped calling one would still pass every hand-written probe
that does not happen to name it; it cannot pass this.
"""

from __future__ import annotations

import inspect

import pytest

from symgov_backend.routes.semantic_review import router

# The four ways this router resolves the section 14.2 predicate. `_scope`
# turns the session into an organisation id or `None` (public-only); the
# other three re-resolve a named row through it and report an out-of-scope
# row as absent rather than forbidden.
SCOPE_RESOLVERS = (
    "organization_id=_scope(",
    "_visible_revision(",
    "_visible_symbol_row(",
    "_visible_rights_record(",
)

# Routes that resolve a symbol-scoped row, and the resolver each must use.
# The PostgreSQL sweep probes every one of these with a foreign row and
# asserts 404-never-403; `test_the_sweep_probes_every_tenant_scoped_route`
# there pins that the two lists agree.
TENANT_SCOPED = {
    ("GET", "/semantic-review/queues/symbol-classifications"): "organization_id=_scope(",
    ("GET", "/semantic-review/queues/symbol-semantic-assignments"): "organization_id=_scope(",
    ("GET", "/semantic-review/queues/rights-records"): "organization_id=_scope(",
    ("GET", "/semantic-review/symbol-revisions/{symbol_revision_id}"): "_visible_revision(",
    (
        "POST",
        "/semantic-review/symbol-revisions/{symbol_revision_id}/semantic-assignments",
    ): "_visible_revision(",
    ("POST", "/semantic-review/semantic-assignments/{assignment_id}/decision"): "_visible_symbol_row(",
    (
        "POST",
        "/semantic-review/symbol-revisions/{symbol_revision_id}/classifications",
    ): "_visible_revision(",
    ("POST", "/semantic-review/symbol-classifications/{assignment_id}/decision"): "_visible_symbol_row(",
    # Conditionally scoped, and the condition is the subject: a
    # symbol-subject record resolves through `_visible_revision`, while a
    # package- or standard-subject record names no symbol and is a
    # platform-level assertion about a licence (WP1.3).
    ("POST", "/semantic-review/rights-records"): "_visible_revision(",
    ("POST", "/semantic-review/rights-records/{record_id}/decision"): "_visible_rights_record(",
}

# The closed list. Each entry states why the route carries no tenant
# predicate; section 14.2 is about organisation-private *symbol existence*,
# and where there is no symbol there is nothing for it to protect.
UNSCOPED_BY_DECISION = {
    ("GET", "/semantic-review/queues/concept-classifications"): (
        "a concept-to-node assertion names no symbol; section 17 made concept "
        "governance platform-level (WP1.1)"
    ),
    ("GET", "/semantic-review/queues/concept-external-mappings"): (
        "a concept-to-external-release assertion names no symbol (WP1.1)"
    ),
    ("GET", "/semantic-review/classification-schemes"): (
        "seeded platform reference data naming no symbol"
    ),
    ("GET", "/semantic-review/review-cases/{review_case_id}/classification-preview"): (
        "a review case names no symbol and no organisation -- neither ReviewCase "
        "nor ClassificationRecord nor IntakeRecord carries one, and the intake "
        "lane feeds the public catalog (WP1.5, decision Q9)"
    ),
    ("POST", "/semantic-review/concepts"): (
        "concept lifecycle is platform-admin only and concepts are platform-level "
        "(decision Q2, section 17)"
    ),
    ("POST", "/semantic-review/concepts/{concept_id}/revisions"): (
        "concept lifecycle is platform-admin only and concepts are platform-level "
        "(decision Q2, section 17)"
    ),
    ("POST", "/semantic-review/concept-revisions/{revision_id}/transition"): (
        "concept lifecycle is platform-admin only and concepts are platform-level "
        "(decision Q2, section 17)"
    ),
    ("POST", "/semantic-review/concept-classifications/{assignment_id}/decision"): (
        "a concept-to-node assertion names no symbol; the same argument WP1.1 "
        "made for the queue this decides on (WP3.1)"
    ),
    ("POST", "/semantic-review/concepts/{concept_id}/external-mappings"): (
        "a mapping hangs off a concept, which has no private existence to leak"
    ),
    ("POST", "/semantic-review/external-mappings/{reference_id}/decision"): (
        "a mapping hangs off a concept, which has no private existence to leak"
    ),
}


def _router_routes():
    """The decorators, off the router object rather than off a hand list."""
    return {
        (method, route.path)
        for route in router.routes
        for method in sorted(route.methods - {"HEAD", "OPTIONS"})
    }


def _source(method, path):
    for route in router.routes:
        if route.path == path and method in route.methods:
            return inspect.getsource(route.endpoint)
    raise AssertionError(f"{method} {path} is not on the router")


def test_the_router_carries_twenty_routes():
    """Counted off the decorators, which is the only total that is a fact.

    Eighteen of them are in `SEMANTIC_REVIEW_ROUTES` -- seventeen from
    SM-P1-01, plus WP3.1's concept-classification decision, which was added
    to that same matrix -- and two, WP1.3's rights writes, are in
    `RIGHTS_REVIEW_ROUTES`. A change to this number is a change to the
    surface section 14.2 governs, and should be noticed.
    """
    assert len(_router_routes()) == 20


def test_every_route_is_classified_as_scoped_or_unscoped_by_decision():
    """The guard that makes the sweep exhaustive.

    A new symbol-scoped route added without a predicate fails here before it
    can reach production: it is in neither table, so the union no longer
    equals the router. Putting it in `UNSCOPED_BY_DECISION` is possible, but
    it costs the author a written argument sitting beside nine others -- the
    difference between a decision and an omission.
    """
    classified = set(TENANT_SCOPED) | set(UNSCOPED_BY_DECISION)
    routes = _router_routes()

    assert routes - classified == set(), "an unclassified route: scoped, or unscoped by what argument?"
    assert classified - routes == set(), "the matrix names a route the router does not have"


def test_the_two_tables_are_disjoint():
    assert set(TENANT_SCOPED) & set(UNSCOPED_BY_DECISION) == set()


@pytest.mark.parametrize("route,resolver", sorted(TENANT_SCOPED.items()))
def test_every_tenant_scoped_route_resolves_its_scope(route, resolver):
    """Declaring a route scoped is not the same as scoping it.

    The handler must actually call the resolver the matrix claims for it. A
    route that quietly stopped calling one would still pass every existing
    probe that does not happen to name it.
    """
    method, path = route
    source = _source(method, path)

    assert resolver in source, f"{method} {path} does not call {resolver}"


@pytest.mark.parametrize("route,reason", sorted(UNSCOPED_BY_DECISION.items()))
def test_every_unscoped_route_records_its_argument(route, reason):
    assert reason.strip(), f"{route} is unscoped with no argument recorded"


@pytest.mark.parametrize("route", sorted(UNSCOPED_BY_DECISION))
def test_no_unscoped_route_silently_acquired_a_predicate(route):
    """The complement, and it is about the record rather than about safety.

    A predicate appearing on one of these would be a tightening, not a leak.
    But the recorded argument would then be stale -- the route would be
    scoped while the matrix still said it names no symbol -- and a stale
    argument is how "unscoped by decision" decays back into "unscoped by
    accident".
    """
    method, path = route
    source = _source(method, path)

    found = [resolver for resolver in SCOPE_RESOLVERS if resolver in source]

    assert found == [], f"{method} {path} now calls {found}; move it to TENANT_SCOPED"


def test_platform_admin_is_not_a_bypass_in_the_scope_resolver():
    """Decision WP1.2, and the assertion section 14.2 actually needs.

    `_scope` reads `active_organization_id` and nothing else, so the most
    privileged principal in the product is scoped exactly like any other --
    and a personal-mode session is public-only whatever the role. The
    PostgreSQL sweep proves the consequence on real rows; this pins that no
    role name reaches the function that would have to grant the exemption.
    """
    source = inspect.getsource(router.routes[0].endpoint.__globals__["_scope"])

    assert "active_organization_id" in source
    assert "platform_admin" not in source
    assert "roles" not in source
