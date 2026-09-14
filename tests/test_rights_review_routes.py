"""Route-policy cover for SM-P1-01 WP1.3: the rights record review API.

Why this package exists at all. Today `publication_gate.
propose_intake_rights_record` is the only thing in the codebase that creates a
`RightsRecord`, and it always writes `determination_method='ai_assisted'`,
which section 8.4 and the `approved_not_ai_determined` constraint make
permanently unapprovable. So every rights record production holds is a
proposal that can never become an approval, the section 9.2 gate's rights
dimension is unsatisfiable by any path, and SM-P2's authoritative ingestion
stays behind an all-waiver or all-refusal gate. A route by which a *reviewer*
proposes their own record is the missing half, not a convenience.

Scope note: this file is the policy matrix, and runs in the portable
partition. It shares `test_semantic_review_routes`'s client builder and
queue stubs -- WP1.3's routes live on the same router, behind the same flag,
under the same `admin`/`reviewer` boundary, so duplicating that scaffold
would only create a second copy to drift. The governance rules themselves --
section 7.12's who/when/why triple, section 8.4's refusal, the licence
requirement, and the tenant boundary on real rows -- are in
`test_rights_review_routes_postgresql.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_semantic_review_routes import (  # noqa: E402
    V1_PREFIX,
    _mounted,
    _probe,
    _semantic_client,
    _stub_queues,
)

from symgov_backend.app import create_app  # noqa: E402

# Both are `admin`/`reviewer`, per decision Q2: rights decisions follow the
# same boundary as the other governance acts, not the platform-level one
# concept lifecycle takes.
RIGHTS_REVIEW_ROUTES = (
    ("POST", "/semantic-review/rights-records"),
    ("POST", "/semantic-review/rights-records/{record_id}/decision"),
)


def test_both_routes_are_mounted_once_at_the_v1_prefix():
    mounted = _mounted(create_app())

    for method, template in RIGHTS_REVIEW_ROUTES:
        assert (method, f"{V1_PREFIX}{template}") in mounted, f"{method} {template} is not mounted"
        assert (method, f"/api{template}") not in mounted, "decision Q7: v1-only, no legacy twin"


@pytest.mark.parametrize("method,template", RIGHTS_REVIEW_ROUTES)
def test_the_rights_surface_is_absent_while_the_flag_is_off(method, template):
    client, _Session, _user_id, _settings = _semantic_client(enabled=False, roles=("admin",))

    response = _probe(client, method, template)

    assert response.status_code == 404, response.text
    assert response.json()["error"] == "not_found"


@pytest.mark.parametrize("method,template", RIGHTS_REVIEW_ROUTES)
def test_an_unauthenticated_caller_cannot_touch_a_rights_record(method, template):
    client, _Session, _user_id, _settings = _semantic_client(roles=("admin",), login=False)

    response = _probe(client, method, template)

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "Authentication required."


@pytest.mark.parametrize("method,template", RIGHTS_REVIEW_ROUTES)
def test_a_role_outside_the_boundary_cannot_touch_a_rights_record(method, template):
    client, _Session, _user_id, _settings = _semantic_client(roles=("submitter",))

    response = _probe(client, method, template)

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Insufficient role for this operation."


@pytest.mark.parametrize("method,template", RIGHTS_REVIEW_ROUTES)
@pytest.mark.parametrize("role", ("admin", "reviewer"))
def test_both_permitted_roles_pass_the_authorization_boundary(monkeypatch, role, method, template):
    _stub_queues(monkeypatch)
    client, _Session, _user_id, _settings = _semantic_client(roles=(role,))

    response = _probe(client, method, template)

    assert response.status_code not in (401, 403), response.text


@pytest.mark.parametrize("method,template", RIGHTS_REVIEW_ROUTES)
@pytest.mark.parametrize("status", (401, 403, 404))
def test_each_error_status_is_declared_on_both_operations(status, method, template):
    operation = create_app().openapi()["paths"][f"{V1_PREFIX}{template}"][method.lower()]

    reference = operation["responses"][str(status)]["content"]["application/json"]["schema"]["$ref"]

    assert reference == "#/components/schemas/APIErrorResponse", operation["responses"]


@pytest.mark.parametrize("method,template", RIGHTS_REVIEW_ROUTES)
def test_both_operations_declare_the_projects_own_validation_envelope(method, template):
    """Not FastAPI's generated `HTTPValidationError`.

    Every governance rule in `transition_rights_record` surfaces as a 422
    through this envelope -- section 8.4's refusal, the missing reason, the
    missing licence -- so a client typed against the wrong body would fail to
    read the reason a rights approval was refused.
    """
    operation = create_app().openapi()["paths"][f"{V1_PREFIX}{template}"][method.lower()]

    reference = operation["responses"]["422"]["content"]["application/json"]["schema"]["$ref"]

    assert reference == "#/components/schemas/APIValidationErrorResponse"


def test_a_proposal_creates_a_row_and_says_so():
    document = create_app().openapi()
    propose = document["paths"][f"{V1_PREFIX}/semantic-review/rights-records"]["post"]
    decide = document["paths"][f"{V1_PREFIX}/semantic-review/rights-records/{{record_id}}/decision"]["post"]

    assert "201" in propose["responses"]
    assert "200" in decide["responses"], "a decision changes a row rather than creating one"


def test_neither_request_body_accepts_an_unknown_field():
    document = create_app().openapi()

    for method, template in RIGHTS_REVIEW_ROUTES:
        operation = document["paths"][f"{V1_PREFIX}{template}"][method.lower()]
        name = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
        assert document["components"]["schemas"][name].get("additionalProperties") is False, name


def test_no_rights_route_requires_step_up_reauthentication():
    """Decision Q8, 2026-09-13.

    A rights approval is reversible: it is retired by the next decision, and
    the succession is recorded. `symbol_demotion` uses step-up because it
    destroys published projections; this does not.
    """
    import symgov_backend.routes.semantic_review as route_module
    from symgov_backend.dependencies import require_recent_step_up

    def callables(dependant):
        yield dependant.call
        for nested in dependant.dependencies:
            yield from callables(nested)

    # The resolved dependency tree, not a source grep: the module docstring
    # names the dependency in order to say it is deliberately absent, so a
    # text search would fail on the sentence explaining the decision.
    routes = [route for route in route_module.router.routes if hasattr(route, "dependant")]
    assert routes, "expected the router to expose its routes for inspection"

    for route in routes:
        assert require_recent_step_up not in set(callables(route.dependant)), route.path
