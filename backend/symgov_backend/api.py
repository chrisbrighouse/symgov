from __future__ import annotations

import argparse
import json

from .app import app, create_app
from .settings import get_settings


def serve_api(host: str | None = None, port: int | None = None) -> None:
    settings = get_settings()
    bind_host = host or settings.host
    bind_port = port or settings.port

    try:
        import uvicorn
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError(
            "uvicorn is not installed. Add backend dependencies from requirements.txt before running serve-api."
        ) from exc

    print(json.dumps({"service": settings.service_name, "host": bind_host, "port": bind_port}, indent=2))
    uvicorn.run("symgov_backend.app:app", host=bind_host, port=bind_port, reload=False, factory=False)


def parse_args() -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Run the Symgov API server.")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    serve_api(host=args.host, port=args.port)


__all__ = ["app", "create_app", "serve_api"]


if __name__ == "__main__":
    main()
