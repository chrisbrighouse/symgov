from __future__ import annotations

from collections.abc import Generator
from typing import Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .auth import AuthenticatedUser, current_user_from_token
from .db import create_session_factory
from .runtime import RuntimePersistenceBridge
from .settings import SymgovAPISettings, get_settings


def get_db_session(settings: SymgovAPISettings | None = None) -> Generator[Session, None, None]:
    resolved_settings = settings or get_settings()
    # Per-request engine: NullPool so we don't leak connections via discarded
    # session factories. The TCP cost is small compared to pool exhaustion risk.
    session_factory = create_session_factory(env_file=resolved_settings.db_env_file, nopool=True)
    with session_factory() as session:
        yield session


def get_runtime_bridge(settings: SymgovAPISettings | None = None) -> RuntimePersistenceBridge:
    resolved_settings = settings or get_settings()
    return RuntimePersistenceBridge(env_file=str(resolved_settings.db_env_file))


SESSION_COOKIE_NAME = "symgov_session"


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


def require_role(role: str) -> Callable[[AuthenticatedUser], AuthenticatedUser]:
    return require_any_role({role})


def require_any_role(roles: set[str]) -> Callable[[AuthenticatedUser], AuthenticatedUser]:
    required_roles = {str(role).strip().lower() for role in roles if str(role).strip()}

    def dependency(current_user: AuthenticatedUser = Depends(require_user)) -> AuthenticatedUser:
        if not required_roles.intersection(current_user.roles):
            raise HTTPException(status_code=403, detail="Insufficient role for this operation.")
        return current_user

    return dependency
