from pathlib import Path


APP_JSX = Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.jsx"
API_JS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "api.js"


def test_forced_pin_change_route_and_guard_exist():
    source = APP_JSX.read_text(encoding="utf-8")

    assert 'path="/change-pin"' in source
    assert "auth.user?.mustChangePin" in source
    assert '<Navigate to="/change-pin" replace state={{ from: location }} />' in source
    assert "function ChangePinPage()" in source


def test_change_pin_ui_calls_change_pin_api_and_refreshes_user():
    source = APP_JSX.read_text(encoding="utf-8")

    assert "changeCurrentUserPin" in source
    assert "currentPin" in source
    assert "newPin" in source
    assert "confirmPin" in source
    assert "await auth.changePin({ currentPin, newPin })" in source
    assert "Your PIN has been changed" in source


def test_change_pin_api_client_posts_expected_endpoint():
    source = API_JS.read_text(encoding="utf-8")

    assert "export async function changeCurrentUserPin({ currentPin, newPin })" in source
    assert "requestJson('/auth/change-pin'" in source
