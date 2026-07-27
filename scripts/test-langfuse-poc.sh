#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd -P)
TIMEOUT_SECONDS=${SYMGOV_LANGFUSE_TEST_TIMEOUT_SECONDS:-120}

case $TIMEOUT_SECONDS in
  ''|*[!0-9]*)
    echo "SYMGOV_LANGFUSE_TEST_TIMEOUT_SECONDS must be a positive integer" >&2
    exit 2
    ;;
esac
case $TIMEOUT_SECONDS in
  *[1-9]*) ;;
  *)
    echo "SYMGOV_LANGFUSE_TEST_TIMEOUT_SECONDS must be a positive integer" >&2
    exit 2
    ;;
esac

cd "$ROOT_DIR"

exec timeout "${TIMEOUT_SECONDS}s" env PYTHONPATH="$ROOT_DIR/langfuse-poc/scripts" \
  uv run --isolated \
  --with-requirements "$ROOT_DIR/backend/requirements-test.txt" \
  python -m pytest "$ROOT_DIR/langfuse-poc/tests/test_synthetic_contract.py" -q "$@"
