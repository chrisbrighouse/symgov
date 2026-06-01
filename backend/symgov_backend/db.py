from __future__ import annotations

import os
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DEPS = BACKEND_ROOT / ".deps"

if os.environ.get("SYMGOV_DISABLE_BACKEND_DEPS", "").strip().lower() not in {"1", "true", "yes", "on"} and BACKEND_DEPS.exists() and str(BACKEND_DEPS) not in sys.path:
    sys.path.insert(0, str(BACKEND_DEPS))

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool


DEFAULT_ENV_FILE = Path("/data/.openclaw/workspace/symgov/.env.backend.database")


def read_env_file(env_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def get_database_url(env_file: str | os.PathLike[str] | None = None, migration: bool = False) -> str:
    if not migration:
        migration_flag = os.environ.get("SYMGOV_ALEMBIC_USE_MIGRATION_DB", "").strip().lower()
        if migration_flag in {"1", "true", "yes", "on"}:
            migration = True

    key = "SYMGOV_MIGRATION_DATABASE_URL" if migration else "SYMGOV_DATABASE_URL"
    if key in os.environ:
        return os.environ[key]

    path = Path(env_file) if env_file else DEFAULT_ENV_FILE
    values = read_env_file(path)
    if key not in values:
        raise RuntimeError(f"Missing required database setting: {key}")
    return values[key]


def normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    return database_url


def create_database_engine(
    env_file: str | os.PathLike[str] | None = None,
    migration: bool = False,
    nopool: bool = False,
    **engine_kwargs: object,
) -> Engine:
    database_url = normalize_database_url(get_database_url(env_file=env_file, migration=migration))
    if nopool:
        # NullPool opens a fresh connection per checkout and closes it on return.
        # Use this for short-lived engines created per-task (e.g. agent workers,
        # CLI commands, reconciliation runs). Avoids idle-connection accumulation
        # when many engines are created and never explicitly disposed.
        # NullPool ignores pool_size / max_overflow / pool_recycle, so don't pass them.
        engine_kwargs["poolclass"] = NullPool
        # pool_pre_ping is still meaningful (validates connection on checkout).
        engine_kwargs.setdefault("pool_pre_ping", True)
        # Drop any pool-sizing kwargs that NullPool can't consume.
        for unsupported in ("pool_size", "max_overflow", "pool_recycle", "pool_timeout"):
            engine_kwargs.pop(unsupported, None)
        return create_engine(database_url, **engine_kwargs)

    # Conservative defaults to keep total idle connection count low across many engines.
    # ~7 agent workers + runtime + dependencies engines * default(5+10) easily exhausts a
    # Postgres max_connections=100. With pool_size=2 + max_overflow=4 + pool_recycle=1800,
    # each engine stays small and recycles every 30 minutes. Callers can override.
    engine_kwargs.setdefault("pool_size", 2)
    engine_kwargs.setdefault("max_overflow", 4)
    engine_kwargs.setdefault("pool_recycle", 1800)
    return create_engine(database_url, **engine_kwargs)


def create_session_factory(
    env_file: str | os.PathLike[str] | None = None,
    migration: bool = False,
    nopool: bool = False,
    **engine_kwargs: object,
) -> sessionmaker[Session]:
    engine = create_database_engine(env_file=env_file, migration=migration, nopool=nopool, **engine_kwargs)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
