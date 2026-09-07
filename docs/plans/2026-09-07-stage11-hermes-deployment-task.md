# Stage 11 production deployment — Hermes kanban task draft (not yet created)

**Status: draft for Chris's review. Not yet submitted to the `symgov` Hermes
kanban board. Nothing in this document has been executed.**

This is the content intended for a `hermes kanban create --assignee symgov
--initial-status blocked` task, handing the actual deployment to the
running Hermes gateway agent (profile `symgov`, the same agent identity
that has been executing all prior stage work on this board). It is gated
per WP11.8/the day-one plan's own philosophy: **created blocked, and the
worker must re-block itself and post a comment after every numbered step
below, awaiting Chris's explicit unblock before continuing to the next
step.** This is not a single go-ahead for the whole rollout.

## Real infrastructure this task will operate against (confirmed 2026-09-07)

- Production is live on this same VPS, already serving (no real end users
  yet, confirmed by Chris).
- Current deployed release: `/data/symgov-releases/llm-consumption-bb40f4c/`
  (backend + built frontend `dist/`), **not** built from `main` — it was a
  divergent branch, now merged into `main` (see below).
- Containers (`docker ps`): `symgov-hermes-api` (FastAPI backend, port
  8010, built via `/docker/symgov-hermes/backend.Dockerfile` from the
  release dir), `applications-web` (nginx serving the release's `dist/`),
  `symgov-postgres` (Postgres 16, db `symgov`, user `symgov`),
  `symgov-minio` (object storage). Fronted by Traefik
  (`traefik-traefik-1`) and a Cloudflare tunnel
  (`cloudflared-symgov-apps.service`).
- Compose file: `/docker/symgov-hermes/docker-compose.yml` — the build
  context, image tag, and the `dist/` bind-mount all hardcode the release
  directory name/path directly (no symlink-swap indirection). Deploying a
  new release means editing this file's `llm-consumption-bb40f4c` strings
  to the new release name, in the same style as the existing
  `f0.2-63edef8`, `f0.3-7bdd9c0`, `f0.4-1824309` release directories
  already under `/data/symgov-releases/`.
- Organization feature flags (`SYMGOV_ORGANIZATIONS_ENABLED` and siblings)
  are **not** set in the current compose file's `environment:` block, so
  they're at the code default (off, `backend/symgov_backend/settings.py`)
  — confirmed by reading the live compose file, not assumed.
- The merge of `feature/llm-consumption-dashboard-20260801` (the
  currently-live release's own source) into `main` is done, on branch
  `merge/llm-consumption-into-main` (commit `eba10aa` at draft time, on
  top of `main`'s `3c790be`), with all conflicts resolved and full test
  suites green. **Chris still needs to review and land this on `main` and
  push before this task references a final commit SHA.**

## What this task must NOT do without a separate explicit unblock

- Must not run `npm run build:publish` / `publish:static` outside this
  task's own controlled release-directory copy.
- Must not enable `SYMGOV_ORGANIZATION_ICON_UPLOAD_ENABLED` (malware-scan
  gap, still deferred).
- Must not drop or downgrade past the visibility floor
  (`20260829_0033`) — additive migrations only.
- Must not touch `symgov-minio` bucket contents directly, only through the
  application.

## Task steps (worker: complete one step, then run `hermes kanban block`
## on this task with a comment describing what you did/found, and stop —
## do not proceed to the next numbered step until Chris unblocks it)

1. **Confirm the exact commit to deploy.** Confirm with Chris (or re-check
   `git log origin/main -1`) that the LLM-consumption merge has landed on
   `origin/main`, and record that commit SHA as `$DEPLOY_SHA` for every
   step below.

2. **Backup.** `docker exec symgov-postgres pg_dump -U symgov -d symgov`
   to a timestamped file outside any container's writable application
   path; confirm the dump is non-empty and restores cleanly into a
   disposable Postgres container (do not restore into `symgov-postgres`
   itself — spin up a throwaway container for the restore check, same
   pattern as `tests/test_stage11_wp11_7_migration_rehearsal_postgresql.py`'s
   `_database()`/`_alembic()` helpers). Also snapshot the `symgov-minio`
   data volume.

3. **Checkout the new release.** Create
   `/data/symgov-releases/stage11-<short $DEPLOY_SHA>/` as a fresh
   checkout of `origin/main` at `$DEPLOY_SHA` (mirroring the existing
   release-directory convention). Build the frontend inside it
   (`npm ci && npm run build`, not `build:publish`).

4. **Point compose at the new release, backend only, flags off.** Copy
   `/docker/symgov-hermes/docker-compose.yml` to a timestamped backup next
   to itself first (matching the existing `*.pre-*` backup convention seen
   under `langfuse-poc/`). Edit the copy in place: build context, image
   tag, and working_dir to the new release dir; leave every
   organization-flag environment variable unset (still off by default).
   Do not touch the `applications-web` service's `dist/` mount yet.

5. **Run the migration.** With `symgov-postgres` reachable, run
   `alembic upgrade 20260905_0044` from the new release's `backend/`
   against production, using the real `SYMGOV` DB env file — confirm
   result is exactly `20260905_0044` afterward (single head, matches this
   task's step 1 SHA).

6. **Deploy backend, verify health.** `docker compose up -d --build
   symgov-api` (new image), confirm
   `curl -fsS http://127.0.0.1:8010/api/v1/health` returns `ok: true`,
   confirm existing Workspace/agent-queue routes still respond, before
   touching the frontend.

7. **Deploy frontend.** Update the `applications-web` bind-mount to the
   new release's `dist/`, restart that container, confirm the site loads
   and shows no organization-aware navigation yet (flags still off).

8. **Bootstrap the `symgov` organization.** Verify (read-only, with Chris
   present) that the protected-owner account
   (`chris.brighouse@hotmail.co.uk`) exists and is active in the
   production DB. Dry-run
   `python3 -m symgov_backend.management bootstrap-symgov-organization`
   (no `--apply`), post the dry-run output as a comment, then — only after
   the next explicit unblock — run it with `--apply`.

9. **Enable flags for `symgov` only, plus platform admin.** Set
   `SYMGOV_ORGANIZATION_PILOT_CODES=symgov`,
   `SYMGOV_ORGANIZATIONS_ENABLED=1`, `SYMGOV_ORGANIZATION_ADMIN_ENABLED=1`,
   `SYMGOV_SYMBOL_SETS_ENABLED=1`, `SYMGOV_ORGANIZATION_SYMBOLS_ENABLED=1`,
   `SYMGOV_ORGANIZATION_AGENTS_ENABLED=1`, `SYMGOV_PLATFORM_ADMIN_ENABLED=1`
   in the compose file, restart `symgov-api`. (Per Chris's decision
   2026-09-07: `symgov` only for org-scoped flags at launch; icon upload
   stays off per the "must not" list above. Platform admin is turned on
   because Chris — as the protected owner, `chris.brighouse@hotmail.co.uk`
   — will operate `POST /platform/organizations` directly to create
   additional organizations; this supersedes the earlier "no live
   platform-admin agent binding yet" deferral, which was about an
   unstaffed automated Hermes governance agent, not a human operator using
   the route.)

   **Creating a new organization (once this step is live):** there is no
   self-registration or "first user to join becomes admin" flow. The
   sequence is: (a) a site admin creates the intended org-admin's user
   account via `POST /admin/users` (email + PIN) if it doesn't already
   exist; (b) the platform admin calls `POST /platform/organizations` with
   that user's ID as `initialAdminUserId`, which creates the org and
   assigns them as its first admin in one transaction; (c) that person can
   then add further members to their own org via
   `POST /organizations/{id}/members`.

10. **Smoke test.** Run the full WP11.8 step-6 smoke-test list
    (login/session, admin, project/set, private symbol, catalog,
    audit, queue, email, health) against the live, now-partially-enabled
    environment, as a real `symgov` pilot account. Post results as a
    comment.

11. **Stand down.** Confirm the old release directory
    (`llm-consumption-bb40f4c`) and its compose backup are left in place
    (not deleted) as the rollback target, per WP11.8 step 8. Mark the
    task complete only after Chris confirms the smoke test is accepted.

## Rollback (if any step 5 onward fails)

Follow WP11.8 step 8 exactly: point the compose file back at the old
release dir/image tag and restart — never a schema downgrade below the
visibility floor once any organization data exists from step 8 onward.

## Corrections from the 2026-09-07 execution attempt

Recorded 2026-09-07 after a gated run that completed steps 1-4 and was
stopped at step 5 by a release defect. Production was left untouched at
alembic `20260801_0026`. Each item is a correction to the step text
above, not a new requirement.

### Step 3 — build location and checkout mechanism

- `npm ci && npm run build` runs at the **release root**, not
  `frontend/`. `package.json` and `vite.config.js` live at the root, and
  vite is configured `root: 'frontend'`, `outDir: '../dist'`, so the
  build writes the root `dist/` that `applications-web` bind-mounts.
  There is no `package.json` under `frontend/`.
- New release directories are created with
  `git worktree add --detach <path> <sha>` from the primary repo, not
  `git clone`. Every existing directory under `/data/symgov-releases/`
  is a worktree (each `.git` is a `gitdir:` file). This avoids both the
  SSH-clone auth failure and the local-clone "dubious ownership" error
  earlier attempts hit, and never touches the primary repo's HEAD.
- Verified for `stage11-47f25bc`: build stamp `2026-09-07.01`, output
  layout (`index.html`, `assets/`, `submit/`) identical to the deployed
  release's, `dist/` and `node_modules/` both gitignored so the worktree
  stays clean at `$DEPLOY_SHA`.

### Step 4 — compose `${...}` variables had no on-disk source

`/docker/symgov-hermes/docker-compose.yml` interpolates seven variables
that were defined in no `.env` file and in no `env_file:`. They existed
only in the shell environment of the 2026-08-01 `docker compose up`:
`SYMGOV_LLM_TELEMETRY_ENABLED`, `_ENDPOINT`, `_PUBLIC_KEY`,
`_SECRET_KEY`, `_TIMEOUT_SECONDS`, `SYMGOV_LANGFUSE_QUERY_ENABLED`,
`SYMGOV_LANGFUSE_QUERY_BASE_URL`.

`docker compose config` in a clean shell resolved all seven to `""`, so
step 6's `up -d --build` would have silently disabled LLM telemetry and
Langfuse query and blanked the credentials — breaking the
LLM-consumption dashboard that this very release introduces. The fault
was latent rather than deploy-specific: any reboot-triggered
`docker compose up` would have done the same.

Fixed 2026-09-07 by persisting the live container's values to
`/docker/symgov-hermes/.env` (mode `0600`), recovered with
`docker inspect symgov-hermes-api`. `docker compose config` now reports
zero unresolved variables and all seven resolve non-empty. **Do not
delete that file** — it is the only on-disk record of those values.

### Step 5 — requires the migration DB role

Plain `alembic upgrade` fails immediately with
`psycopg.errors.InsufficientPrivilege: permission denied for schema
public`. `alembic/env.py:25` calls `get_database_url()` with no
arguments, resolving `SYMGOV_DATABASE_URL` — the app role, which has no
DDL rights. The upgrade must select the migration role:

    docker exec -e SYMGOV_ALEMBIC_USE_MIGRATION_DB=1 \
      -w /data/symgov-releases/<release>/backend symgov-hermes-api \
      python3 -m alembic upgrade <target>

`db.py:33-47` reads `SYMGOV_MIGRATION_DATABASE_URL` when that flag is
set. The two roles are indistinguishable in the test suite — every test
that touches them sets both URLs to the same value — which is why the
split is easy to miss.

### Step 5 — the whole upgrade is a single transaction

`env.py` does not pass `transaction_per_migration`, so one
`alembic upgrade` runs every revision in one transaction and a failure
at any revision rolls back all of them. Observed directly: six
revisions logged `Running upgrade`, the seventh raised, and
`alembic_version` stayed `20260801_0026` with none of the new tables or
columns present. Operationally this is reassuring — a failed upgrade
cannot leave a partial schema — but the `Running upgrade` log lines must
not be read as committed work.

### Step 5 — BLOCKER: no catalog identifier backfill exists

This is why the 2026-09-07 attempt stopped. It is a release defect, not
a data or environment problem.

- `20260802_0026` creates `catalog_symbol_identifiers` and adds a
  nullable `governed_symbols.catalog_symbol_id`, populating neither.
- `20260826_0031` then asserts every published symbol already has a
  matching canonical identifier, raising `catalog publication invariant
  preflight failed: published symbol lacks matching canonical catalog
  identifier`.
- Nothing bridges them. No migration inserts a registry row or assigns
  `catalog_symbol_id` anywhere in `backend/alembic/versions/`, and
  `management.py` exposes only `bootstrap-first-user` and
  `bootstrap-symgov-organization`.
- `catalog_symbol_ids.py:73` `ensure_catalog_symbol_id()` is the
  intended mechanism — it allocates `S-%06d` identifiers from
  `catalog_symbol_id_seq` and accepts
  `allocation_source="legacy_backfill"`, a value that exists solely for
  pre-existing symbols — but nothing runnable calls it.

Consequently **this release cannot migrate any database that already
contains published symbols.** Production holds 95 `governed_symbols`,
84 published revisions, 84 `published_pages`, 84 `pack_entries`.

The full backend suite passes (2356 tests), so no test seeds published
symbols *before* the registry is introduced — precisely production's
shape. Step 2's backup verification compared row counts only; it never
ran the migration against the restored copy, which would have caught
this.

Resolution is planned in
`docs/plans/2026-09-07-stage11-catalog-backfill-and-resume-plan.md`.

### Step 6 and step 10 — health check is unreachable from the host

The compose file publishes no host port for `symgov-api` (`8010/tcp`,
unmapped), so `curl -fsS http://127.0.0.1:8010/api/v1/health` from the
host fails with connection refused even while the service is healthy.
Use the container's own view, as its healthcheck does:

    docker exec symgov-hermes-api curl -fsS http://127.0.0.1:8010/api/v1/health

The same applies to every HTTP smoke test in step 10.
