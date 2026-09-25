"""The 422 handler must survive validators that raise a plain ValueError.

Pydantic keeps the raised exception object in the issue's ``ctx``. Returning
``exc.errors()`` unencoded made JSONResponse fail on it, so a request that
should have been a 422 became a 500.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import BaseModel, field_validator, model_validator

from symgov_backend.app import create_app
from symgov_backend.schemas import ProjectPatchRequest


class _FieldValueErrorBody(BaseModel):
    months: int

    @field_validator("months")
    @classmethod
    def not_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("Months must not be zero.")
        return value


def _client() -> TestClient:
    app = create_app()

    @app.post("/__test__/model-validator")
    def model_validator_route(body: ProjectPatchRequest) -> dict:
        return {"ok": True}

    @app.post("/__test__/field-validator")
    def field_validator_route(body: _FieldValueErrorBody) -> dict:
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False)


def test_model_validator_value_error_is_a_422_with_its_message():
    # ProjectPatchRequest.require_field raises ValueError on an empty body.
    response = _client().post("/__test__/model-validator", json={})

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation_error"
    assert "At least one field is required." in body["issues"][0]["msg"]
    assert body["issues"][0]["ctx"]["error"] == "At least one field is required."


def test_field_validator_value_error_is_a_422_with_its_message():
    response = _client().post("/__test__/field-validator", json={"months": 0})

    assert response.status_code == 422
    assert response.json()["issues"][0]["ctx"]["error"] == "Months must not be zero."


def test_ordinary_validation_issues_are_unchanged():
    response = _client().post("/__test__/field-validator", json={})

    assert response.status_code == 422
    issue = response.json()["issues"][0]
    assert issue["type"] == "missing"
    assert issue["loc"] == ["body", "months"]
