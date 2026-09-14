"""Route-policy cover for SM-P1-01 WP1.2: the semantic review API.

Scope note: this file is the *policy* matrix -- the feature flag, the mount
surface, authentication, role boundaries and the validation envelope. It runs
in the portable partition, so it deliberately proves only what can be proved
without a migrated database: every assertion here is settled by a router
dependency or the application factory before a handler ever touches a table.

The governance acts themselves -- proposing, verifying, the section 14.2
tenant boundary on real joined rows -- need seeded schemes, nodes and
`governed_symbols`, and live in `test_semantic_review_routes_postgresql.py`.

Two facts this file exists to pin, both of which a later change could quietly
break:

* The router is **v1-only** (decision Q7, 2026-09-13). Every router added
  since Stage 4 is mounted once at `settings.api_prefix`; the six legacy
  `/api` routers are pre-Stage-4 surfaces kept for existing clients. A legacy
  twin here would be a compatibility surface for zero callers.
* A disabled feature answers **404, not 403** -- the `symbol_demotion`
  pattern. A 403 would advertise the existence of a surface that is off.
"""

from __future__ import annotations

import re
import sys
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_auth_context import (  # noqa: E402
    _add_membership,
    _build_client,
    _login,
)

from symgov_backend.app import create_app  # noqa: E402
from symgov_backend.models import UserRole  # noqa: E402
from symgov_backend.settings import SymgovAPISettings, get_settings  # noqa: E402

SETTINGS_SOURCE = (
    Path(__file__).resolve().parents[1] / "backend" / "symgov_backend" / "settings.py"
).read_text()


# The tables the semantic review API reads and writes. `_build_client` creates
# only the authentication/organization set, and the portable partition is
# SQLite, so these are created here with the same two adaptations the other
# portable route suites use: JSONB becomes JSON, and the CHECK constraints
# written in PostgreSQL dialect are dropped for the duration of the DDL.
#
# Dropping those CHECKs is not a weakening of what this file proves. The
# constraints they carry -- section 12.3's `backfill_not_verified` above all --
# are database invariants, and database invariants are proved against a real
# server in `test_semantic_review_routes_postgresql.py`. What this file
# proves is route policy, which is settled before any row is written.
_SEMANTIC_MODELS = (
    # WP1.5 only: the forecast route resolves the review case before it
    # reaches any semantic table, so an absent case must be answerable as
    # 404 rather than as a missing relation.
    "ReviewCase",
    "ReviewSplitItem",
    "ClassificationRecord",
    "ReviewSymbolProperty",
    "CatalogSymbolIdentifier",
    "GovernedSymbol",
    "SymbolRevision",
    "ClassificationScheme",
    "ClassificationNode",
    "SemanticConcept",
    "SemanticConceptRevision",
    "SymbolSemanticAssignment",
    "SymbolRevisionClassificationAssignment",
    "ConceptClassificationAssignment",
    "ExternalSemanticScheme",
    "ExternalSemanticSchemeVersion",
    "ConceptExternalReference",
    "RightsRecord",
)


def _create_semantic_tables(engine) -> None:
    from sqlalchemy import JSON
    from sqlalchemy import CheckConstraint as _Check
    import symgov_backend.models as models

    for name in _SEMANTIC_MODELS:
        table = getattr(models, name).__table__
        original = table.constraints
        original_types = {column.name: column.type for column in table.columns}
        original_defaults = {column.name: column.server_default for column in table.columns}
        try:
            for column in table.columns:
                if column.type.__class__.__name__ == "JSONB":
                    column.type = JSON()
                    column.server_default = None
            table.constraints = {
                item
                for item in original
                if not (
                    isinstance(item, _Check)
                    and any(
                        token in str(item.sqltext)
                        for token in ("~", "jsonb", "char_length", "::", "convert_to")
                    )
                )
            }
            table.create(engine, checkfirst=True)
        finally:
            table.constraints = original
            for column in table.columns:
                column.type = original_types[column.name]
                column.server_default = original_defaults[column.name]


def _concrete(template: str) -> str:
    """Fill a path template with a syntactically valid but absent identifier.

    The policy assertions in this file settle before a handler resolves the
    resource, so the identifier only has to parse. It must still be a real
    UUID: a route that rejects a malformed one would answer 422 and hide the
    401/403/404 the test is actually about.
    """
    return re.sub(r"\{[^}]+\}", lambda _: str(uuid.uuid4()), template)


def _semantic_client(*, enabled=True, roles=("reviewer",), platform_admin=False, login=True):
    """An authenticated client with an exact role set and the flag as given.

    Roles are written before login deliberately: the session is issued against
    the roles that exist at that moment, so granting one afterwards would test
    a principal the application never saw.
    """
    client, Session, user_id, settings = _build_client(
        enabled=True, pilots=("acme", "symgov"), platform_admin_enabled=True
    )
    _create_semantic_tables(Session.kw["bind"])
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        session.query(UserRole).filter(UserRole.user_id == user_id).delete()
        for role in roles:
            session.add(UserRole(user_id=user_id, role=role, created_at=now))
        session.commit()
    # Platform Admin is not a bare role assignment: it is an active
    # `platform_admin` assignment *plus* an `admin` base role in the `symgov`
    # organization itself (`organization_authorization`). Binding to `acme`
    # with the assignment attached would silently produce an ordinary member.
    _add_membership(
        Session,
        user_id,
        "symgov" if platform_admin else "acme",
        base_role="admin",
        platform_admin=platform_admin,
    )

    client.app.dependency_overrides[get_settings] = lambda: replace(
        settings, semantic_review_enabled=enabled
    )
    if login:
        _login(client)
    return client, Session, user_id, settings


def test_the_semantic_review_flag_exists_and_defaults_off():
    """Decision Q3: `SYMGOV_SEMANTIC_REVIEW_ENABLED`, default off.

    Same literal boolean-parse form as the other I-20 flags, evaluated at
    import, so activation needs a process restart -- a separate operation
    with its own approval, and not part of any work package here.
    """
    assert SymgovAPISettings().semantic_review_enabled is False


def test_the_flag_is_declared_in_the_same_literal_form_as_every_other_i20_flag():
    """The form is the contract, because the value is read exactly once.

    These flags are dataclass field defaults, so `os.environ` is consulted
    when the class body executes and never again -- which is what makes
    activation a process restart rather than a runtime toggle. A test that
    set the variable and re-instantiated would therefore pass only if
    somebody had changed that property. So pin the declaration instead: the
    same truthy vocabulary as its eight neighbours, and no `default_factory`
    that would quietly make it re-readable.
    """
    declarations = re.findall(
        r'(\w+): bool = os\.environ\.get\(\s*"(SYMGOV_\w+)",\s*""\s*\)\.strip\(\)\.lower\(\) in \{([^}]*)\}',
        SETTINGS_SOURCE,
    )
    by_field = {field: (variable, {token.strip().strip('"') for token in raw.split(",") if token.strip()})
                for field, variable, raw in declarations}

    assert "semantic_review_enabled" in by_field, "the flag must use the shared literal form"
    variable, vocabulary = by_field["semantic_review_enabled"]

    assert variable == "SYMGOV_SEMANTIC_REVIEW_ENABLED"
    assert vocabulary == {"1", "true", "yes", "on"}
    assert all(other == vocabulary for _, other in by_field.values()), by_field
    assert len(by_field) >= 9, "expected the eight pre-existing I-20 flags plus this one"


def test_auth_me_exposes_the_capability_so_the_frontend_never_guesses():
    client, Session, user_id, settings = _build_client(enabled=True, pilots=("acme",))
    _add_membership(Session, user_id, "acme", base_role="admin")
    _login(client)

    capabilities = client.get("/api/v1/auth/me").json()["user"]["capabilities"]

    assert capabilities["semanticReviewEnabled"] is False

    client.app.dependency_overrides[get_settings] = lambda: replace(settings, semantic_review_enabled=True)
    capabilities = client.get("/api/v1/auth/me").json()["user"]["capabilities"]

    assert capabilities["semanticReviewEnabled"] is True


# The complete WP1.2 surface, as (method, template, policy). WP1.3 adds the
# two rights write routes; the rights *queue* read is WP1.1's fifth queue and
# belongs here. `policy` is the authorization boundary decision Q2 settled:
# concept lifecycle is platform-level (section 17), every other act is open to
# the roles the existing Reviews surface already uses.
SEMANTIC_REVIEW_ROUTES = (
    ("GET", "/semantic-review/queues/symbol-classifications", "reviewer_admin"),
    ("GET", "/semantic-review/queues/symbol-semantic-assignments", "reviewer_admin"),
    ("GET", "/semantic-review/queues/concept-classifications", "reviewer_admin"),
    ("GET", "/semantic-review/queues/concept-external-mappings", "reviewer_admin"),
    ("GET", "/semantic-review/queues/rights-records", "reviewer_admin"),
    ("GET", "/semantic-review/symbol-revisions/{symbol_revision_id}", "reviewer_admin"),
    ("POST", "/semantic-review/concepts", "platform_admin"),
    ("POST", "/semantic-review/concepts/{concept_id}/revisions", "platform_admin"),
    ("POST", "/semantic-review/concept-revisions/{revision_id}/transition", "platform_admin"),
    ("POST", "/semantic-review/symbol-revisions/{symbol_revision_id}/semantic-assignments", "reviewer_admin"),
    ("POST", "/semantic-review/semantic-assignments/{assignment_id}/decision", "reviewer_admin"),
    ("POST", "/semantic-review/symbol-revisions/{symbol_revision_id}/classifications", "reviewer_admin"),
    ("POST", "/semantic-review/symbol-classifications/{assignment_id}/decision", "reviewer_admin"),
    ("POST", "/semantic-review/concepts/{concept_id}/external-mappings", "reviewer_admin"),
    ("POST", "/semantic-review/external-mappings/{reference_id}/decision", "reviewer_admin"),
    ("GET", "/semantic-review/classification-schemes", "reviewer_admin"),
    # WP1.5, decision Q9: the approval forecast on the intake review pane.
    (
        "GET",
        "/semantic-review/review-cases/{review_case_id}/classification-preview",
        "reviewer_admin",
    ),
)

V1_PREFIX = "/api/v1"


def _mounted(app):
    """The generated contract, not the decorators.

    `app.routes` holds lazy `_IncludedRouter` objects in this FastAPI
    version, so it exposes no flat paths at all -- a mount assertion written
    against it passes vacuously whether or not the router is registered.
    `openapi()` is the resolved surface, and it is the same document a client
    generator consumes.
    """
    return {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }


def test_every_route_is_mounted_once_at_the_v1_prefix():
    mounted = _mounted(create_app())

    for method, template, _ in SEMANTIC_REVIEW_ROUTES:
        assert (method, f"{V1_PREFIX}{template}") in mounted, f"{method} {template} is not mounted at v1"


def test_no_route_has_a_legacy_api_twin():
    """Decision Q7, 2026-09-13: v1-only.

    Every router added since Stage 4 is mounted once at `settings.api_prefix`.
    The six legacy `/api` routers are pre-Stage-4 surfaces kept for existing
    clients; WP1.4's frontend is this API's first consumer, so a legacy mount
    would be a compatibility surface for zero callers and would double the
    route-policy matrix.
    """
    mounted = _mounted(create_app())

    twins = [(method, f"/api{template}") for method, template, _ in SEMANTIC_REVIEW_ROUTES
             if (method, f"/api{template}") in mounted]

    assert twins == []


def test_the_surface_does_not_exist_at_all_while_the_flag_is_off():
    """A disabled feature answers 404, not 403 -- the `symbol_demotion` rule.

    A 403 would confirm that the surface exists and is merely forbidden,
    which advertises an unreleased feature to anyone who probes for it.
    """
    client, Session, user_id, _ = _semantic_client(enabled=False, roles=("admin",))

    for method, template, _ in SEMANTIC_REVIEW_ROUTES:
        response = client.request(method, f"{V1_PREFIX}{_concrete(template)}", json={})
        assert response.status_code == 404, f"{method} {template} -> {response.status_code}"
        assert response.json()["error"] == "not_found"


def _probe(client, method, template, **kwargs):
    return client.request(method, f"{V1_PREFIX}{_concrete(template)}", json={}, **kwargs)


# The five queue reads cannot execute on SQLite. WP1.1's concept display name
# is a correlated subquery whose ORDER BY references the outer
# `semantic_concepts` row, which SQLite will not resolve -- the same reason
# `test_semantic_review_queries.py` is deliberately DB-free and every
# execution assertion for these queries lives in the PostgreSQL file.
#
# Stubbing them keeps this file on the boundary it is for. Authentication,
# the role dependencies, the flag guard and the application factory are all
# real; only the query WP1.1 already owns 65 tests for is replaced, and it is
# replaced with the emptiest possible answer so nothing here can pass because
# of fixture data.
_QUEUE_FUNCTIONS = (
    "list_open_symbol_revision_classifications",
    "list_open_symbol_semantic_assignments",
    "list_open_concept_classifications",
    "list_open_concept_external_references",
    "list_open_rights_records",
)


def _stub_queues(monkeypatch):
    import symgov_backend.routes.semantic_review as route_module

    for name in _QUEUE_FUNCTIONS:
        monkeypatch.setattr(route_module, name, lambda *a, **k: [])


@pytest.mark.parametrize("method,template,policy", SEMANTIC_REVIEW_ROUTES)
def test_an_unauthenticated_caller_is_refused_on_every_route(method, template, policy):
    client, _Session, _user_id, _settings = _semantic_client(roles=("admin",), login=False)

    response = _probe(client, method, template)

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "Authentication required."


@pytest.mark.parametrize("method,template,policy", SEMANTIC_REVIEW_ROUTES)
def test_a_role_outside_the_boundary_is_refused_on_every_route(method, template, policy):
    """`submitter` is a real product role and holds neither boundary.

    It is the role `_build_client` issues by default, so this also proves the
    router is not simply inheriting whatever the harness happens to grant.
    """
    client, _Session, _user_id, _settings = _semantic_client(roles=("submitter",))

    response = _probe(client, method, template)

    assert response.status_code == 403, response.text
    # Both boundaries refuse, and each says which one it is. The concept
    # routes are refused by `require_platform_admin` before the role check is
    # ever reached, so they carry its message rather than the role one.
    expected = (
        "Platform Admin privileges are required."
        if policy == "platform_admin"
        else "Insufficient role for this operation."
    )
    assert response.json()["detail"] == expected


@pytest.mark.parametrize("method,template,policy", SEMANTIC_REVIEW_ROUTES)
def test_a_reviewer_may_act_on_everything_except_concept_lifecycle(monkeypatch, method, template, policy):
    """Decision Q2: concept governance is platform-level (section 17).

    A reviewer decides on assignments, classifications and mappings -- the
    same boundary the existing Reviews surface uses -- but may not create or
    move a concept.
    """
    _stub_queues(monkeypatch)
    client, _Session, _user_id, _settings = _semantic_client(roles=("reviewer",))

    response = _probe(client, method, template)

    if policy == "platform_admin":
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "Platform Admin privileges are required."
    else:
        assert response.status_code not in (401, 403), response.text


@pytest.mark.parametrize("method,template,policy", SEMANTIC_REVIEW_ROUTES)
def test_a_platform_admin_passes_every_authorization_boundary(monkeypatch, method, template, policy):
    _stub_queues(monkeypatch)
    client, _Session, _user_id, _settings = _semantic_client(roles=("admin",), platform_admin=True)

    response = _probe(client, method, template)

    assert response.status_code not in (401, 403), response.text


def _operation(document, method, template):
    return document["paths"][f"{V1_PREFIX}{template}"][method.lower()]


def _ref(operation, status):
    content = operation["responses"][str(status)].get("content", {})
    return content.get("application/json", {}).get("schema", {}).get("$ref")


@pytest.mark.parametrize("method,template,policy", SEMANTIC_REVIEW_ROUTES)
def test_every_operation_declares_the_projects_own_validation_envelope(method, template, policy):
    """FastAPI's generated `HTTPValidationError` is a contract defect here.

    `app.py` installs a `RequestValidationError` handler that answers
    `{"error": "validation_error", "detail": ..., "issues": [...]}` -- which is
    `APIValidationErrorResponse`, not the `{"detail": [...]}` FastAPI
    documents by default. A client generated from an undeclared operation
    would be typed against a body the server never sends, so this is checked
    against the generated document rather than by reading the decorators.
    """
    operation = _operation(create_app().openapi(), method, template)

    assert _ref(operation, 422) == "#/components/schemas/APIValidationErrorResponse", operation["responses"]["422"]


@pytest.mark.parametrize("method,template,policy", SEMANTIC_REVIEW_ROUTES)
@pytest.mark.parametrize("status", (401, 403, 404))
def test_every_operation_declares_each_error_status_it_can_return(status, method, template, policy):
    """Each of these is reachable on every route in this router.

    401 and 403 come from the shared dependencies, and 404 from either the
    flag guard or a row outside the caller's tenant scope -- the section 14.2
    substitution that makes an out-of-scope row indistinguishable from an
    absent one.
    """
    operation = _operation(create_app().openapi(), method, template)

    assert _ref(operation, status) == "#/components/schemas/APIErrorResponse", operation["responses"]


def test_the_success_status_is_201_exactly_where_a_row_is_created():
    document = create_app().openapi()
    created = {
        (method, template)
        for method, template, _ in SEMANTIC_REVIEW_ROUTES
        if "201" in _operation(document, method, template)["responses"]
    }

    assert created == {
        ("POST", "/semantic-review/concepts"),
        ("POST", "/semantic-review/concepts/{concept_id}/revisions"),
        ("POST", "/semantic-review/symbol-revisions/{symbol_revision_id}/semantic-assignments"),
        ("POST", "/semantic-review/symbol-revisions/{symbol_revision_id}/classifications"),
        ("POST", "/semantic-review/concepts/{concept_id}/external-mappings"),
    }


def test_no_request_body_accepts_an_unknown_field():
    """`extra="forbid"` on every request model, matching the house shape.

    A silently ignored field is how a frontend ends up believing it sent a
    decision reason that was never stored.
    """
    document = create_app().openapi()
    bodies = set()
    for method, template, _ in SEMANTIC_REVIEW_ROUTES:
        operation = _operation(document, method, template)
        body = operation.get("requestBody")
        if body:
            bodies.add(body["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1])

    assert bodies, "expected the write routes to declare request bodies"
    for name in bodies:
        assert document["components"]["schemas"][name].get("additionalProperties") is False, name


# ---------------------------------------------------------------------------
# SM-P1-01 WP1.2 amendment (2026-09-14): making section 12.3's remedy
# reachable.
#
# `test_a_reviewer_rejects_a_backfilled_row_then_proposes_afresh` already
# proves the API can carry out "reject it and propose afresh with a real
# method" -- but it proves it using a node id the *fixture* holds. No read on
# this surface returned one, so no client could take the second step. These
# two close that, and nothing else about the delivered contract changes.
# ---------------------------------------------------------------------------


def test_a_classification_row_names_the_node_a_reproposal_needs():
    """Without this the queue's own `mustRepropose` advice is unfollowable.

    The row already carries `schemeCode`, `nodeCode` and `nodeLabel` -- all
    display values. `propose_symbol_revision_classification` takes a
    `classificationNodeId`, and a client that cannot obtain one can reject a
    backfilled assignment but never replace it.
    """
    schemas = create_app().openapi()["components"]["schemas"]

    row = schemas["SymbolClassificationReviewRowResponse"]["properties"]

    assert "classificationNodeId" in row, sorted(row)
    # The display fields stay: `CLAUDE.md` keeps the human-readable label
    # prominent and the identifier is a transport key, never the label.
    for display_field in ("schemeCode", "nodeCode", "nodeLabel"):
        assert display_field in row, sorted(row)


def test_the_scheme_read_offers_only_the_schemes_a_reviewer_may_assign_into():
    """Decision Q6, enforced at the picker as well as at the proposal.

    `USE-CASE`, `DOCUMENT-TYPE` and `REPRESENTATION-TYPE` stay read-only in
    v1. The propose route already refuses them with the validation envelope;
    listing them as choosable would invite a refusal the caller could have
    been spared.
    """
    from symgov_backend.routes.semantic_review import REVIEWER_ASSIGNABLE_SCHEME_CODES

    assert REVIEWER_ASSIGNABLE_SCHEME_CODES == frozenset(
        {"ENGINEERING-DISCIPLINE", "SYMBOL-CATEGORY-FAMILY"}
    )

    schemas = create_app().openapi()["components"]["schemas"]

    assert "ClassificationSchemeOptionsResponse" in schemas, sorted(schemas)
    assert "items" in schemas["ClassificationSchemeOptionsResponse"]["properties"]


# --- WP1.5: the approval forecast ----------------------------------------


def test_an_absent_review_case_is_reported_absent_not_as_a_broken_query():
    """The forecast route resolves the case before anything else.

    A review case is not a symbol, so section 14.2 has nothing to scope here
    (`ReviewCase` names no organisation, and neither does `ClassificationRecord`
    or `IntakeRecord`); the 404 is plain absence rather than the tenant
    substitution the symbol routes make.
    """
    client, _Session, _user_id, _settings = _semantic_client(roles=("reviewer",))

    response = client.get(
        f"{V1_PREFIX}/semantic-review/review-cases/{uuid.uuid4()}/classification-preview"
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Review case was not found."


def test_the_forecast_route_carries_no_tenant_predicate_and_says_why():
    """Measured rather than assumed, and the argument is the reason.

    Neither `IntakeRecord` nor `ClassificationRecord` carries an organisation,
    and the intake lane feeds the public catalog. Section 14.2 is about
    organisation-private *symbol existence*; a review case naming no symbol
    and no organisation gives it nothing to protect. This is the same
    reasoning that left `GET /semantic-review/classification-schemes`
    unscoped -- and the argument has to be stated where the next reader of
    the route will find it, not merely acted on.
    """
    import inspect

    import symgov_backend.routes.semantic_review as route_module

    source = inspect.getsource(route_module.review_case_classification_preview)
    assert "_scope(" not in source
    assert "14.2" in source


def test_the_forecast_is_named_as_a_forecast_in_the_contract_itself():
    """`CLAUDE.md` forbids presenting an illustrative value as a production
    one. A client generated from this operation must not be able to read the
    response as recorded state."""
    operation = _operation(
        create_app().openapi(),
        "GET",
        "/semantic-review/review-cases/{review_case_id}/classification-preview",
    )
    schema = create_app().openapi()["components"]["schemas"][
        "ReviewCaseClassificationPreviewResponse"
    ]

    assert set(schema["properties"]) >= {"willAssert", "willGap", "willLink"}
    assert "assignments" not in schema["properties"]
    assert "gaps" not in schema["properties"]
    assert "splitItemId" in {
        parameter["name"] for parameter in operation.get("parameters", [])
    }
