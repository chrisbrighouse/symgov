---
name: deploy-release
description: Deploy a Symgov release to the production VPS - preflight gates, release worktree, migration, compose repoint, and post-deploy verification. Use when asked to deploy, release, or push to production, or to roll back a release.
---

# Deploy a Symgov release

Deploying is a live mutation. `CLAUDE.md` requires **Chris's explicit
approval** for each deployment; this skill does not grant it. Approval to
deploy once is not approval to deploy again. Pushing needs approval too.

Two scripts do the work. The whole cycle is four commands, and only the last
one touches production:

| # | Who | Command | Changes |
|---|-----|---------|---------|
| 1 | Claude | `scripts/release-preflight.sh [pytest targets]` | nothing |
| 2 | Claude | `git push origin main` (after approval) | origin |
| 3 | Claude | `scripts/deploy-release.sh` | nothing (plan) |
| 4 | Chris | `! scripts/deploy-release.sh --yes` | **production** |

Step 4 is Chris's to run, typed with the `!` prefix so the output lands in
the conversation. The agent cannot run it: the permission gate denies `npm`
under `/data/symgov-releases`, and production database reads are blocked for
the agent. Do not try to work around either.

## 1. Preflight

    scripts/release-preflight.sh

It runs the frontend tests, `npm run build`, the backend tests, and the
secret scan, and stops at the first failure. With no arguments, the backend
tests it runs are every `tests/test_*.py` changed since the **live** release,
committed or untracked. Pass pytest targets to widen that when the change
reaches code whose tests did not change, e.g.
`scripts/release-preflight.sh tests/test_platform_admin_api.py tests/test_auth_routes.py`.

The secret scan only sees **tracked** changes, so `git add` new files first.
A new file scans clean while untracked and can fail once committed. Commit,
if asked, before running it for the release.

## 2. Push

Only after preflight is green and Chris has approved. Run it on its own, never
chained after the scan with `&&` or `;`. The deploy script refuses any
commit that is not on `origin/main`.

## 3. Plan

    scripts/deploy-release.sh            # origin/main
    scripts/deploy-release.sh <sha>      # a specific pushed commit

Read-only. Prints the live release, the release to deploy, the commits
between them, and any **new migrations**. Show Chris the plan, and call out
migrations explicitly, before asking him to run step 4. It refuses a commit
that is already live, not pushed, or older than the live release.

## 4. Deploy (Chris)

    ! scripts/deploy-release.sh --yes

In order, stopping at the first failure:

1. `pg_dump -Fc` to `/data/symgov-backups/symgov-pre-<sha>-<ts>.dump`. It
   fails if the dump is empty or under half the size of the previous one.
2. `git worktree add --detach /data/symgov-releases/stage11-<sha>`, then
   `npm ci && npm run build` at the release **root** (vite is `root:
   'frontend'`, `outDir: '../dist'`). An existing release dir is reused.
3. If the release's alembic head differs from `alembic_version`, it runs
   `alembic upgrade <head>` with `SYMGOV_ALEMBIC_USE_MIGRATION_DB=1`. The app
   role has no DDL rights. The upgrade is one transaction, so a failure leaves
   the schema untouched and the script stops **before** touching compose.
   Multiple heads abort.
4. Copies the compose file to `docker-compose.yml.pre-<sha>-<ts>`, repoints
   all four `stage11-` references, and requires four matches and zero
   unresolved `${...}` in `compose config`. Seven telemetry variables live
   only in `/docker/symgov-hermes/.env` (mode 0600, do not delete it), and
   deploying with them empty silently blanks the Langfuse credentials.
5. `up -d --build symgov-api`, then `up -d applications-web`. Never
   `restart`: the web container's bind-mount path changed, and a restart keeps
   serving the old `dist/`.
6. Verifies: API health from inside the container (retried for 60s; no host
   port is published, so a host `curl` is refused even when healthy); the
   web mount is the new `dist/`; the served `index-*.js` equals the built one;
   the schema revision; and the API log since restart.

The full output is also written to
`/data/symgov-releases/deploy-stage11-<sha>-<ts>.log`. If Chris's terminal
output is truncated, read that log.

On success, report the release, bundle hash, schema revision and anything
unusual in the log lines. Then update the resume-point memory.

## If a step fails

The script prints `FAILED: <reason>`. Before the compose repoint, production
is unchanged apart from a new dump, a release dir and possibly a committed
migration. After the repoint, the message names the compose backup to
restore. Fix the cause, then re-run `--yes`. A partly built release dir is
reused, and a completed migration is detected and skipped.

## Rollback

    scripts/deploy-release.sh --rollback <old-sha>        # plan
    ! scripts/deploy-release.sh --rollback <old-sha> --yes

It repoints compose to an existing release dir, restarts both services, and
verifies. It never downgrades the schema, and the plan prints both revisions
so the mismatch is visible. Keep old release dirs; they are the rollback
targets. The previous release is printed at the end of every deploy.

Never downgrade the schema below the visibility floor (`20260829_0033`) once
organization data exists; `20260829_0033`'s own `downgrade()` refuses. With
real tenant data, "roll back" means disable the organization flags and
redeploy at or above the floor, not a schema downgrade.

## Facts about this deployment (verified 2026-09-25)

- `/data` is a symlink to `/docker/openclaw-hz0t/data`. The api container
  bind-mounts the same tree at `/data`, so a new release worktree is visible
  inside the running container immediately.
- Release dirs: `/data/symgov-releases/stage11-<short-sha>/`, each a
  `git worktree`, not a clone.
- Compose file: `/docker/symgov-hermes/docker-compose.yml`, which hardcodes
  the release in four places (build context, image tag, `working_dir`, the
  `applications-web` `dist/` mount). Always pass `-f`; a bare
  `docker compose` acts on whatever compose file the current directory holds.
- Containers: `symgov-hermes-api` (FastAPI, port 8010, unpublished),
  `applications-web` (nginx), `symgov-postgres`, `symgov-minio`.

## Before believing a bug report about a fresh deployment

**Suspect a stale browser bundle first.** Assets are hash-named, but an open
tab keeps running the JavaScript it loaded. This has produced two convincing
false alarms -- a blank page, and a "fix that did not work" whose logs showed
the old bundle's requests interleaved with the new one's.

Prove which bundle the browser is running before investigating anything else:
grep the old and new files under `/data/symgov-releases/*/dist/assets/` for a
string the fix changed. If they differ, ask for a hard refresh (Ctrl+Shift+R)
and for other Symgov tabs to be closed.

Note also that `nginx.conf` uses `try_files $uri $uri/ /index.html`, so a
missing asset returns `index.html` as `200 text/html` rather than a 404,
which surfaces in the browser as a module-script MIME error.

## Environment settings that are fatal in production

`settings.py` `_csv_setting` applies its declared default **only** in `local`
and `test`. In production an unset variable is an empty tuple no matter how
sensible the default looks, which has caused three separate outages. Treat
every `_csv_setting` as required in production.

`create_app()` runs two validators, so these are the complete set of
startup-fatal settings: `SYMGOV_AUTH_LOGIN_HASH_SECRET` (min 16 chars,
local/test placeholders rejected) and `SYMGOV_CSRF_TRUSTED_ORIGINS` /
`SYMGOV_CSRF_TRUSTED_HOSTS`. Both crash-loop the API on startup. The
script's health check catches this; roll back, then fix the env.

`SYMGOV_TRUSTED_PROXY_CIDRS` is not fatal but breaks **every authenticated
mutation** with `403 Cross-origin request is not permitted.` when empty, while
login still succeeds -- which makes the fault look far narrower than it is.
The health check does **not** catch this one.
