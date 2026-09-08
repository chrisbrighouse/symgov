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

### THE ROOT CAUSE BEHIND MOST OF THESE: `_csv_setting` is empty in production

`settings.py:20-24`:

    def _csv_setting(name: str, local_default: str = "") -> tuple[str, ...]:
        raw = os.environ.get(name)
        if raw is None and _environment() in LOCAL_SECURITY_ENVIRONMENTS:
            raw = local_default
        return tuple(...)

**The declared default applies only in `local`/`test`.** In production an
unset variable yields an *empty tuple*, no matter how sensible the
default looks in the source. Three separate production failures on
2026-09-07 came from this one rule, and reading the declaration line
alone will mislead you every time. When auditing configuration, treat
every `_csv_setting` as "required in production" regardless of its
apparent default.

Affected: `SYMGOV_CSRF_TRUSTED_ORIGINS`, `SYMGOV_CSRF_TRUSTED_HOSTS`,
`SYMGOV_TRUSTED_PROXY_CIDRS`.

### Step 6 — two settings are mandatory in production, and both crash-loop the API

Neither exists anywhere on disk in the pre-Stage-11 deployment, neither
is mentioned in any plan doc, and neither is covered by a test. Both
surface only as a crash loop after `docker compose up -d --build`:

1. `SYMGOV_AUTH_LOGIN_HASH_SECRET` — minimum 16 characters, and the
   local/test placeholder values are explicitly rejected when
   `environment` is not `local`/`test` (`auth_security.py:39,56`).
   Failure: `ValueError: A deployment-provided login throttle hash
   secret is required.` Stored in `/docker/symgov-hermes/.env`
   (mode 0600). A regenerated value only resets transient login-throttle
   counters; nothing persistent depends on it.
2. `SYMGOV_CSRF_TRUSTED_ORIGINS` and `SYMGOV_CSRF_TRUSTED_HOSTS` — see
   the `_csv_setting` note above. Failure: `ValueError: At least one
   trusted CSRF origin is required.` Set literally in the compose file
   (they are not secrets and document the deployment):
   `https://apps.chrisbrighouse.com` and `apps.chrisbrighouse.com`.

`create_app()` runs exactly two validators — `login_throttle_policy` and
`validate_request_security_settings` — so these two are the complete set
of startup-fatal settings. A config audit on 2026-09-07 confirmed the
other 65 `SYMGOV_*` variables the code reads are safe unset.

### Step 8 — the bootstrap command needs the migration role too

`bootstrap-symgov-organization --apply` fails as the app role with
`psycopg.errors.InsufficientPrivilege: permission denied for table
organization_memberships`. `reconcile_symgov_organization_bootstrap`
uses `with_for_update()`, and PostgreSQL requires `UPDATE` privilege for
`SELECT ... FOR UPDATE`, but `20260810_0028` deliberately grants
`symgov_app` only `INSERT, SELECT` on the membership and role tables and
`REVOKE`s `UPDATE, DELETE, TRUNCATE` — an append-only design protecting
membership history. The command therefore **can never work as the app
role**. Run it with the same flag the migration uses:

    docker exec -e SYMGOV_ALEMBIC_USE_MIGRATION_DB=1 \
      -w /data/symgov-releases/<release>/backend symgov-hermes-api \
      python3 -m symgov_backend.management bootstrap-symgov-organization --apply

Despite its name, `db.py:35-38` applies that flag to *every*
`get_database_url()` call, not just Alembic's.

Note also that the **dry run under-reports**. With no organization
present, the defensive early return at `organization_service.py:403`
short-circuits before the membership and platform-role checks, so the
audit lists only `create organization symgov` while `--apply` performs
four actions (organization, membership, org admin role, platform admin
role). The sparse dry-run output is expected, not a discrepancy.

### Step 10 — authenticated mutations need the proxy to be trusted

Symptom: login succeeds but **every authenticated mutation** fails with
`403 Cross-origin request is not permitted.` — sign-out, project
creation, symbol sets, organization creation. Login is exempt because it
is in `UNAUTHENTICATED_LOGIN_OPERATIONS` and returns at
`dependencies.py:361` before the origin check, which makes the fault
look far narrower than it is.

Cause: `SYMGOV_TRUSTED_PROXY_CIDRS` was empty (the `_csv_setting` rule),
so `_peer_is_trusted()` rejected nginx and `X-Forwarded-Proto: https`
was ignored. The API then computed `effective_origin` as
`('http', host, 80)` against a browser `Origin` of
`('https', host, 443)`, and `dependencies.py:399` rejected the mismatch.

Fix: trust the nginx container explicitly —
`SYMGOV_TRUSTED_PROXY_CIDRS: "127.0.0.0/8,::1/128,172.31.30.10/32"`,
scoped to its static compose-assigned address rather than the whole
`/24`.

The three 403s carry distinct messages, which makes diagnosis fast:
`"Cross-origin request is not permitted."` (origin mismatch or untrusted
origin), `"Request host is not trusted."` (host not in
`csrf_trusted_hosts`), `"Forwarded request scheme is not trusted."`
(malformed `X-Forwarded-Proto`).

Still open: `trusted_proxy_hops` is `1` against a
Cloudflare → Traefik → nginx chain, so the client IP recorded for
per-IP login throttling may be a proxy rather than the real client.

### Production agent workers execute from the primary working tree

`agent_queue_worker.py` hardcodes five of six runner paths to
`/data/symgov/scripts/run_*.py` — the **primary repo working tree**, not
the release directory (only `run_rupert_publication.py` uses the
release-relative `REPOSITORY_ROOT / "scripts"`). This is identical in
the old and new releases.

Consequences: any edit under `/docker/openclaw-hz0t/data/symgov/scripts/`
takes effect in production immediately with no release step, and those
scripts can desync from whichever backend package is deployed. That is
exactly what happened before this deployment — the working tree's
scripts imported `symgov_backend.services.llm_router`, absent from the
August release, so 5 of 6 agent runners raised `ModuleNotFoundError` on
every attempt (~120 tracebacks per 10 minutes). Deploying this release
fixed it incidentally, but the coupling remains and deserves its own
work item.

### nginx masks missing assets as HTML

`nginx.conf:99-101` is `try_files $uri $uri/ /index.html`, so a missing
file under `/assets/` returns `index.html` with `200 text/html` instead
of `404`. In a browser this surfaces as `Failed to load module script:
Expected a JavaScript-or-Wasm module script but the server responded
with a MIME type of "text/html"` and a blank page — which reads like a
deployment failure even when the deployment is correct (on 2026-09-07 it
was a stale browser cache). The app uses `HashRouter`, so real routes
never need that fallback for asset paths. Suggested hardening, ahead of
`location /`:

    location /assets/ {
      try_files $uri =404;
    }

### Step 6 and step 10 — health check is unreachable from the host

The compose file publishes no host port for `symgov-api` (`8010/tcp`,
unmapped), so `curl -fsS http://127.0.0.1:8010/api/v1/health` from the
host fails with connection refused even while the service is healthy.
Use the container's own view, as its healthcheck does:

    docker exec symgov-hermes-api curl -fsS http://127.0.0.1:8010/api/v1/health

The same applies to every HTTP smoke test in step 10.

## Execution record — 2026-09-08: the deployment completed

Steps 1-11 ran to completion against production, gated one command at a
time. Unlike the 2026-09-07 attempt, nothing here was blocked by a
release defect. What was deployed:

- `$DEPLOY_SHA` = `056c13c`, release directory
  `/data/symgov-releases/stage11-056c13c`.
- Alembic `20260905_0044` -> `20260907_0045` (`GRANT UPDATE` on the four
  membership/role history tables, so `require_stage4_principal`'s
  `SELECT ... FOR SHARE` is permitted; `DELETE`/`TRUNCATE` stay revoked).
- Frontend: uppercase coercion on organization, project and symbol set
  code inputs.

Backend application code was otherwise unchanged from `stage11-87abf09`,
so this was a migration plus a frontend rebuild rather than a code
rollout. Verified after deploy: schema at a single head `20260907_0045`;
grants exactly `INSERT, SELECT, UPDATE` on all four tables; API healthy
on `symgov-hermes-api:stage11-056c13c` with a clean startup log; nginx
serving `stage11-056c13c/dist` with `index.html` referencing the new
`index-BhZ9JF6W.js` and that asset returning `application/javascript`.
`stage11-87abf09` and `docker-compose.yml.pre-0045-20260908T085248Z` are
both retained as the rollback target.

### The pre-migration backup does not survive a naive restore

Step 2's restore verification is not a formality — it failed. A plain
`pg_restore` of `symgov-pre-0045-migration-20260908T083951Z.dump`
reports three errors, exits, and leaves `projects` with **0 of 1 rows**,
while `users` (11) and `governed_symbols` (95) restore correctly. The
first error is the real one; the two FK failures that follow are
consequences:

    COPY failed for table "projects": ERROR:  function
    stage4_jsonb_max_depth(jsonb) does not exist

`stage4_jsonb_max_depth` (`20260822_0030_project_symbol_sets.py:20`) is
a recursive SQL function that calls itself *unqualified* and declares no
`SET search_path`. `pg_dump` emits
`set_config('search_path', '', false)`, so when the
`ck_projects_metadata_bounds` CHECK constraint evaluates during `COPY`,
the inlined self-call cannot resolve. The same constraint guards
`symbol_set_items` (empty in production today, which is the only reason
it produced a single error rather than two).

The data is present in the dump — this is a restore-path failure, not a
bad backup. Verified working recovery procedure:

    pg_restore ... --section=pre-data
    psql -c "ALTER FUNCTION stage4_jsonb_max_depth(jsonb)
             SET search_path = public, pg_catalog;"
    pg_restore ... --section=data --section=post-data

That restores exit 0 with `projects` 1/1 and `alembic_version` matching
production. Of the 30 functions the migrations define, this is the only
self-recursive one reached from a CHECK constraint, so the blast radius
is this single function. **Until an in-schema fix lands, no operator
should treat a plain `pg_restore` of a production dump as successful
without checking row counts** — it reports only a warning line and exits
non-zero, which is easy to miss under an emergency.

### "The admin options disappeared" is a session-mode symptom

Reported mid-deployment and initially read as a deployment regression.
It was neither caused by nor related to the deploy: the Projects, Symbol
Sets and Builder panels are gated client-side on
`capabilities.symbolSetsEnabled` (`OrganizationAdminPage.js:665`), which
`routes/auth.py:72-80` returns as true only for a session that is
`purpose=application` **and** `session_mode=organization` with an
`active_organization_id` whose code is in
`SYMGOV_ORGANIZATION_PILOT_CODES`. Browsing with a personal-mode session
renders all three as `null`, with no API call made at all — so the
server logs show nothing wrong, because nothing is.

`trg_user_sessions_immutable_org_context` prevents converting a live
session's organization context, so the fix is to sign out and back in
through the organization.

Two consequences for step 10. A smoke test run from a personal-mode
session proves nothing about organization-scoped behaviour, because the
controls are not rendered to fail. And "the panel is missing" and "the
panel errors" are entirely different diagnoses — only the second one
exercises the authorization path that `20260907_0045` fixed.
