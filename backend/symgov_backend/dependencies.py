from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
import ipaddress
import json
from types import MappingProxyType
from typing import Callable
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .auth import AuthenticatedUser, authoritative_user_from_token, current_user_from_token
from .db import create_session_factory
from .runtime import RuntimePersistenceBridge
from .settings import SymgovAPISettings, get_settings


def get_db_session(settings: SymgovAPISettings = Depends(get_settings)) -> Generator[Session, None, None]:
    resolved_settings = settings
    # Per-request engine: NullPool so we don't leak connections via discarded
    # session factories. The TCP cost is small compared to pool exhaustion risk.
    session_factory = create_session_factory(env_file=resolved_settings.db_env_file, nopool=True)
    with session_factory() as session:
        yield session


def get_runtime_bridge(settings: SymgovAPISettings | None = None) -> RuntimePersistenceBridge:
    resolved_settings = settings or get_settings()
    return RuntimePersistenceBridge(env_file=str(resolved_settings.db_env_file))


SESSION_COOKIE_NAME = "symgov_session"
COOKIE_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
UNAUTHENTICATED_LOGIN_OPERATIONS = frozenset(
    {
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/auth/login"),
    }
)
API_KEY_ONLY_MUTATION_OPERATIONS = frozenset(
    {
        ("POST", "/api/v1/catalog/ed/query"),
        ("POST", "/api/v1/catalog/search"),
        ("POST", "/api/v1/catalog/symbols/{symbol_ref}/feedback"),
    }
)


class BoundedMutationBodyMiddleware:
    """Bound mutation bodies before routing, parsing, or authentication."""

    def __init__(self, app, *, max_body_bytes: int):
        self.app = app
        self.max_body_bytes = max_body_bytes

    def _must_bound(self, scope: dict) -> bool:
        method = str(scope.get("method", "")).upper()
        return scope.get("type") == "http" and method in COOKIE_MUTATION_METHODS

    async def __call__(self, scope, receive, send) -> None:
        if not self._must_bound(scope):
            await self.app(scope, receive, send)
            return

        body = bytearray()
        disconnected = False
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                disconnected = True
                break
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.max_body_bytes:
                response_body = b'{"error":"request_error","detail":"Request body is too large."}'
                await send(
                    {
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(response_body)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": response_body})
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive():
            nonlocal replayed
            if not replayed:
                replayed = True
                if disconnected:
                    return {"type": "http.disconnect"}
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)


def validate_request_security_settings(settings: SymgovAPISettings) -> None:
    if settings.mutation_max_body_bytes < 1:
        raise ValueError("Mutation request body limit must be positive.")
    if not 1 <= settings.trusted_proxy_hops <= 10:
        raise ValueError("Trusted proxy hops must be between 1 and 10.")
    try:
        tuple(ipaddress.ip_network(value, strict=False) for value in settings.trusted_proxy_cidrs)
    except ValueError as exc:
        raise ValueError("Trusted proxy CIDRs must contain valid IP networks.") from exc

    if not settings.csrf_trusted_origins:
        raise ValueError("At least one trusted CSRF origin is required.")
    for value in settings.csrf_trusted_origins:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Trusted CSRF origins must be HTTP(S) origins without paths or credentials.")

    if not settings.csrf_trusted_hosts:
        raise ValueError("At least one trusted CSRF host is required.")
    for value in settings.csrf_trusted_hosts:
        host = value.strip().lower()
        parsed = urlsplit(f"//{host}")
        if not host or parsed.hostname != host or parsed.port is not None:
            raise ValueError("Trusted CSRF hosts must contain hostnames without schemes, paths, or ports.")


def _normalized_ip(value: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return None


def resolve_client_ip(
    peer_ip: str | None,
    forwarded_for: str | None,
    settings: SymgovAPISettings,
) -> str | None:
    if not 1 <= settings.trusted_proxy_hops <= 10:
        raise ValueError("Trusted proxy hops must be between 1 and 10.")
    peer = _normalized_ip(peer_ip)
    if peer is None:
        return None
    try:
        trusted_networks = tuple(ipaddress.ip_network(value, strict=False) for value in settings.trusted_proxy_cidrs)
    except ValueError as exc:
        raise ValueError("Trusted proxy CIDRs must contain valid IP networks.") from exc
    if not any(peer in network for network in trusted_networks):
        return peer.compressed
    if not forwarded_for:
        return peer.compressed
    forwarded_values = tuple(value.strip() for value in forwarded_for.split(","))
    if not 1 <= len(forwarded_values) <= settings.trusted_proxy_hops:
        return peer.compressed
    forwarded = tuple(_normalized_ip(value) for value in forwarded_values)
    if any(value is None for value in forwarded):
        return peer.compressed
    for candidate in reversed(forwarded):
        assert candidate is not None
        if not any(candidate in network for network in trusted_networks):
            return candidate.compressed
    first = forwarded[0]
    return first.compressed if first is not None else None


def _peer_is_trusted(peer_ip: str | None, settings: SymgovAPISettings) -> bool:
    peer = _normalized_ip(peer_ip)
    if peer is None:
        return False
    try:
        return any(peer in ipaddress.ip_network(value, strict=False) for value in settings.trusted_proxy_cidrs)
    except ValueError as exc:
        raise ValueError("Trusted proxy CIDRs must contain valid IP networks.") from exc


def _canonical_http_origin(scheme: str, authority: str) -> tuple[str, str, int] | None:
    normalized_scheme = str(scheme or "").strip().lower()
    if normalized_scheme not in {"http", "https"}:
        return None
    try:
        parsed = urlsplit(f"{normalized_scheme}://{str(authority or '').strip()}")
        port = parsed.port
    except ValueError:
        return None
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    return normalized_scheme, parsed.hostname.lower(), port or (443 if normalized_scheme == "https" else 80)


def _canonical_source_origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return scheme, parsed.hostname.lower(), port or (443 if scheme == "https" else 80)


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON constant is not permitted: {value}")


@dataclass(frozen=True)
class WorkspaceOperation:
    method: str
    template: str
    surfaces: tuple[str, ...]
    policy: str
    operation_class: str


@dataclass(frozen=True)
class ConcreteWorkspaceOperation:
    method: str
    template: str
    surface: str
    path: str
    policy: str
    operation_class: str


WORKSPACE_OPERATIONS = (
    WorkspaceOperation("GET", "/agent-queue-items", ("v1", "legacy"), "admin", "operational_queue"),
    WorkspaceOperation("GET", "/tracy/status", ("v1", "legacy"), "admin", "agent_status"),
    WorkspaceOperation("GET", "/agent-worker-health", ("v1", "legacy"), "admin", "worker_health"),
    WorkspaceOperation("GET", "/reggie/queue-controls", ("v1", "legacy"), "admin", "queue_control"),
    WorkspaceOperation("POST", "/scott/source-searches", ("v1", "legacy"), "admin", "scan_control"),
    WorkspaceOperation("POST", "/scott/source-searches/{queue_item_id}/stop", ("v1", "legacy"), "admin", "scan_control"),
    WorkspaceOperation("GET", "/scott/source-sites", ("v1", "legacy"), "admin", "source_config"),
    WorkspaceOperation("PATCH", "/scott/source-sites/{source_site_id}/prompt", ("v1", "legacy"), "admin", "source_config"),
    WorkspaceOperation("PATCH", "/scott/source-sites/{source_site_id}/include-next-run", ("v1", "legacy"), "admin", "source_config"),
    WorkspaceOperation("PATCH", "/scott/source-sites/{source_site_id}/status", ("v1", "legacy"), "admin", "source_config"),
    WorkspaceOperation("PATCH", "/scott/source-sites/{source_site_id}/auth", ("v1", "legacy"), "admin", "source_config"),
    WorkspaceOperation("POST", "/hannah/cleanup-actions", ("v1", "legacy"), "admin", "agent_control"),
    WorkspaceOperation("POST", "/hannah/curation-searches", ("v1", "legacy"), "admin", "scan_control"),
    WorkspaceOperation("POST", "/hannah/curation-searches/{queue_item_id}/stop", ("v1", "legacy"), "admin", "scan_control"),
    WorkspaceOperation("GET", "/hannah/photo-candidates", ("v1", "legacy"), "admin", "agent_operational"),
    WorkspaceOperation("POST", "/whitney/demand-scans", ("v1", "legacy"), "admin", "scan_control"),
    WorkspaceOperation("POST", "/whitney/demand-scans/{queue_item_id}/stop", ("v1", "legacy"), "admin", "scan_control"),
    WorkspaceOperation("GET", "/whitney/demand-signals", ("v1", "legacy"), "admin", "agent_operational"),
    WorkspaceOperation("GET", "/review-cases", ("v1", "legacy"), "reviewer_admin", "review"),
    WorkspaceOperation("GET", "/rights-review-cases", ("v1", "legacy"), "reviewer_admin", "rights"),
    WorkspaceOperation("PATCH", "/review-cases/{review_case_id}/symbol-properties", ("v1",), "reviewer_admin", "property"),
    WorkspaceOperation("GET", "/review-symbol-property-options", ("v1",), "reviewer_admin", "property"),
    WorkspaceOperation("POST", "/rights-review-cases/{review_case_id}/decisions", ("v1",), "reviewer_admin", "rights"),
    WorkspaceOperation("POST", "/review-cases/{review_case_id}/decisions", ("v1",), "reviewer_admin", "review"),
    WorkspaceOperation("POST", "/review-cases/{review_case_id}/split-items/process-decisions", ("v1",), "reviewer_admin", "review"),
    WorkspaceOperation("GET", "/daisy/reports", ("v1", "legacy"), "reviewer_admin", "review_asset"),
    WorkspaceOperation("GET", "/review-cases/{review_case_id}/children/preview", ("v1",), "reviewer_admin", "preview"),
    WorkspaceOperation("GET", "/review-cases/{review_case_id}/source/preview", ("v1",), "reviewer_admin", "preview"),
)

WORKSPACE_SURFACE_PREFIXES = {"v1": "/api/v1/workspace", "legacy": "/api/workspace"}


def expand_workspace_operations() -> tuple[ConcreteWorkspaceOperation, ...]:
    return tuple(
        ConcreteWorkspaceOperation(
            method=operation.method,
            template=operation.template,
            surface=surface,
            path=f"{WORKSPACE_SURFACE_PREFIXES[surface]}{operation.template}",
            policy=operation.policy,
            operation_class=operation.operation_class,
        )
        for operation in WORKSPACE_OPERATIONS
        for surface in operation.surfaces
    )


WORKSPACE_POLICY_BY_OPERATION = MappingProxyType(
    {
        (operation.method, operation.template, surface): operation.policy
        for operation in WORKSPACE_OPERATIONS
        for surface in operation.surfaces
    }
)


def normalize_workspace_route_path(route_path: object) -> tuple[str, str] | None:
    if not isinstance(route_path, str):
        return None
    for surface, prefix in WORKSPACE_SURFACE_PREFIXES.items():
        if route_path.startswith(f"{prefix}/"):
            return surface, route_path[len(prefix) :]
    return None


def classify_workspace_policy(method: object, route_path: object) -> str:
    normalized = normalize_workspace_route_path(route_path)
    if normalized is None or not isinstance(method, str):
        return "admin"
    surface, template = normalized
    return WORKSPACE_POLICY_BY_OPERATION.get((method.upper(), template, surface), "admin")


def matched_route_template(request: Request) -> object:
    fastapi_scope = request.scope.get("fastapi")
    if isinstance(fastapi_scope, dict):
        effective_route = fastapi_scope.get("effective_route_context")
        effective_path = getattr(effective_route, "path", None)
        if isinstance(effective_path, str):
            return effective_path
    route = request.scope.get("route")
    return getattr(route, "path", None)


async def require_cookie_mutation_security(
    request: Request,
    settings: SymgovAPISettings = Depends(get_settings),
) -> None:
    method = request.method.upper()
    if method not in COOKIE_MUTATION_METHODS:
        return

    operation = (method, matched_route_template(request))
    if operation in API_KEY_ONLY_MUTATION_OPERATIONS:
        return

    body = await request.body()
    if body:
        if not request.headers.get("content-type", "").lower().startswith("application/json"):
            raise HTTPException(status_code=415, detail="Content-Type must be application/json.")
        try:
            payload = json.loads(body, parse_constant=_reject_non_finite_json_constant)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Request body must be a JSON object.")

    if operation in UNAUTHENTICATED_LOGIN_OPERATIONS:
        return
    if not request.cookies.get(SESSION_COOKIE_NAME):
        return
    if operation in API_KEY_ONLY_MUTATION_OPERATIONS:
        return

    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    if not (origin or referer):
        raise HTTPException(status_code=403, detail="Origin or Referer is required for browser mutations.")
    source = origin or referer
    assert source is not None
    source_origin = _canonical_source_origin(source)
    trusted_origins = {
        canonical
        for value in settings.csrf_trusted_origins
        if (canonical := _canonical_source_origin(value)) is not None
    }
    if source_origin is None or source_origin not in trusted_origins:
        raise HTTPException(status_code=403, detail="Cross-origin request is not permitted.")
    host = request.headers.get("host", "").strip().lower()
    scheme = request.url.scheme.lower()
    if _peer_is_trusted(request.client.host if request.client else None, settings):
        forwarded_host = request.headers.get("x-forwarded-host", "").strip().lower()
        forwarded_proto = request.headers.get("x-forwarded-proto", "").strip().lower()
        if forwarded_host:
            if "," in forwarded_host:
                raise HTTPException(status_code=403, detail="Forwarded request host is not trusted.")
            host = forwarded_host
        if forwarded_proto:
            if "," in forwarded_proto or forwarded_proto not in {"http", "https"}:
                raise HTTPException(status_code=403, detail="Forwarded request scheme is not trusted.")
            scheme = forwarded_proto
    effective_origin = _canonical_http_origin(scheme, host)
    if effective_origin is None or effective_origin[1] not in {
        value.lower() for value in settings.csrf_trusted_hosts
    }:
        raise HTTPException(status_code=403, detail="Request host is not trusted.")
    if source_origin != effective_origin:
        raise HTTPException(status_code=403, detail="Cross-origin request is not permitted.")


def get_current_user(
    request: Request,
    session: Session = Depends(get_db_session),
    settings: SymgovAPISettings = Depends(get_settings),
) -> AuthenticatedUser | None:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    current = current_user_from_token(
        session,
        token,
        settings=settings,
        before_maintenance=lambda must_change_pin, session_purpose: enforce_session_access_state(
            must_change_pin,
            session_purpose,
            request.method,
            matched_route_template(request),
        ),
    )
    if current is not None:
        session.commit()
    return current


FORCED_PIN_ALLOWED_OPERATIONS = frozenset(
    {
        ("GET", "/api/v1/auth/me"),
        ("GET", "/api/auth/me"),
        ("POST", "/api/v1/auth/change-pin"),
        ("POST", "/api/auth/change-pin"),
        ("POST", "/api/v1/auth/logout"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/v1/profile"),
        ("POST", "/api/v1/auth/reauthenticate"),
        ("POST", "/api/auth/reauthenticate"),
    }
)


@dataclass(frozen=True)
class SessionAccessDecision:
    allowed: bool
    detail: str | None = None


def session_access_decision_for_state(
    must_change_pin: bool,
    session_purpose: str,
    method: object,
    route_template: object,
) -> SessionAccessDecision:
    if not must_change_pin and session_purpose != "credential_change":
        return SessionAccessDecision(allowed=True)
    operation = (str(method).upper(), route_template)
    if operation in FORCED_PIN_ALLOWED_OPERATIONS:
        return SessionAccessDecision(allowed=True)
    return SessionAccessDecision(
        allowed=False,
        detail="PIN change is required before accessing this operation.",
    )


def session_access_decision(
    current_user: AuthenticatedUser | None,
    method: object,
    route_template: object,
) -> SessionAccessDecision:
    if current_user is None:
        return SessionAccessDecision(allowed=True)
    return session_access_decision_for_state(
        current_user.must_change_pin,
        current_user.session_purpose,
        method,
        route_template,
    )


def enforce_session_access_state(
    must_change_pin: bool,
    session_purpose: str,
    method: object,
    route_template: object,
) -> None:
    decision = session_access_decision_for_state(must_change_pin, session_purpose, method, route_template)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.detail)


def require_session_access(
    request: Request,
    current_user: AuthenticatedUser | None = Depends(get_current_user),
) -> AuthenticatedUser | None:
    decision = session_access_decision(current_user, request.method, matched_route_template(request))
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.detail)
    return current_user


def require_user(current_user: AuthenticatedUser | None = Depends(require_session_access)) -> AuthenticatedUser:
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return current_user


def require_workspace_access(
    request: Request,
    current_user: AuthenticatedUser = Depends(require_user),
) -> AuthenticatedUser:
    policy = classify_workspace_policy(request.scope.get("method"), matched_route_template(request))
    if "admin" in current_user.roles or (policy == "reviewer_admin" and "reviewer" in current_user.roles):
        return current_user
    raise HTTPException(status_code=403, detail="Insufficient role for this operation.")


def require_role(role: str) -> Callable[[AuthenticatedUser], AuthenticatedUser]:
    return require_any_role({role})


def require_any_role(roles: set[str]) -> Callable[[AuthenticatedUser], AuthenticatedUser]:
    required_roles = {str(role).strip().lower() for role in roles if str(role).strip()}

    def dependency(current_user: AuthenticatedUser = Depends(require_user)) -> AuthenticatedUser:
        if not required_roles.intersection(current_user.roles):
            raise HTTPException(status_code=403, detail="Insufficient role for this operation.")
        return current_user

    return dependency


def require_authoritative_external_submission_user(
    request: Request,
    session: Session = Depends(get_db_session),
    settings: SymgovAPISettings = Depends(get_settings),
    _: AuthenticatedUser = Depends(require_any_role({"admin", "submitter"})),
) -> AuthenticatedUser:
    current_user = authoritative_user_from_token(
        session,
        request.cookies.get(SESSION_COOKIE_NAME, ""),
        settings=settings,
    )
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    enforce_session_access_state(
        current_user.must_change_pin,
        current_user.session_purpose,
        request.method,
        matched_route_template(request),
    )
    if not {"admin", "submitter"}.intersection(current_user.roles):
        raise HTTPException(status_code=403, detail="Insufficient role for this operation.")
    return current_user

def require_organization_session(
    current_user: AuthenticatedUser = Depends(require_user),
) -> AuthenticatedUser:
    if current_user.session_mode != "organization" or current_user.active_organization_id is None:
        raise HTTPException(status_code=403, detail="An organization-bound session is required.")
    return current_user


def require_organization_admin(
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> AuthenticatedUser:
    if current_user.organization_base_role != "admin":
        raise HTTPException(status_code=403, detail="Organization Admin privileges are required.")
    return current_user


def require_platform_admin(
    current_user: AuthenticatedUser = Depends(require_user),
) -> AuthenticatedUser:
    if not current_user.is_platform_admin:
        raise HTTPException(status_code=403, detail="Platform Admin privileges are required.")
    return current_user


def require_capability(capability: str) -> Callable[[AuthenticatedUser], AuthenticatedUser]:
    def dependency(current_user: AuthenticatedUser = Depends(require_organization_session)) -> AuthenticatedUser:
        if capability not in current_user.organization_capabilities:
            raise HTTPException(status_code=403, detail=f"Capability '{capability}' is required.")
        return current_user

    return dependency


def require_recent_step_up(
    request: Request,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_user),
) -> AuthenticatedUser:
    from .auth import _as_aware_utc, hash_session_token, utc_now
    from .models import UserSession

    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")

    token_hash = hash_session_token(token)
    session_row = session.query(UserSession).filter(
        UserSession.token_hash == token_hash,
        UserSession.revoked_at.is_(None),
    ).one_or_none()

    if session_row is None or session_row.recent_step_up_at is None:
        raise HTTPException(status_code=403, detail="Step-up reauthentication is required.")

    now = utc_now()
    # Enforce recent-step-up validity at elapsed 599 seconds and expiry at exactly 600 seconds.
    # We'll use > 600 for expiry and >= 600 for fail-closed.
    elapsed = (_as_aware_utc(now) - _as_aware_utc(session_row.recent_step_up_at)).total_seconds()
    if elapsed >= 600:
        raise HTTPException(status_code=403, detail="Step-up reauthentication has expired.")

    return current_user


def require_authoritative_user(
    request: Request,
    session: Session = Depends(get_db_session),
    settings: SymgovAPISettings = Depends(get_settings),
) -> AuthenticatedUser:
    from .auth import authoritative_user_from_token

    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    current_user = authoritative_user_from_token(session, token, settings=settings)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    enforce_session_access_state(
        current_user.must_change_pin,
        current_user.session_purpose,
        request.method,
        matched_route_template(request),
    )
    return current_user


I25_PROTECTED_OPERATIONS = frozenset(
    {
        ("POST", "/api/v1/admin/auth/throttles/recover"),
        ("POST", "/api/admin/auth/throttles/recover"),
        ("POST", "/api/v1/admin/users"),
        ("POST", "/api/admin/users"),
        ("PATCH", "/api/v1/admin/users/{user_id}"),
        ("PATCH", "/api/admin/users/{user_id}"),
        ("POST", "/api/v1/admin/users/{user_id}/subscription/upgrade"),
        ("POST", "/api/admin/users/{user_id}/subscription/upgrade"),
        ("POST", "/api/v1/admin/users/{user_id}/subscription/adjust"),
        ("POST", "/api/admin/users/{user_id}/subscription/adjust"),
        ("POST", "/api/v1/admin/users/{user_id}/subscription/cancel"),
        ("POST", "/api/admin/users/{user_id}/subscription/cancel"),
        ("DELETE", "/api/v1/admin/users/{user_id}"),
        ("DELETE", "/api/admin/users/{user_id}"),
        ("POST", "/api/v1/admin/users/{user_id}/reset-pin"),
        ("POST", "/api/admin/users/{user_id}/reset-pin"),
    }
)


def require_i25_protected_mutation(
    request: Request,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_authoritative_user),
) -> AuthenticatedUser:
    operation = (request.method.upper(), matched_route_template(request))
    if operation in I25_PROTECTED_OPERATIONS:
        require_recent_step_up(request, session, current_user)
    return current_user
