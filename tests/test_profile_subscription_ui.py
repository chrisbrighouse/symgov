from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend" / "src" / "App.jsx"
PROFILE = ROOT / "frontend" / "src" / "ProfilePage.jsx"
API = ROOT / "frontend" / "src" / "api.js"
STYLES = ROOT / "frontend" / "src" / "styles.css"


def test_profile_route_and_accessible_banner_link_exist():
    app = APP.read_text(encoding="utf-8")
    assert 'path="/profile"' in app
    assert 'to="/profile"' in app
    assert "user-identity-link" in app
    assert "<ProfilePage" in app


def test_profile_page_renders_identity_tier_dates_and_server_plan():
    source = PROFILE.read_text(encoding="utf-8")
    for marker in (
        "Your profile",
        "displayName",
        "user.email",
        "Current tier",
        "startedOn",
        "expiresOn",
        "annualPricePence",
        "minimumYears",
        "maximumYears",
        "No payment will be taken",
        "Perpetual",
        "confirmationRef",
        'aria-live="polite"',
    ):
        assert marker in source


def test_profile_page_has_explicit_upgrade_and_immediate_downgrade_confirmations():
    source = PROFILE.read_text(encoding="utf-8")
    assert "Confirm upgrade" in source
    assert "Confirm immediate downgrade" in source
    assert "remaining subscription time" in source
    assert "confirmed: true" in source
    assert "auth.refresh" in source
    assert "disabled={busy}" in source


def test_profile_api_helpers_use_current_session_without_user_id():
    source = API.read_text(encoding="utf-8")
    assert "fetchProfile" in source
    assert "upgradeCurrentSubscription" in source
    assert "downgradeCurrentSubscription" in source
    assert "'/profile/subscription/upgrade'" in source
    assert "'/profile/subscription/downgrade'" in source


def test_profile_has_responsive_styles():
    source = STYLES.read_text(encoding="utf-8")
    assert ".profile-page" in source
    assert ".profile-subscription-card" in source
    assert "@media" in source
