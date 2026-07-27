#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd -P)
OUTPUT_DIR=${SYMGOV_BUILD_OUT_DIR:-"${TMPDIR:-/tmp}/symgov-build"}
TIMEOUT_SECONDS=${SYMGOV_FRONTEND_BUILD_TIMEOUT_SECONDS:-120}

if [ "$#" -ne 0 ]; then
  echo "build-frontend-isolated.sh does not accept CLI arguments; use SYMGOV_BUILD_OUT_DIR to select an output directory" >&2
  exit 2
fi

case $TIMEOUT_SECONDS in
  ''|*[!0-9]*)
    echo "SYMGOV_FRONTEND_BUILD_TIMEOUT_SECONDS must be a positive integer" >&2
    exit 2
    ;;
esac
case $TIMEOUT_SECONDS in
  *[1-9]*) ;;
  *)
    echo "SYMGOV_FRONTEND_BUILD_TIMEOUT_SECONDS must be a positive integer" >&2
    exit 2
    ;;
esac

case $OUTPUT_DIR in
  /*) ;;
  *)
    echo "SYMGOV_BUILD_OUT_DIR must be an absolute path outside the repository" >&2
    exit 2
    ;;
esac

CANONICAL_ROOT=$(realpath -m -- "$ROOT_DIR")
CANONICAL_OUTPUT=$(realpath -m -- "$OUTPUT_DIR")
case $CANONICAL_OUTPUT in
  /|"$CANONICAL_ROOT"|"$CANONICAL_ROOT"/*)
    echo "Refusing to write an isolated build inside the repository: $OUTPUT_DIR" >&2
    exit 2
    ;;
esac

cd "$ROOT_DIR"
exec timeout "${TIMEOUT_SECONDS}s" npm run build -- --outDir "$CANONICAL_OUTPUT"