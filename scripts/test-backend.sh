#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PORTABLE_TIMEOUT=${SYMGOV_BACKEND_TIMEOUT_SECONDS-300}
EXTERNAL_TIMEOUT=${SYMGOV_EXTERNAL_TEST_TIMEOUT_SECONDS-120}

validate_timeout() {
  timeout_value=$1
  timeout_variable=$2
  case $timeout_value in
    ''|*[!0-9]*)
      echo "$timeout_variable must be a positive integer" >&2
      exit 2
      ;;
  esac
  case $timeout_value in
    *[1-9]*) ;;
    *)
      echo "$timeout_variable must be a positive integer" >&2
      exit 2
      ;;
  esac
}

validate_timeout "$PORTABLE_TIMEOUT" SYMGOV_BACKEND_TIMEOUT_SECONDS
validate_timeout "$EXTERNAL_TIMEOUT" SYMGOV_EXTERNAL_TEST_TIMEOUT_SECONDS

run_pytest() {
  timeout_seconds=$1
  shift
  timeout "${timeout_seconds}s" env PYTHONPATH="$ROOT_DIR/backend" \
    uv run --isolated \
    --with-requirements "$ROOT_DIR/backend/requirements.txt" \
    --with-requirements "$ROOT_DIR/backend/requirements-test.txt" \
    python -m pytest "$@"
}

run_portable() {
  run_pytest "$PORTABLE_TIMEOUT" \
    "$ROOT_DIR/tests" \
    --ignore="$ROOT_DIR/tests/test_daisy_rights_review_coordination.py" \
    --ignore="$ROOT_DIR/tests/test_dxf_phase1.py" \
    --ignore="$ROOT_DIR/tests/test_libby_duplicate_triage.py" \
    --ignore="$ROOT_DIR/tests/test_libby_symbol_vision.py" \
    --ignore="$ROOT_DIR/tests/test_zip_phase2.py" \
    -m "not external_workspace" -q "$@"
}

run_external() {
  run_pytest "$EXTERNAL_TIMEOUT" "$ROOT_DIR/tests/test_daisy_rights_review_coordination.py" -q "$@"
  run_pytest "$EXTERNAL_TIMEOUT" "$ROOT_DIR/tests/test_dxf_phase1.py" -q "$@"
  run_pytest "$EXTERNAL_TIMEOUT" "$ROOT_DIR/tests/test_libby_duplicate_triage.py" -q "$@"
  run_pytest "$EXTERNAL_TIMEOUT" "$ROOT_DIR/tests/test_libby_symbol_vision.py" -q "$@"
  run_pytest "$EXTERNAL_TIMEOUT" "$ROOT_DIR/tests/test_zip_phase2.py" -q "$@"
  run_pytest "$EXTERNAL_TIMEOUT" "$ROOT_DIR/tests/test_published_symbol_review_workflow.py" \
    -m external_workspace -q "$@"
  run_pytest "$EXTERNAL_TIMEOUT" "$ROOT_DIR/tests/test_vlad_hardening.py" \
    -m external_workspace -q "$@"
}

cd "$ROOT_DIR"
case ${1:-} in
  --external)
    shift
    run_external "$@"
    ;;
  --full)
    shift
    run_portable "$@"
    run_external "$@"
    ;;
  *)
    run_portable "$@"
    ;;
esac
