from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .auth import AuthenticatedUser, current_user_from_token
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


def get_current_user(request: Request, session: Session = Depends(get_db_session)) -> AuthenticatedUser | None:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    current = current_user_from_token(session, token)
    if current is not None:
        session.commit()
    return current


def require_user(current_user: AuthenticatedUser | None = Depends(get_current_user)) -> AuthenticatedUser:
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
