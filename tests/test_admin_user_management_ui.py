from pathlib import Path


APP_JSX = Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.jsx"
API_JS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "api.js"


def test_admin_user_management_page_and_navigation_exist():
    source = APP_JSX.read_text(encoding="utf-8")

    assert 'path="/workspace/users"' in source
    assert 'to="/workspace/users"' in source
    assert 'path="/workspace/llm"' in source
    assert 'to="/workspace/llm"' in source
    assert "function AdminUsersPage()" in source
    assert "function AdminLlmPage()" in source
    assert "Manage users" in source
    assert "Manage LLM" in source


def test_admin_user_management_ui_uses_admin_user_api_calls():
    source = APP_JSX.read_text(encoding="utf-8")

    assert "fetchAdminUsers" in source
    assert "createAdminUser" in source
    assert "updateAdminUser" in source
    assert "resetAdminUserPin" in source


def test_admin_user_management_supports_inline_role_editing_and_custom_pin_modal():
    source = APP_JSX.read_text(encoding="utf-8")

    assert "toggleUserRole" in source
    assert "role-pill" in source
    assert "pinResetDialog" in source
    assert "Reset PIN for" in source
    assert "Custom 4-digit PIN" in source


def test_admin_user_management_offers_integrator_role():
    source = APP_JSX.read_text(encoding="utf-8")

    assert "setRole('integrator'" in source
    assert "<span>Integrator</span>" in source
    assert "['admin', 'integrator', 'submitter', 'reviewer'].map" in source
    assert '<label className="checkbox-row"><input type="checkbox" checked={form.roles.includes(\'integrator\')}' in source


def test_admin_user_management_shows_toast_feedback():
    source = APP_JSX.read_text(encoding="utf-8")

    assert "admin-toast" in source
    assert "setToast(" in source


def test_admin_user_management_uses_per_user_loading_state_not_global_busy_for_rows():
    source = APP_JSX.read_text(encoding="utf-8")

    assert "rowBusyByUser" in source
    assert "isRowBusy" in source
    assert "setRowBusyByUser" in source
    assert "disabled={isRowBusy(user.id)}" in source
    assert "disabled={createBusy}" in source


def test_admin_user_management_api_client_methods_exist():
    source = API_JS.read_text(encoding="utf-8")

    assert "export async function fetchAdminUsers()" in source
    assert "export async function createAdminUser(" in source
    assert "export async function updateAdminUser(" in source
    assert "export async function resetAdminUserPin(" in source
    assert "requestJson('/admin/users'" in source


def test_admin_llm_management_api_client_methods_exist():
    source = API_JS.read_text(encoding="utf-8")

    assert "export async function fetchAdminLlmSettings()" in source
    assert "export async function updateAdminLlmSettings(" in source
    assert "export async function fetchOpenRouterModels()" in source
    assert "export async function testAdminLlmPrompt(" in source
    assert "requestJson('/admin/llm/settings'" in source
