from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .db import DEFAULT_ENV_FILE
from .runtime import DEFAULT_STORAGE_ENV_FILE


LOCAL_SECURITY_ENVIRONMENTS = frozenset({"local", "test"})
NORMALIZED_ORGANIZATION_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,31}$")


def _environment() -> str:
    return os.environ.get("SYMGOV_ENVIRONMENT", "production").strip().lower() or "production"


def _csv_setting(name: str, local_default: str = "") -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None and _environment() in LOCAL_SECURITY_ENVIRONMENTS:
        raw = local_default
    return tuple(item.strip() for item in (raw or "").split(",") if item.strip())


def _organization_pilot_codes() -> tuple[str, ...]:
    values = os.environ.get("SYMGOV_ORGANIZATION_PILOT_CODES", "")
    normalized = {
        item.strip().lower()
        for item in values.split(",")
        if item.strip()
    }
    invalid = sorted(
        code for code in normalized if not NORMALIZED_ORGANIZATION_CODE_PATTERN.fullmatch(code)
    )
    if invalid:
        raise ValueError("Organization pilot codes must use normalized lowercase code grammar.")
    return tuple(sorted(normalized))


def _login_hash_secret() -> str:
    configured = os.environ.get("SYMGOV_AUTH_LOGIN_HASH_SECRET", "").strip()
    if configured:
        return configured
    if _environment() == "test":
        return "symgov-explicit-test-auth-throttle-secret"
    if _environment() == "local":
        return "symgov-explicit-local-auth-throttle-secret"
    return ""


def _read_env_value(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        candidate, value = line.split("=", 1)
        if candidate.strip() == key:
            return value.strip().strip("'\"")
    return ""


def _agentmail_api_key() -> str:
    direct = os.environ.get("AGENTMAIL_API_KEY", "").strip()
    if direct:
        return direct
    profile = os.environ.get("SYMGOV_HERMES_PROFILE", "symgov").strip() or "symgov"
    env_file = Path(
        os.environ.get("SYMGOV_HERMES_ENV_FILE", f"/root/.hermes/profiles/{profile}/.env")
    )
    return _read_env_value(env_file, "AGENTMAIL_API_KEY")


@dataclass(frozen=True)
class SymgovAPISettings:
    environment: str = field(default_factory=_environment)
    service_name: str = "symgov-api"
    api_prefix: str = "/api/v1"
    host: str = "0.0.0.0"
    port: int = 8010
    submission_pin: str = os.environ.get("SYMGOV_API_PIN", "4590")
    db_env_file: Path = DEFAULT_ENV_FILE
    storage_env_file: Path = DEFAULT_STORAGE_ENV_FILE
    enable_libby_worker: bool = os.environ.get("SYMGOV_ENABLE_LIBBY_WORKER", "").strip().lower() in {"1", "true", "yes", "on"}
    enable_agent_workers: bool = os.environ.get("SYMGOV_ENABLE_AGENT_WORKERS", "").strip().lower() in {"1", "true", "yes", "on"}
    agent_workers: tuple[str, ...] = tuple(
        item.strip().lower()
        for item in os.environ.get("SYMGOV_AGENT_WORKERS", "scott,vlad,tracy,libby,daisy,rupert,ed").split(",")
        if item.strip()
    )
    libby_worker_interval_seconds: float = float(os.environ.get("SYMGOV_LIBBY_WORKER_INTERVAL_SECONDS", "10"))
    libby_worker_limit: int = int(os.environ.get("SYMGOV_LIBBY_WORKER_LIMIT", "10"))
    agent_worker_interval_seconds: float = float(os.environ.get("SYMGOV_AGENT_WORKER_INTERVAL_SECONDS", "10"))
    agent_worker_limit: int = int(os.environ.get("SYMGOV_AGENT_WORKER_LIMIT", "10"))
    agent_worker_drain: bool = os.environ.get("SYMGOV_AGENT_WORKER_DRAIN", "1").strip().lower() in {"1", "true", "yes", "on"}
    agent_runtime: str = os.environ.get("SYMGOV_AGENT_RUNTIME", "direct").strip().lower()
    hermes_profile: str = os.environ.get("SYMGOV_HERMES_PROFILE", "symgov").strip() or "symgov"
    hermes_timeout_seconds: int = int(os.environ.get("SYMGOV_HERMES_TIMEOUT_SECONDS", "600"))
    hermes_host_openclaw_root: Path = Path(
        os.environ.get("SYMGOV_HERMES_HOST_OPENCLAW_ROOT", "/docker/openclaw-hz0t/data/.openclaw")
    )
    hermes_container_openclaw_root: Path = Path(
        os.environ.get("SYMGOV_HERMES_CONTAINER_OPENCLAW_ROOT", "/data/.openclaw")
    )
    subscription_admin_email: str = os.environ.get(
        "SYMGOV_SUBSCRIPTION_ADMIN_EMAIL", "chris.brighouse@hotmail.co.uk"
    ).strip().lower()
    email_transport: str = os.environ.get("SYMGOV_EMAIL_TRANSPORT", "smtp").strip().lower()
    agentmail_api_key: str = field(default_factory=_agentmail_api_key, repr=False)
    agentmail_inbox: str = os.environ.get("SYMGOV_AGENTMAIL_INBOX", "").strip().lower()
    agentmail_base_url: str = "https://api.agentmail.to/v0"
    agentmail_timeout_seconds: float = float(os.environ.get("SYMGOV_AGENTMAIL_TIMEOUT_SECONDS", "20"))
    smtp_host: str = os.environ.get("SYMGOV_SMTP_HOST", "").strip()
    smtp_port: int = int(os.environ.get("SYMGOV_SMTP_PORT", "587"))
    smtp_username: str = os.environ.get("SYMGOV_SMTP_USERNAME", "").strip()
    smtp_password: str = field(default=os.environ.get("SYMGOV_SMTP_PASSWORD", ""), repr=False)
    smtp_from_email: str = os.environ.get("SYMGOV_SMTP_FROM_EMAIL", "").strip().lower()
    smtp_starttls: bool = os.environ.get("SYMGOV_SMTP_STARTTLS", "1").strip().lower() in {"1", "true", "yes", "on"}
    smtp_ssl: bool = os.environ.get("SYMGOV_SMTP_SSL", "0").strip().lower() in {"1", "true", "yes", "on"}
    email_worker_interval_seconds: float = float(os.environ.get("SYMGOV_EMAIL_WORKER_INTERVAL_SECONDS", "30"))
    auth_login_account_failure_limit: int = int(os.environ.get("SYMGOV_AUTH_LOGIN_ACCOUNT_FAILURE_LIMIT", "5"))
    auth_login_ip_failure_limit: int = int(os.environ.get("SYMGOV_AUTH_LOGIN_IP_FAILURE_LIMIT", "20"))
    auth_login_window_seconds: int = int(os.environ.get("SYMGOV_AUTH_LOGIN_WINDOW_SECONDS", "900"))
    auth_login_block_seconds: int = int(os.environ.get("SYMGOV_AUTH_LOGIN_BLOCK_SECONDS", "900"))
    auth_login_hash_secret: str = field(
        default_factory=_login_hash_secret,
        repr=False,
    )
    trusted_proxy_cidrs: tuple[str, ...] = field(
        default_factory=lambda: _csv_setting("SYMGOV_TRUSTED_PROXY_CIDRS", "127.0.0.0/8,::1/128")
    )
    trusted_proxy_hops: int = int(os.environ.get("SYMGOV_TRUSTED_PROXY_HOPS", "1"))
    csrf_trusted_origins: tuple[str, ...] = field(
        default_factory=lambda: _csv_setting("SYMGOV_CSRF_TRUSTED_ORIGINS", "http://testserver,http://localhost")
    )
    csrf_trusted_hosts: tuple[str, ...] = field(
        default_factory=lambda: _csv_setting("SYMGOV_CSRF_TRUSTED_HOSTS", "testserver,localhost")
    )
    mutation_max_body_bytes: int = int(
        os.environ.get("SYMGOV_MUTATION_MAX_BODY_BYTES", str(128 * 1024 * 1024))
    )
    organizations_enabled: bool = os.environ.get("SYMGOV_ORGANIZATIONS_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    organization_admin_enabled: bool = os.environ.get("SYMGOV_ORGANIZATION_ADMIN_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    organization_custom_icons_enabled: bool = os.environ.get(
        "SYMGOV_ORGANIZATION_CUSTOM_ICONS_ENABLED", ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    organization_icon_upload_enabled: bool = os.environ.get(
        "SYMGOV_ORGANIZATION_ICON_UPLOAD_ENABLED", ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    platform_admin_enabled: bool = os.environ.get("SYMGOV_PLATFORM_ADMIN_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    symbol_sets_enabled: bool = os.environ.get("SYMGOV_SYMBOL_SETS_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    organization_symbols_enabled: bool = os.environ.get("SYMGOV_ORGANIZATION_SYMBOLS_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    organization_agents_enabled: bool = os.environ.get("SYMGOV_ORGANIZATION_AGENTS_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    organization_pilot_codes: tuple[str, ...] = field(default_factory=_organization_pilot_codes)


def get_settings() -> SymgovAPISettings:
    return SymgovAPISettings()
