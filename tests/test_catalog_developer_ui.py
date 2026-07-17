from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend" / "src" / "App.jsx"
HUB = ROOT / "frontend" / "src" / "CatalogDeveloperHub.jsx"
HELPERS = ROOT / "frontend" / "src" / "catalogDeveloper.js"
API = ROOT / "frontend" / "src" / "api.js"
STYLE = ROOT / "frontend" / "src" / "catalogDeveloper.css"
MAIN = ROOT / "frontend" / "src" / "main.jsx"


def test_catalog_developer_hub_is_routed_after_login_and_linked_from_navigation():
    app = APP.read_text(encoding="utf-8")

    assert 'path="/integrator/catalog"' in app
    assert "<RequireAnyRole roles={['admin', 'integrator']}><CatalogDeveloperHub /></RequireAnyRole>" in app
    assert 'to="/integrator/catalog"' in app
    assert 'label="Integrator"' in app
    assert "const canIntegrate = hasAnyRole(user, ['admin', 'integrator']);" in app
    assert "{canIntegrate ? <RailNavLink" in app


def test_catalog_developer_hub_contains_first_milestone_experiences():
    source = HUB.read_text(encoding="utf-8")

    for marker in (
        "Catalog Integrator Hub",
        "Open Integrator documentation",
        "kept in memory only",
        "Five-minute quickstart",
        "API reference",
        "Try it in the sandbox",
        "Ask Ed about integration",
        "Changelog",
        'to="/support"',
    ):
        assert marker in source

    assert "Catalog Developer Hub" not in source
    assert "Developer access" not in source
    assert "Open integrator documentation" in source
    assert 'aria-label="Integrator documentation"' in source
    assert "if (!candidate) return;" not in source
    assert "disabled={accessState.busy || !apiKey.trim()}" not in source
    assert "An API key is optional for documentation" in source
    assert "disabled={sandboxState.busy || !toolApiKey}" in source
    assert "disabled={edState.busy || !edQuestion.trim() || !toolApiKey}" in source

    assert "localStorage" not in source
    assert "sessionStorage" not in source
    assert "setApiKey('')" in source
    assert 'role="status"' in source or 'aria-live="polite"' in source


def test_catalog_developer_hub_uses_protected_backend_contracts():
    source = HUB.read_text(encoding="utf-8")
    api = API.read_text(encoding="utf-8")

    for api_method in (
        "fetchCatalogDeveloperManifest",
        "fetchCatalogDeveloperOpenApi",
        "runCatalogDeveloperSandbox",
        "askCatalogIntegrationEd",
    ):
        assert api_method in source
        assert f"export async function {api_method}" in api

    assert "buildCatalogCodeExample" in source
    assert "Authorization" in api
    assert "localStorage" not in api
    assert "sessionStorage" not in api


def test_catalog_developer_hub_styles_are_isolated_and_loaded():
    assert STYLE.exists()
    assert ".catalog-developer-hub" in STYLE.read_text(encoding="utf-8")
    assert "./catalogDeveloper.css" in MAIN.read_text(encoding="utf-8")
    assert HELPERS.exists()


def test_catalog_developer_hub_uses_a_readable_light_documentation_surface():
    styles = STYLE.read_text(encoding="utf-8")

    assert "--developer-surface: #ffffff" in styles
    assert "--developer-ink: #173042" in styles
    assert "--developer-muted: #5f7180" in styles
    assert "background: var(--developer-surface);" in styles
    assert "color: var(--developer-ink);" in styles
    assert ".catalog-code-card" in styles
    assert "--developer-code-surface: #10242f" in styles
    assert ":focus-visible" in styles


def test_catalog_developer_hub_cancels_key_bearing_requests_and_ignores_late_results():
    source = HUB.read_text(encoding="utf-8")
    api = API.read_text(encoding="utf-8")

    assert "AbortController" in source
    assert "useEffect" in source
    assert "pagehide" in source
    assert "beforeunload" in source
    assert "signal.aborted" in source
    assert "mountedRef.current" in source
    assert "controller.abort()" in source
    assert "signal" in api
    assert "localStorage" not in source
    assert "sessionStorage" not in source


def test_catalog_developer_hub_binds_protected_calls_to_the_validated_key():
    source = HUB.read_text(encoding="utf-8")
    unlock = source[source.index("async function unlockDeveloperHub"):source.index("function lockDeveloperHub")]
    lock = source[source.index("function lockDeveloperHub"):source.index("async function runSandbox")]

    assert re.search(r'type="password"[\s\S]*?disabled=\{accessState\.busy\}', source)
    key_input = source[source.index('type="password"'):source.index('/>', source.index('type="password"'))]
    assert "required" not in key_input
    assert "const [toolApiKey, setToolApiKey] = useState('');" in source
    assert unlock.index("fetchCatalogDeveloperManifest(controller.signal)") < unlock.index("fetchCatalogDeveloperOpenApi(controller.signal)")
    assert "if (!openApiResult.ok)" in unlock
    assert unlock.index("if (!openApiResult.ok)") < unlock.index("setToolApiKey(candidate)")
    assert "setToolApiKey('')" in unlock
    assert "runCatalogDeveloperSandbox(toolApiKey," in source
    assert "askCatalogIntegrationEd(toolApiKey," in source
    assert "runCatalogDeveloperSandbox(apiKey," not in source
    assert "askCatalogIntegrationEd(apiKey," not in source
    assert "setApiKey('')" in lock
    assert "setToolApiKey('')" in lock


def test_catalog_developer_hub_keeps_native_buttons_and_safe_guide_navigation():
    source = HUB.read_text(encoding="utf-8")

    assert 'role="listitem"' not in source
    assert "resolveDeveloperCitation" in source
    assert 'href="#quickstart"' in source
    assert 'href="#reference"' in source
    assert 'href="#changelog"' in source
    assert 'to="/support"' in source


def test_integrator_can_generate_view_status_and_revoke_one_self_service_key():
    source = HUB.read_text(encoding="utf-8")
    api = API.read_text(encoding="utf-8")

    for api_method in (
        "fetchCatalogSelfServiceApiKey",
        "createCatalogSelfServiceApiKey",
        "revokeCatalogSelfServiceApiKey",
    ):
        assert f"export async function {api_method}" in api
        assert api_method in source

    assert 'href="#api-key"' in source
    assert 'id="api-key"' in source
    assert 'name="customerName"' in source
    assert 'name="integrationName"' in source
    assert 'type="datetime-local"' in source
    for scope in (
        "catalog.read",
        "catalog.preview",
        "catalog.ed.query",
        "catalog.feedback.write",
        "catalog.usage.read",
    ):
        assert scope in source
    assert "Generate API key" in source
    assert "Active API key" in source
    assert "Clear and revoke key" in source
    assert "must be saved" in source
    assert "won't be accessible again" in source
    assert "window.confirm" in source
    assert "setToolApiKey(result.payload.rawKey)" in source
    assert "localStorage" not in source
    assert "sessionStorage" not in source
