from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .db import DEFAULT_ENV_FILE


@dataclass(frozen=True)
class SymgovAPISettings:
    service_name: str = "symgov-api"
    api_prefix: str = "/api/v1"
    host: str = "0.0.0.0"
    port: int = 8010
    submission_pin: str = os.environ.get("SYMGOV_API_PIN", "4590")
    db_env_file: Path = DEFAULT_ENV_FILE


def get_settings() -> SymgovAPISettings:
    return SymgovAPISettings()
