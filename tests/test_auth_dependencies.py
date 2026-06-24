import pytest
from fastapi import HTTPException

from symgov_backend.auth import AuthenticatedUser
from symgov_backend.dependencies import require_any_role, require_role, require_user


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
