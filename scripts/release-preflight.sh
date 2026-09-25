#!/usr/bin/env bash
# Repository-side release gates, in one command, from any directory:
#
#   scripts/release-preflight.sh [pytest targets...]
#
# Runs the frontend tests, the frontend build, the backend tests given (by
# default every tests/test_*.py changed since the live release), and the
# secret scan. Exits non-zero at the first failure. It pushes nothing and
# changes nothing outside the repo's own dist/ build output.
set -euo pipefail

REPO=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd -P)
COMPOSE=/docker/symgov-hermes/docker-compose.yml
cd "$REPO"

step() { printf '\n==> %s\n' "$*"; }

if [ $# -gt 0 ]; then
  TESTS=("$@")
else
  LIVE=$(grep -o 'symgov-hermes-api:stage11-[0-9a-f]*' "$COMPOSE" 2>/dev/null | head -1 | sed 's/.*stage11-//' || true)
  TESTS=()
  if [ -n "$LIVE" ]; then
    # Changed since the live release, committed or not, and still present.
    while IFS= read -r f; do
      [ -f "$f" ] && TESTS+=("$f")
    done < <({ git diff --name-only "$LIVE" -- 'tests/test_*.py'; git ls-files --others --exclude-standard -- 'tests/test_*.py'; } | sort -u)
  fi
fi

step "Frontend tests"
npm run --silent test:frontend 2>&1 | tail -n 9

step "Frontend build"
npm run --silent build 2>&1 | grep -vE 'chunks are larger|dynamic import|manualChunks|chunkSizeWarningLimit' | tail -n 4

step "Backend tests"
if [ ${#TESTS[@]} -eq 0 ]; then
  echo "No backend test files changed since the live release; none run."
  echo "Pass pytest targets explicitly to run some."
else
  printf '  %s\n' "${TESTS[@]}"
  PYTHONPATH=backend uv run --isolated \
    --with-requirements backend/requirements.txt \
    --with-requirements backend/requirements-test.txt \
    python -m pytest "${TESTS[@]}" -q 2>&1 | tail -n 3
fi

step "Secret scan (tracked changes only: git add new files first)"
python3 scripts/secret_scan_added_lines.py

printf '\nPreflight green.\n'
