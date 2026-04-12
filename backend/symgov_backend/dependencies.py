from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from .db import create_session_factory
from .runtime import RuntimePersistenceBridge
from .settings import SymgovAPISettings, get_settings


def get_db_session(settings: SymgovAPISettings | None = None) -> Generator[Session, None, None]:
    resolved_settings = settings or get_settings()
    session_factory = create_session_factory(env_file=resolved_settings.db_env_file, pool_pre_ping=True)
    with session_factory() as session:
        yield session


def get_runtime_bridge(settings: SymgovAPISettings | None = None) -> RuntimePersistenceBridge:
    resolved_settings = settings or get_settings()
    return RuntimePersistenceBridge(env_file=str(resolved_settings.db_env_file))
