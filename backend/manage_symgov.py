#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from symgov_backend.api import serve_api
from symgov_backend.runtime import RuntimePersistenceBridge, check_database_health, check_storage_health


def parse_args():
    parser = argparse.ArgumentParser(description="Symgov backend bootstrap and health commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    seed_parser = subparsers.add_parser("seed-agent-definitions", help="Upsert baseline agent_definitions rows.")
    seed_parser.add_argument("--db-env-file", help="Path to the Symgov database env file.")

    db_parser = subparsers.add_parser("check-db", help="Run a small database health and inspection check.")
    db_parser.add_argument("--db-env-file", help="Path to the Symgov database env file.")
    db_parser.add_argument(
        "--migration-db",
        action="store_true",
        help="Use the migration database URL instead of the application URL.",
    )

    storage_parser = subparsers.add_parser("check-storage", help="Run a small storage health and inspection check.")
    storage_parser.add_argument("--storage-env-file", help="Path to the Symgov storage env file.")

    api_parser = subparsers.add_parser("serve-api", help="Run the Symgov API server.")
    api_parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind.")
    api_parser.add_argument("--port", type=int, default=8010, help="TCP port to bind.")

    return parser.parse_args()


def main():
    args = parse_args()

    if args.command == "seed-agent-definitions":
        bridge = RuntimePersistenceBridge(env_file=args.db_env_file)
        print(json.dumps({"operations": bridge.seed_agent_definitions()}, indent=2))
        return

    if args.command == "check-db":
        print(json.dumps(check_database_health(env_file=args.db_env_file, migration=args.migration_db), indent=2))
        return

    if args.command == "check-storage":
        print(json.dumps(check_storage_health(env_file=args.storage_env_file), indent=2))
        return

    if args.command == "serve-api":
        serve_api(host=args.host, port=args.port)
        return

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
