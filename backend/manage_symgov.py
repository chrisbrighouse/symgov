#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

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
from symgov_backend.openclaw_sync import audit_openclaw_registration, reconcile_openclaw_registration
from symgov_backend.runtime import RuntimePersistenceBridge, check_database_health, check_storage_health
from symgov_backend.tracy_operations import (
    archive_agent_runtime_queue,
    backfill_provenance_libby_review_cases,
    find_provenance_libby_items_missing_review_cases,
    tracy_status_summary,
)


def parse_args():
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

    return parser.parse_args()


def main():
    args = parse_args()

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
    main()
