#!/usr/bin/env python3
"""Hannah's queue runner script, loaded by the agent worker.

The logic lives in `symgov_backend.hannah_runner`, which the Workspace routes
share; this file re-exports what the worker calls and keeps the CLI.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.hannah_runner import (  # noqa: E402,F401  (re-exported for the worker)
    CARD_SOURCE_TYPE,
    process_queue_item,
    seed_hannah_concept_queue_cards,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one Hannah concept curation card.")
    parser.add_argument("--queue-item", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--persist-db", action="store_true")
    parser.add_argument("--db-env-file")
    parser.add_argument("--storage-env-file")
    args = parser.parse_args()
    print(json.dumps(process_queue_item(args.queue_item, args.runtime_root, args.persist_db, args.db_env_file, args.storage_env_file), indent=2))


if __name__ == "__main__":
    main()
