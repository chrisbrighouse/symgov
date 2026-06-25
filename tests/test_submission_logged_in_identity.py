from pathlib import Path


API_JS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "api.js"
APP_JSX = Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.jsx"
SCHEMAS_PY = Path(__file__).resolve().parents[1] / "backend" / "symgov_backend" / "schemas.py"
PUBLIC_ROUTE_PY = Path(__file__).resolve().parents[1] / "backend" / "symgov_backend" / "routes" / "public.py"
SERVICE_PY = Path(__file__).resolve().parents[1] / "backend" / "symgov_backend" / "services" / "external_submissions.py"


def test_submission_ui_no_longer_collects_or_sends_submission_pin():
    app_source = APP_JSX.read_text(encoding="utf-8")
    api_source = API_JS.read_text(encoding="utf-8")

    assert "Submission PIN" not in app_source
    assert "formState.pin" not in app_source
    assert "updateField('pin'" not in app_source
    assert "pin: formState.pin.trim()" not in api_source


def test_submission_ui_uses_logged_in_identity_not_manual_submitter_fields():
    app_source = APP_JSX.read_text(encoding="utf-8")
    api_source = API_JS.read_text(encoding="utf-8")

    assert "Submitter name" not in app_source
    assert "Submitter email" not in app_source
    assert "rememberDetails" not in app_source
    assert "readSubmissionDetailsCookie" not in app_source
    assert "writeSubmissionDetailsCookie" not in app_source
    assert "clearSubmissionDetailsCookie" not in app_source
    assert "submitter_name: formState.submitterName.trim()" not in api_source
    assert "submitter_email: formState.submitterEmail.trim()" not in api_source


def test_backend_submission_request_no_longer_accepts_pin_field():
    schema_source = SCHEMAS_PY.read_text(encoding="utf-8")

    request_block = schema_source.split("class ExternalSubmissionRequest", 1)[1].split("class ExternalSubmissionResponse", 1)[0]
    assert "pin:" not in request_block


def test_submission_route_uses_logged_in_identity_not_shared_pin():
    public_route_source = PUBLIC_ROUTE_PY.read_text(encoding="utf-8")
    service_source = SERVICE_PY.read_text(encoding="utf-8")

    assert "current_user: AuthenticatedUser" in public_route_source
    assert 'payload["submitter_name"] = current_user.display_name' in public_route_source
    assert 'payload["submitter_email"] = current_user.email' in public_route_source
    assert "settings.submission_pin" not in public_route_source
    assert "Invalid submission PIN" not in service_source
