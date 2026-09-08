---
name: deploy-release
description: Deploy a Symgov release to the production VPS - preflight gates, release worktree, migration, compose repoint, and post-deploy verification. Use when asked to deploy, release, or push to production, or to roll back a release.
---

# Deploy a Symgov release

Deploying is a live mutation. `CLAUDE.md` requires **Chris's explicit
approval** for each deployment; this skill does not grant it. Approval to
deploy once is not approval to deploy again.

Every step below is one command. Complete and verify each before starting
the next. If a step fails, stop and report rather than continuing.

## Facts about this deployment (verified 2026-09-08)

- Release directories: `/data/symgov-releases/stage11-<short-sha>/`, each a
  `git worktree`, not a clone.
- Compose file: `/docker/symgov-hermes/docker-compose.yml`. It hardcodes the
  release directory in **four** places (build context, image tag,
  `working_dir`, and the `applications-web` `dist/` mount).
- Containers: `symgov-hermes-api` (FastAPI, port 8010, **not published to the
  host**), `applications-web` (nginx), `symgov-postgres`, `symgov-minio`.
- The api container bind-mounts `/docker/openclaw-hz0t/data:/data`, so a new
  release worktree is visible inside the running container immediately.
- Backups: `/data/symgov-backups/`, `pg_dump -Fc` custom-format dumps.

## Preflight, from the repository root

1. Run the tests that cover the change. Backend:
   `PYTHONPATH=backend uv run --isolated --with-requirements backend/requirements.txt --with-requirements backend/requirements-test.txt python -m pytest <files> -q`.
   Frontend: `npm run test:frontend`. Then `npm run build`.
2. Run the secret-scan gate **as its own command**, never chained to the push
   with `&&` or `;` -- a chained run does not gate anything and a failing scan
   will still push:

       python3 scripts/secret_scan_added_lines.py

   It only sees tracked changes, so `git add` new files before trusting a
   clean result. A new file scans clean while untracked and can fail
   immediately after being committed.
3. `git push origin main` once the scan exits 0.

## Deploy

Let `SHA` be the short commit being deployed and `OLD` the release currently
in the compose file (`grep -n 'stage11-' /docker/symgov-hermes/docker-compose.yml`).

1. **Backup** -- required whenever a migration is involved; cheap enough to do
   regardless:

       docker exec symgov-postgres pg_dump -U symgov -d symgov -Fc > /data/symgov-backups/symgov-pre-<SHA>-$(date -u +%Y%m%dT%H%M%SZ).dump

   Confirm it is non-empty and comparable in size to the previous dump.

2. **Release worktree and frontend build.** `npm ci && npm run build` runs at
   the release **root**, not `frontend/` -- `package.json` and `vite.config.js`
   are at the root and vite is configured `root: 'frontend'`, `outDir: '../dist'`:

       git -C /docker/openclaw-hz0t/data/symgov worktree add --detach /data/symgov-releases/stage11-<SHA> <SHA>
       cd /data/symgov-releases/stage11-<SHA> && npm ci && npm run build && ls dist

3. **Migration**, only if the release adds one. It needs the migration role;
   the app role has no DDL rights and fails with `permission denied for
   schema public`:

       docker exec -e SYMGOV_ALEMBIC_USE_MIGRATION_DB=1 -w /data/symgov-releases/stage11-<SHA>/backend symgov-hermes-api python3 -m alembic upgrade <revision>
       docker exec symgov-postgres psql -U symgov -d symgov -c 'select * from alembic_version;'

   The whole upgrade runs in one transaction, so a failure rolls everything
   back and leaves the schema untouched. `Running upgrade` log lines are not
   evidence of committed work.

4. **Compose backup and repoint:**

       cp /docker/symgov-hermes/docker-compose.yml /docker/symgov-hermes/docker-compose.yml.pre-<SHA>-$(date -u +%Y%m%dT%H%M%SZ)
       sed -i 's/stage11-<OLD>/stage11-<SHA>/g' /docker/symgov-hermes/docker-compose.yml
       grep -n 'stage11-' /docker/symgov-hermes/docker-compose.yml
       docker compose -f /docker/symgov-hermes/docker-compose.yml config | grep -c '\${'

   The grep must show all four lines updated, and the count **must be 0**.
   Any other number means an unresolved `${...}`: seven telemetry variables
   live only in `/docker/symgov-hermes/.env` (mode 0600, do not delete it),
   and deploying with them empty silently blanks the Langfuse credentials.

5. **Deploy both services.** `applications-web` needs `up -d`, not `restart`,
   because its bind-mount path changed -- a restart keeps serving the old
   `dist/`:

       docker compose -f /docker/symgov-hermes/docker-compose.yml up -d --build symgov-api
       docker compose -f /docker/symgov-hermes/docker-compose.yml up -d applications-web

   Always pass `-f`; a bare `docker compose` acts on whatever compose file the
   current directory happens to contain.

## Verify

    docker exec symgov-hermes-api curl -fsS http://127.0.0.1:8010/api/v1/health

From the host this fails with connection refused even when healthy, because
no host port is published. Then confirm the frontend actually changed:

    docker inspect applications-web --format '{{range .Mounts}}{{if eq .Destination "/usr/share/nginx/html"}}{{.Source}}{{end}}{{end}}'
    docker exec applications-web sh -c 'wget -qO- http://127.0.0.1/ | grep -o "assets/index-[A-Za-z0-9_-]*\.js"'

The hash must match the one `npm run build` printed. Finally check the API
log for startup errors:
`docker logs --since 5m symgov-hermes-api 2>&1 | grep -viE ' 200 OK| 304 | 404 Not Found' | tail`.

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

## Rollback

Point the compose file back at the previous release directory and image tag
(or restore the `*.pre-*` backup) and `up -d` both services. Keep the previous
release directory in place -- it is the rollback target.

Never downgrade the schema below the visibility floor (`20260829_0033`) once
organization data exists; `20260829_0033`'s own `downgrade()` refuses. With
real tenant data, "roll back" means disable the organization flags and
redeploy at or above the floor, not a schema downgrade.

## Environment settings that are fatal in production

`settings.py` `_csv_setting` applies its declared default **only** in `local`
and `test`. In production an unset variable is an empty tuple no matter how
sensible the default looks, which has caused three separate outages. Treat
every `_csv_setting` as required in production.

`create_app()` runs two validators, so these are the complete set of
startup-fatal settings: `SYMGOV_AUTH_LOGIN_HASH_SECRET` (min 16 chars,
local/test placeholders rejected) and `SYMGOV_CSRF_TRUSTED_ORIGINS` /
`SYMGOV_CSRF_TRUSTED_HOSTS`. Both crash-loop the API on startup.

`SYMGOV_TRUSTED_PROXY_CIDRS` is not fatal but breaks **every authenticated
mutation** with `403 Cross-origin request is not permitted.` when empty, while
login still succeeds -- which makes the fault look far narrower than it is.
