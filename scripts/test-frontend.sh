#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd -P)
TIMEOUT_SECONDS=${SYMGOV_FRONTEND_TEST_TIMEOUT_SECONDS:-120}

case $TIMEOUT_SECONDS in
  ''|*[!0-9]*)
    echo "SYMGOV_FRONTEND_TEST_TIMEOUT_SECONDS must be a positive integer" >&2
    exit 2
    ;;
esac
case $TIMEOUT_SECONDS in
  *[1-9]*) ;;
  *)
    echo "SYMGOV_FRONTEND_TEST_TIMEOUT_SECONDS must be a positive integer" >&2
    exit 2
    ;;
esac

cd "$ROOT_DIR"

exec timeout "${TIMEOUT_SECONDS}s" node --test "$@" frontend/src/*.test.js
