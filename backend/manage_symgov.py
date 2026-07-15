#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

from symgov_backend.agent_queue_worker import (
    DEFAULT_AGENT_ORDER,
    AgentQueueWorkerConfig,
    drain_agent_queues,
    process_agent_queues_once,
)
from symgov_backend.agent_queue_reconciliation import reconcile_agent_queue_state
from symgov_backend.api import serve_api
from symgov_backend.automation_policy import (
    evaluate_publication_automation_candidate,
    evaluate_publication_automation_candidates,
    evaluate_review_split_metadata_candidates,
)
from symgov_backend.catalog_api_auth import PLANNED_CATALOG_API_SCOPES
from symgov_backend.catalog_api_keys import (
    CatalogApiKeyCreateDTO,
    CatalogApiKeyDTO,
    create_catalog_api_key,
    list_catalog_api_keys,
    revoke_catalog_api_key,
)
from symgov_backend.db import create_session_factory
from symgov_backend.openclaw_sync import audit_openclaw_registration, reconcile_openclaw_registration
from symgov_backend.runtime import RuntimePersistenceBridge, check_database_health, check_storage_health
from symgov_backend.tracy_operations import (
    archive_agent_runtime_queue,
    backfill_provenance_libby_review_cases,
    find_provenance_libby_items_missing_review_cases,
    tracy_status_summary,
)


def _parse_aware_datetime(value: str) -> datetime:
    candidate = f"{value[:-1]}+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a valid ISO-8601 timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("must include a timezone")
    return parsed.astimezone(timezone.utc)


def _parse_scope(value: str) -> str:
    if value not in PLANNED_CATALOG_API_SCOPES:
        raise argparse.ArgumentTypeError("must be an allowed Catalog API scope")
    return value


def _parse_catalog_key_status(value: str) -> str:
    if value not in {"active", "disabled", "revoked"}:
        raise argparse.ArgumentTypeError("must be active, disabled, or revoked")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Symgov backend bootstrap and health commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    seed_parser = subparsers.add_parser("seed-agent-definitions", help="Upsert baseline agent_definitions rows.")
    seed_parser.add_argument("--db-env-file", help="Path to the Symgov database env file.")

    scott_seed_parser = subparsers.add_parser(
        "seed-scott-source-discovery",
        help="Upsert Scott source discovery memory rows.",
    )
    scott_seed_parser.add_argument("--db-env-file", help="Path to the Symgov database env file.")

    db_parser = subparsers.add_parser("check-db", help="Run a small database health and inspection check.")
    db_parser.add_argument("--db-env-file", help="Path to the Symgov database env file.")
    db_parser.add_argument(
        "--migration-db",
        action="store_true",
        help="Use the migration database URL instead of the application URL.",
    )

    storage_parser = subparsers.add_parser("check-storage", help="Run a small storage health and inspection check.")
    storage_parser.add_argument("--storage-env-file", help="Path to the Symgov storage env file.")

    openclaw_check_parser = subparsers.add_parser(
        "check-openclaw",
        help="Audit whether OpenClaw registration still matches the SymGov agent manifest.",
    )
    openclaw_check_parser.add_argument("--manifest", help="Path to the SymGov OpenClaw agent manifest.")
    openclaw_check_parser.add_argument("--config", help="Path to the OpenClaw config file.")

    openclaw_reconcile_parser = subparsers.add_parser(
        "reconcile-openclaw",
        help="Repair OpenClaw registration from the SymGov agent manifest.",
    )
    openclaw_reconcile_parser.add_argument("--manifest", help="Path to the SymGov OpenClaw agent manifest.")
    openclaw_reconcile_parser.add_argument("--config", help="Path to the OpenClaw config file.")

    api_parser = subparsers.add_parser("serve-api", help="Run the Symgov API server.")
    api_parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind.")
    api_parser.add_argument("--port", type=int, default=8010, help="TCP port to bind.")

    worker_parser = subparsers.add_parser("process-agent-queue", help="Process queued Symgov agent work.")
    worker_parser.add_argument(
        "--agent",
        action="append",
        choices=[*DEFAULT_AGENT_ORDER, "all"],
        help="Agent queue to process. Repeat for multiple agents, or use all.",
    )
    worker_parser.add_argument("--db-env-file", help="Path to the Symgov database env file.")
    worker_parser.add_argument("--storage-env-file", help="Path to the Symgov storage env file.")
    worker_parser.add_argument("--limit", type=int, default=10, help="Maximum queued items to process in one run.")
    worker_parser.add_argument(
        "--drain",
        action="store_true",
        help="Keep processing selected queues until a full cycle finds no queued work.",
    )
    worker_parser.add_argument("--max-cycles", type=int, default=50, help="Maximum drain cycles.")
    worker_parser.add_argument(
        "--agent-runtime",
        choices=["direct", "hermes"],
        default="direct",
        help="Queue execution runtime. direct preserves the current in-process runner behavior; hermes dispatches via Hermes.",
    )
    worker_parser.add_argument("--hermes-profile", default="symgov", help="Hermes profile for --agent-runtime hermes.")
    worker_parser.add_argument(
        "--hermes-timeout-seconds",
        type=int,
        default=600,
        help="Timeout for each Hermes specialist dispatch.",
    )
    worker_parser.add_argument(
        "--hermes-host-openclaw-root",
        default="/docker/openclaw-hz0t/data/.openclaw",
        help="Host path corresponding to /data/.openclaw for host-side Hermes dispatch.",
    )
    worker_parser.add_argument(
        "--hermes-container-openclaw-root",
        default="/data/.openclaw",
        help="Container path prefix to translate for host-side Hermes dispatch.",
    )

    reconcile_parser = subparsers.add_parser(
        "reconcile-agent-queue",
        help="Compare DB agent_queue_items with runtime queue JSON and optionally repair verified stale terminal statuses.",
    )
    reconcile_parser.add_argument("--db-env-file", help="Path to the Symgov database env file.")
    reconcile_parser.add_argument(
        "--agent",
        action="append",
        choices=[*DEFAULT_AGENT_ORDER, "all"],
        help="Agent queue to inspect. Repeat for multiple agents, or use all.",
    )
    reconcile_parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply verified runtime->DB queue status repairs. Omit for dry-run.",
    )
    reconcile_parser.add_argument(
        "--include-terminal-db-rows",
        action="store_true",
        help="Inspect terminal DB rows too. Default inspects active/stuck DB rows only.",
    )


    archive_parser = subparsers.add_parser(
        "archive-agent-runtime-queue",
        help="Archive terminal agent runtime queue JSON files out of active agent_queue_items.",
    )
    archive_parser.add_argument("--agent", required=True, choices=DEFAULT_AGENT_ORDER, help="Agent runtime queue to archive.")
    archive_parser.add_argument("--runtime-root", required=True, help="Agent runtime root containing agent_queue_items/.")
    archive_parser.add_argument("--archive-root", help="Destination archive directory. Defaults under runtime root.")
    archive_parser.add_argument(
        "--terminal-status",
        action="append",
        help="Terminal status to archive. Repeatable; defaults to known terminal statuses.",
    )
    archive_parser.add_argument("--apply", action="store_true", help="Move files. Omit for dry-run.")

    tracy_status_parser = subparsers.add_parser("tracy-status", help="Summarize Tracy provenance health and coverage.")
    tracy_status_parser.add_argument("--db-env-file", help="Path to the Symgov database env file.")
    tracy_status_parser.add_argument("--runtime-root", default="/data/.openclaw/workspaces/tracy/runtime")

    tracy_backfill_parser = subparsers.add_parser(
        "backfill-tracy-libby-review-cases",
        help="Create missing libby_disposition_review cases for Libby provenance handoffs.",
    )
    tracy_backfill_parser.add_argument("--db-env-file", help="Path to the Symgov database env file.")
    tracy_backfill_parser.add_argument("--origin-batch-id", help="Restrict backfill to one origin batch id.")
    tracy_backfill_parser.add_argument("--list-only", action="store_true", help="Only list candidate missing cases.")
    tracy_backfill_parser.add_argument("--apply", action="store_true", help="Apply the backfill. Omit for dry-run.")

    gate_parser = subparsers.add_parser(
        "evaluate-automation-gates",
        help="Evaluate conservative publication automation gates without creating publication work.",
    )
    gate_parser.add_argument("--db-env-file", help="Path to the Symgov database env file.")
    gate_parser.add_argument("--classification-id", help="Evaluate one classification_records id instead of recent candidates.")
    gate_parser.add_argument("--limit", type=int, default=50, help="Maximum current classification records to evaluate.")
    gate_parser.add_argument(
        "--review-split-metadata",
        action="store_true",
        help="Evaluate split-symbol name/category/discipline metadata instead of classification records.",
    )

    create_key_parser = subparsers.add_parser(
        "create-catalog-api-key",
        help="Create a Catalog API key and emit its one-time secret to stdout.",
        description="Create a Catalog API key. Successful stdout contains one-time secret material.",
        epilog="WARNING: rawKey is one-time secret material written to stdout. Capture and store it securely.",
    )
    create_key_parser.add_argument("--customer", required=True, help="Customer name.")
    create_key_parser.add_argument("--integration", required=True, help="Integration name.")
    create_key_parser.add_argument(
        "--scope",
        action="append",
        required=True,
        type=_parse_scope,
        metavar="SCOPE",
        help="Allowed Catalog API scope. Repeat for multiple scopes.",
    )
    create_key_parser.add_argument(
        "--expires-at",
        type=_parse_aware_datetime,
        help="Optional future ISO-8601 timestamp with timezone (trailing Z supported).",
    )
    create_key_parser.add_argument("--db-env-file", help="Path to the Symgov database env file.")

    list_keys_parser = subparsers.add_parser(
        "list-catalog-api-keys",
        help="List secret-safe Catalog API key metadata.",
    )
    list_keys_parser.add_argument("--customer", help="Filter by exact customer name.")
    list_keys_parser.add_argument(
        "--status",
        type=_parse_catalog_key_status,
        metavar="{active,disabled,revoked}",
        help="Filter by status.",
    )
    list_keys_parser.add_argument("--db-env-file", help="Path to the Symgov database env file.")

    revoke_key_parser = subparsers.add_parser(
        "revoke-catalog-api-key",
        help="Revoke a Catalog API key by immutable ID and exact displayed prefix.",
    )
    revoke_key_parser.add_argument("--key-id", required=True, help="Immutable Catalog API key UUID.")
    revoke_key_parser.add_argument(
        "--confirm-prefix",
        required=True,
        help="Exact displayed key prefix used as fail-closed confirmation.",
    )
    revoke_key_parser.add_argument("--db-env-file", help="Path to the Symgov database env file.")

    return parser


def parse_args(argv: Sequence[str] | None = None):
    return build_parser().parse_args(argv)


def _safe_key_payload(key: CatalogApiKeyDTO) -> dict[str, object]:
    return {
        "keyId": str(key.id),
        "keyPrefix": key.key_prefix,
        "customer": key.customer_name,
        "integration": key.integration_name,
        "scopes": list(key.scopes),
        "status": key.status,
        "expiresAt": key.expires_at.isoformat() if key.expires_at else None,
        "lastUsedAt": key.last_used_at.isoformat() if key.last_used_at else None,
        "createdAt": key.created_at.isoformat(),
        "updatedAt": key.updated_at.isoformat(),
        "revokedAt": key.revoked_at.isoformat() if key.revoked_at else None,
    }


def _created_key_payload(created: CatalogApiKeyCreateDTO) -> dict[str, object]:
    key = created.key
    return {
        "keyId": str(key.id),
        "keyPrefix": key.key_prefix,
        "customer": key.customer_name,
        "integration": key.integration_name,
        "scopes": list(key.scopes),
        "status": key.status,
        "expiresAt": key.expires_at.isoformat() if key.expires_at else None,
        "createdAt": key.created_at.isoformat(),
        "rawKey": created.raw_key,
    }


def _run_catalog_command(
    *,
    env_file: str | None,
    operation: Callable[[Any], object],
    serialize: Callable[[Any], object],
) -> int:
    """Run one short-lived lifecycle transaction and print only after commit."""
    session = None
    try:
        session_factory = create_session_factory(env_file=env_file, nopool=True)
        session = session_factory()
        result = operation(session)
        payload = json.dumps(serialize(result), indent=2)
        session.commit()
    except Exception:
        cleanup_failed = False
        if session is not None:
            try:
                session.rollback()
            except Exception:
                cleanup_failed = True
            try:
                session.close()
            except Exception:
                cleanup_failed = True
        print("Catalog API key operation failed.", file=sys.stderr)
        if cleanup_failed:
            print("Catalog API key session cleanup failed.", file=sys.stderr)
        return 1

    try:
        session.close()
    except Exception:
        print(payload)
        print("Catalog API key session cleanup failed.", file=sys.stderr)
        return 1

    print(payload)
    return 0


def main(argv: Sequence[str] | None = None):
    args = parse_args(argv)

    if args.command == "create-catalog-api-key":
        return _run_catalog_command(
            env_file=args.db_env_file,
            operation=lambda session: create_catalog_api_key(
                session,
                customer_name=args.customer,
                integration_name=args.integration,
                scopes=args.scope,
                expires_at=args.expires_at,
            ),
            serialize=lambda result: _created_key_payload(result),
        )

    if args.command == "list-catalog-api-keys":
        return _run_catalog_command(
            env_file=args.db_env_file,
            operation=lambda session: list_catalog_api_keys(
                session,
                customer_name=args.customer,
                status=args.status,
            ),
            serialize=lambda result: {"keys": [_safe_key_payload(key) for key in result]},
        )

    if args.command == "revoke-catalog-api-key":
        return _run_catalog_command(
            env_file=args.db_env_file,
            operation=lambda session: revoke_catalog_api_key(
                session,
                args.key_id,
                key_prefix=args.confirm_prefix,
            ),
            serialize=lambda result: _safe_key_payload(result),
        )

    if args.command == "seed-agent-definitions":
        bridge = RuntimePersistenceBridge(env_file=args.db_env_file)
        print(json.dumps({"operations": bridge.seed_agent_definitions()}, indent=2))
        return

    if args.command == "seed-scott-source-discovery":
        bridge = RuntimePersistenceBridge(env_file=args.db_env_file)
        print(json.dumps({"operations": bridge.seed_scott_source_discovery_sites()}, indent=2))
        return

    if args.command == "check-db":
        print(json.dumps(check_database_health(env_file=args.db_env_file, migration=args.migration_db), indent=2))
        return

    if args.command == "check-storage":
        print(json.dumps(check_storage_health(env_file=args.storage_env_file), indent=2))
        return

    if args.command == "check-openclaw":
        print(json.dumps(audit_openclaw_registration(manifest_path=args.manifest, config_path=args.config), indent=2))
        return

    if args.command == "reconcile-openclaw":
        print(json.dumps(reconcile_openclaw_registration(manifest_path=args.manifest, config_path=args.config), indent=2))
        return

    if args.command == "serve-api":
        serve_api(host=args.host, port=args.port)
        return

    if args.command == "process-agent-queue":
        requested = args.agent or ["libby"]
        agents = DEFAULT_AGENT_ORDER if "all" in requested else tuple(requested)
        config = AgentQueueWorkerConfig(
            agents=agents,
            db_env_file=Path(args.db_env_file) if args.db_env_file else None,
            storage_env_file=Path(args.storage_env_file) if args.storage_env_file else None,
            limit=args.limit,
            drain=args.drain,
            agent_runtime=args.agent_runtime,
            hermes_profile=args.hermes_profile,
            hermes_timeout_seconds=args.hermes_timeout_seconds,
            hermes_host_openclaw_root=Path(args.hermes_host_openclaw_root),
            hermes_container_openclaw_root=Path(args.hermes_container_openclaw_root),
        )
        result = drain_agent_queues(config, max_cycles=args.max_cycles) if args.drain else process_agent_queues_once(config)
        print(json.dumps(result, indent=2))
        return

    if args.command == "reconcile-agent-queue":
        requested = args.agent or ["all"]
        agents = DEFAULT_AGENT_ORDER if "all" in requested else tuple(requested)
        result = reconcile_agent_queue_state(
            db_env_file=args.db_env_file,
            agents=agents,
            apply=args.apply,
            active_only=not args.include_terminal_db_rows,
        )
        print(json.dumps(result, indent=2, default=str))
        return


    if args.command == "archive-agent-runtime-queue":
        result = archive_agent_runtime_queue(
            agent=args.agent,
            runtime_root=args.runtime_root,
            archive_root=args.archive_root,
            terminal_statuses=set(args.terminal_status) if args.terminal_status else None,
            dry_run=not args.apply,
        )
        print(json.dumps(result, indent=2))
        return

    if args.command == "tracy-status":
        result = tracy_status_summary(db_env_file=args.db_env_file, runtime_root=args.runtime_root)
        print(json.dumps(result, indent=2))
        return

    if args.command == "backfill-tracy-libby-review-cases":
        if args.list_only:
            result = {
                "items": find_provenance_libby_items_missing_review_cases(
                    db_env_file=args.db_env_file,
                    origin_batch_id=args.origin_batch_id,
                )
            }
        else:
            result = backfill_provenance_libby_review_cases(
                db_env_file=args.db_env_file,
                origin_batch_id=args.origin_batch_id,
                dry_run=not args.apply,
            )
        print(json.dumps(result, indent=2, default=str))
        return

    if args.command == "evaluate-automation-gates":
        if args.review_split_metadata:
            result = evaluate_review_split_metadata_candidates(db_env_file=args.db_env_file, limit=args.limit)
        elif args.classification_id:
            result = evaluate_publication_automation_candidate(args.classification_id, db_env_file=args.db_env_file)
        else:
            result = evaluate_publication_automation_candidates(db_env_file=args.db_env_file, limit=args.limit)
        print(json.dumps(result, indent=2))
        return

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
