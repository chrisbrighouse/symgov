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
    assert "upgradeAdminUserSubscription" in source
    assert "adjustAdminUserSubscription" in source
    assert "cancelAdminUserSubscription" in source
    assert "deleteAdminUser" in source


def test_admin_user_management_supports_inline_role_editing_and_custom_pin_modal():
    source = APP_JSX.read_text(encoding="utf-8")

    assert "toggleUserRole" in source
    assert "role-pill" in source
    assert "pinResetDialog" in source
    assert "Reset PIN for" in source
    assert "Custom 4-digit PIN" in source


def test_admin_user_management_offers_roles_only_for_plus_users():
    source = APP_JSX.read_text(encoding="utf-8")

    assert "['admin', 'integrator', 'submitter', 'reviewer'].map" in source
    assert "user.subscription?.tier !== 'plus'" in source


def test_admin_user_management_allows_catalog_only_users_without_roles():
    source = APP_JSX.read_text(encoding="utf-8")

    assert "roles: []" in source
    assert "Each user needs at least one role." not in source


def test_admin_user_management_shows_subscription_dates_controls_and_pagination():
    source = APP_JSX.read_text(encoding="utf-8")

    assert "Subscription start" in source
    assert "Plus expiry" in source
    assert "Upgrade to Plus" in source
    assert "Adjust months" in source
    assert "Cancel Plus" in source
    assert "Remove user" in source
    assert "searchDraft" in source
    assert "appliedSearch" in source
    assert "userPage" in source
    assert "sortDirection" in source
    assert "requestId !== userRequestSequence.current" in source
    assert r"/^-?\d+$/" in source
    assert "Number.isSafeInteger" in source
    assert "Protected owner" in source


def test_header_shows_active_plus_badge():
    source = APP_JSX.read_text(encoding="utf-8")
    assert "plus-subscription-badge" in source
    assert "user.subscription?.isActive" in source


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

    assert "export async function fetchAdminUsers(" in source
    assert "export async function createAdminUser(" in source
    assert "export async function updateAdminUser(" in source
    assert "export async function resetAdminUserPin(" in source
    assert "requestJson('/admin/users'" in source
    assert "subscription/upgrade" in source
    assert "subscription/adjust" in source
    assert "subscription/cancel" in source


def test_admin_llm_management_api_client_methods_exist():
    source = API_JS.read_text(encoding="utf-8")

    assert "export async function fetchAdminLlmSettings()" in source
    assert "export async function updateAdminLlmSettings(" in source
    assert "export async function fetchOpenRouterModels()" in source
    assert "export async function testAdminLlmPrompt(" in source
    assert "requestJson('/admin/llm/settings'" in source
