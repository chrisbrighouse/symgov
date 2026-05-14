from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .db import DEFAULT_ENV_FILE
from .runtime import DEFAULT_STORAGE_ENV_FILE


@dataclass(frozen=True)
class SymgovAPISettings:
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


def get_settings() -> SymgovAPISettings:
    return SymgovAPISettings()
