from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from symgov_backend.auth import AuthenticatedUser
from symgov_backend.dependencies import (
    classify_workspace_policy,
    require_any_role,
    require_role,
    require_user,
    require_workspace_access,
)


def user_with_roles(*roles):
    return AuthenticatedUser(
        id="user-1",
        email="chris.brighouse@hotmail.co.uk",
        display_name="Alfi",
        roles=tuple(roles),
        must_change_pin=False,
    )


def test_require_user_rejects_missing_session_user():
    with pytest.raises(HTTPException) as exc:
        require_user(None)

    assert exc.value.status_code == 401


def test_require_user_accepts_authenticated_user():
    user = user_with_roles("submitter")

    assert require_user(user) is user


def test_require_role_accepts_matching_role():
    user = user_with_roles("admin", "reviewer")

    assert require_role("admin")(user) is user


def test_require_role_rejects_missing_role():
    with pytest.raises(HTTPException) as exc:
        require_role("admin")(user_with_roles("reviewer"))

    assert exc.value.status_code == 403


def test_require_any_role_accepts_any_matching_role():
    user = user_with_roles("reviewer")

    assert require_any_role({"admin", "reviewer"})(user) is user


def test_require_any_role_rejects_when_no_roles_match():
    with pytest.raises(HTTPException) as exc:
        require_any_role({"admin", "reviewer"})(user_with_roles("submitter"))

    assert exc.value.status_code == 403


def workspace_request(method="GET", route_path="/api/v1/workspace/review-cases", url_path=None):
    scope = {
        "type": "http",
        "method": method,
        "path": url_path or route_path,
        "headers": [],
        "route": SimpleNamespace(path=route_path),
    }
    return Request(scope)


@pytest.mark.parametrize(
    ("method", "route_path", "expected"),
    [
        ("GET", "/api/v1/workspace/review-cases", "reviewer_admin"),
        ("GET", "/api/workspace/review-cases", "reviewer_admin"),
        ("POST", "/api/v1/workspace/scott/source-searches", "admin"),
        ("GET", "/api/v1/workspace/new-review-route", "admin"),
        ("DELETE", "/api/v1/workspace/review-cases", "admin"),
        ("GET", "/api/v2/workspace/review-cases", "admin"),
        ("GET", "/malformed", "admin"),
    ],
)
def test_workspace_policy_classification_is_exact_and_fail_closed(method, route_path, expected):
    assert classify_workspace_policy(method, route_path) == expected


def test_workspace_policy_uses_matched_template_not_concrete_url_path():
    request = workspace_request(
        "POST",
        "/api/v1/workspace/review-cases/{review_case_id}/decisions",
        "/api/v1/workspace/review-cases/0c242672-3b15-43b7-b870-bbac5f92af06/decisions",
    )

    assert require_workspace_access(request, user_with_roles("reviewer")).roles == ("reviewer",)


def test_workspace_policy_uses_effective_matched_template_for_lazy_included_routers():
    request = workspace_request("GET", "/workspace/review-cases")
    request.scope["fastapi"] = {
        "effective_route_context": SimpleNamespace(path="/api/v1/workspace/review-cases")
    }

    assert require_workspace_access(request, user_with_roles("reviewer")).roles == ("reviewer",)


@pytest.mark.parametrize("route_value", [None, SimpleNamespace(), SimpleNamespace(path=None), SimpleNamespace(path=12)])
def test_workspace_policy_missing_or_malformed_route_metadata_is_admin_only(route_value):
    scope = {"type": "http", "method": "GET", "path": "/api/v1/workspace/review-cases", "headers": []}
    if route_value is not None:
        scope["route"] = route_value
    request = Request(scope)

    with pytest.raises(HTTPException) as exc:
        require_workspace_access(request, user_with_roles("reviewer"))

    assert exc.value.status_code == 403
    assert exc.value.detail == "Insufficient role for this operation."


@pytest.mark.parametrize(
    ("roles", "method", "route_path", "allowed"),
    [
        (("reviewer",), "GET", "/api/v1/workspace/review-cases", True),
        (("reviewer",), "POST", "/api/v1/workspace/scott/source-searches", False),
        (("reviewer",), "GET", "/api/v1/workspace/unknown", False),
        (("admin",), "GET", "/api/v1/workspace/review-cases", True),
        (("admin",), "POST", "/api/v1/workspace/scott/source-searches", True),
        (("admin",), "GET", "/api/v1/workspace/unknown", True),
        (("admin", "reviewer"), "POST", "/api/v1/workspace/scott/source-searches", True),
        (("submitter",), "GET", "/api/v1/workspace/review-cases", False),
    ],
)
def test_workspace_access_role_matrix(roles, method, route_path, allowed):
    user = user_with_roles(*roles)
    if allowed:
        assert require_workspace_access(workspace_request(method, route_path), user) is user
        return

    with pytest.raises(HTTPException) as exc:
        require_workspace_access(workspace_request(method, route_path), user)

    assert exc.value.status_code == 403
    assert exc.value.detail == "Insufficient role for this operation."


def test_workspace_access_preserves_unauthenticated_contract():
    with pytest.raises(HTTPException) as exc:
        require_user(None)

    assert exc.value.status_code == 401
    assert exc.value.detail == "Authentication required."
