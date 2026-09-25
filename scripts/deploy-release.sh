#!/usr/bin/env bash
# Deploy a Symgov release to production in one command.
#
#   scripts/deploy-release.sh                 plan only: what would deploy, no changes
#   scripts/deploy-release.sh --yes           deploy origin/main
#   scripts/deploy-release.sh --yes <sha>     deploy a specific pushed commit
#   scripts/deploy-release.sh --rollback <sha> [--yes]
#                                             repoint to an existing release dir
#
# Deploying is a live mutation and needs Chris's explicit approval each time
# (CLAUDE.md). Without --yes nothing is changed. The steps and their traps are
# documented in .claude/skills/deploy-release/SKILL.md; this script performs
# them in order and stops at the first failure.
set -euo pipefail

REPO=/docker/openclaw-hz0t/data/symgov
RELEASES=/data/symgov-releases
BACKUPS=/data/symgov-backups
COMPOSE=/docker/symgov-hermes/docker-compose.yml
API=symgov-hermes-api
WEB=applications-web
PG=symgov-postgres

MODE=deploy
CONFIRMED=0
TARGET=""

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

while [ $# -gt 0 ]; do
  case $1 in
    --yes) CONFIRMED=1 ;;
    --rollback) MODE=rollback ;;
    -h|--help) usage ;;
    -*) echo "Unknown option: $1" >&2; usage ;;
    *) [ -z "$TARGET" ] || usage; TARGET=$1 ;;
  esac
  shift
done

step() { printf '\n==> %s\n' "$*"; }
die() { printf '\nFAILED: %s\n' "$*" >&2; exit 1; }
compose() { docker compose -f "$COMPOSE" "$@"; }

current_release() {
  grep -o 'symgov-hermes-api:stage11-[0-9a-f]*' "$COMPOSE" | head -1 | sed 's/.*stage11-//'
}

release_head() {  # alembic head of a release dir, read from its scripts only
  docker exec -w "$RELEASES/stage11-$1/backend" "$API" python3 -m alembic heads 2>/dev/null \
    | awk '{print $1}'
}

db_revision() {
  docker exec "$PG" psql -U symgov -d symgov -tA -c 'select version_num from alembic_version;'
}

bundle_of() {  # the hash-named entry bundle a dist/ directory contains
  (cd "$1/dist/assets" && ls index-*.js) 2>/dev/null | head -1
}

repoint_and_restart() {  # $1 = from, $2 = to
  local from=$1 to=$2 ts
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  step "Back up compose file and repoint stage11-$from -> stage11-$to"
  cp "$COMPOSE" "$COMPOSE.pre-$to-$ts"
  sed -i "s/stage11-$from/stage11-$to/g" "$COMPOSE"
  grep -n 'stage11-' "$COMPOSE"
  [ "$(grep -c "stage11-$to" "$COMPOSE")" -eq 4 ] \
    || die "expected 4 compose lines on stage11-$to; restore $COMPOSE.pre-$to-$ts"
  [ "$(compose config | grep -c '\${' || true)" -eq 0 ] \
    || die "unresolved \${...} in compose config (check /docker/symgov-hermes/.env); restore $COMPOSE.pre-$to-$ts"

  step "Restart both services"
  # up -d, never restart: the web container's bind-mount path has changed.
  compose up -d --build symgov-api
  compose up -d "$WEB"
}

verify() {  # $1 = release sha expected live
  local rel=$RELEASES/stage11-$1 expected served mount i
  step "Verify"
  for i in $(seq 1 30); do
    if docker exec "$API" curl -fsS http://127.0.0.1:8010/api/v1/health >/dev/null 2>&1; then
      echo "API health: ok"; break
    fi
    [ "$i" -eq 30 ] && die "API health check failed after 60s: docker logs --since 5m $API"
    sleep 2
  done
  mount=$(docker inspect "$WEB" --format '{{range .Mounts}}{{if eq .Destination "/usr/share/nginx/html"}}{{.Source}}{{end}}{{end}}')
  echo "Web mount:  $mount"
  [ "$mount" = "$rel/dist" ] || die "web container serves $mount, expected $rel/dist"
  expected=$(bundle_of "$rel")
  served=$(docker exec "$WEB" sh -c 'wget -qO- http://127.0.0.1/' | grep -o 'index-[A-Za-z0-9_-]*\.js' | head -1)
  echo "Bundle:     served $served, built $expected"
  [ -n "$expected" ] && [ "$served" = "$expected" ] || die "served bundle does not match the release build"
  echo "Schema:     $(db_revision)"
  echo "API log since restart (non-2xx/3xx/404 lines):"
  docker logs --since 5m "$API" 2>&1 | grep -viE ' 200 OK| 304 | 404 Not Found' | tail -n 15 || true
  printf '\nLive: stage11-%s. Ask testers to hard-refresh (Ctrl+Shift+R) open tabs.\n' "$1"
}

OLD=$(current_release)
[ -n "$OLD" ] || die "could not read the live release from $COMPOSE"

# ---------------------------------------------------------------- rollback
if [ "$MODE" = rollback ]; then
  [ -n "$TARGET" ] || die "--rollback needs the release sha to return to"
  [ -d "$RELEASES/stage11-$TARGET/dist" ] || die "$RELEASES/stage11-$TARGET/dist does not exist"
  [ "$TARGET" != "$OLD" ] || die "stage11-$TARGET is already live"
  echo "Rollback plan: stage11-$OLD -> stage11-$TARGET"
  echo "  schema now:            $(db_revision)"
  echo "  schema head of target: $(release_head "$TARGET")"
  echo "  The schema is never downgraded; the target runs against the current schema."
  [ "$CONFIRMED" -eq 1 ] || { echo; echo "Plan only. Re-run with --yes to roll back."; exit 0; }
  repoint_and_restart "$OLD" "$TARGET"
  verify "$TARGET"
  exit 0
fi

# ------------------------------------------------------------------ deploy
step "Resolve the commit to deploy"
git -C "$REPO" fetch --quiet origin
SHA=$(git -C "$REPO" rev-parse --short=7 "${TARGET:-origin/main}^{commit}")
git -C "$REPO" merge-base --is-ancestor "$SHA" origin/main \
  || die "$SHA is not on origin/main; push it first"
[ "$SHA" != "$OLD" ] || die "stage11-$SHA is already live"
git -C "$REPO" merge-base --is-ancestor "$OLD" "$SHA" \
  || die "$SHA does not descend from live stage11-$OLD; use --rollback to go back"
REL=$RELEASES/stage11-$SHA
echo "Live:   stage11-$OLD"
echo "Deploy: stage11-$SHA  $(git -C "$REPO" log -1 --format='%s' "$SHA")"
echo "Commits since the live release:"
git -C "$REPO" log --oneline "$OLD..$SHA" | sed 's/^/  /'

MIGRATIONS=$(git -C "$REPO" diff --name-only --diff-filter=A "$OLD" "$SHA" -- backend/alembic/versions/ | grep '\.py$' || true)
if [ -n "$MIGRATIONS" ]; then
  echo "New migrations (will run against production):"
  echo "$MIGRATIONS" | sed 's/^/  /'
else
  echo "New migrations: none"
fi
if [ -e "$REL" ]; then echo "Note: $REL already exists and will be reused."; fi

if [ "$CONFIRMED" -ne 1 ]; then
  echo; echo "Plan only; nothing was changed. Re-run with --yes to deploy stage11-$SHA."
  exit 0
fi

LOG=$RELEASES/deploy-stage11-$SHA-$(date -u +%Y%m%dT%H%M%SZ).log
exec > >(tee -a "$LOG") 2>&1
echo "Logging to $LOG"

step "Back up the database"
PREV_DUMP=$(ls -1t "$BACKUPS"/symgov-pre-*.dump 2>/dev/null | head -1 || true)
DUMP=$BACKUPS/symgov-pre-$SHA-$(date -u +%Y%m%dT%H%M%SZ).dump
docker exec "$PG" pg_dump -U symgov -d symgov -Fc > "$DUMP"
NEW_SIZE=$(stat -c %s "$DUMP")
echo "$DUMP ($NEW_SIZE bytes)"
[ "$NEW_SIZE" -gt 0 ] || die "empty dump"
if [ -n "$PREV_DUMP" ]; then
  PREV_SIZE=$(stat -c %s "$PREV_DUMP")
  echo "previous: $PREV_DUMP ($PREV_SIZE bytes)"
  [ "$NEW_SIZE" -ge $((PREV_SIZE / 2)) ] || die "dump is under half the size of the previous one"
fi

step "Create the release worktree and build the frontend"
if [ ! -e "$REL" ]; then
  git -C "$REPO" worktree add --detach "$REL" "$SHA"
fi
# npm runs at the release root: vite is configured root 'frontend', outDir '../dist'.
(cd "$REL" && npm ci --no-audit --no-fund && npm run build)
BUNDLE=$(bundle_of "$REL")
[ -n "$BUNDLE" ] || die "build produced no dist/assets/index-*.js"
echo "Built bundle: $BUNDLE"

step "Migrate the schema if the release is ahead of the database"
HEAD=$(release_head "$SHA")
[ "$(echo "$HEAD" | wc -w)" -eq 1 ] || die "release has $(echo "$HEAD" | wc -w) alembic heads: $HEAD"
CURRENT=$(db_revision)
echo "database: $CURRENT  release head: $HEAD"
if [ "$CURRENT" != "$HEAD" ]; then
  # The migration role is required; the app role has no DDL rights. The whole
  # upgrade is one transaction, so a failure leaves the schema untouched.
  docker exec -e SYMGOV_ALEMBIC_USE_MIGRATION_DB=1 -w "$REL/backend" "$API" python3 -m alembic upgrade "$HEAD"
  [ "$(db_revision)" = "$HEAD" ] || die "schema is $(db_revision) after upgrade, expected $HEAD"
  echo "schema now $HEAD"
else
  echo "no migration needed"
fi

repoint_and_restart "$OLD" "$SHA"
verify "$SHA"
echo "Rollback if needed: scripts/deploy-release.sh --rollback $OLD --yes"
