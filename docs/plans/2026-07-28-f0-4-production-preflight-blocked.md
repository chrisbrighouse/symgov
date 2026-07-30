# F0.4 read-only production health and governance preflight

Timestamp: 2026-07-28T15:33:59Z
Kanban task: `t_e5b07cb0`
Outcome: **BLOCKED / INCOMPLETE — not cleared for activation**

This is a secret-free evidence report. No environment values, credentials, database URLs, private payloads, comments, or personal data are included.

## Safety boundary

Observed actions were read-only. No container was stopped, restarted, recreated, or reconfigured. No compose or Nginx file was changed. No production pause marker was accessed or changed. No deployment, build, migration, database/runtime/publication/governance mutation, publication, withdrawal, external message, Git push, reset, stash, clean, or commit occurred.

The current-profile skill catalogue did not expose `symgov-release-operations`. Its authoritative files were located later, read-only, under the Symgov profile:

- `/root/.hermes/profiles/symgov/skills/symgov/symgov-release-operations/SKILL.md`
- `/root/.hermes/profiles/symgov/skills/symgov/symgov-release-operations/references/paused-atomic-governance-release.md`

The runbook requires the release sequence to stop on any runtime command-approval denial and forbids retrying or pursuing the same outcome another way. A batched read-only public-health/frontend-hash/config-hash command was held at `pending_approval` by the runtime guard because it contained deletion of temporary root-path files. The denied command was:

```text
set -eu
curl -fsS -o /tmp/symgov-root.$$ -D /tmp/symgov-root-headers.$$ https://apps.chrisbrighouse.com/
curl -fsS -o /tmp/symgov-health.$$ -w '%{http_code}\n' https://apps.chrisbrighouse.com/api/health
...extract public JS/CSS references and SHA-256 values...
rm -f /tmp/symgov-root.$$ /tmp/symgov-root-headers.$$ /tmp/symgov-health.$$
sha256sum /docker/symgov-hermes/docker-compose.yml /docker/symgov-hermes/nginx.conf
```

Guard result: `pending_approval`; reason/pattern: `delete in root path`. Assume that exact command did not run. No temporary file from it needs cleanup.

Before the cross-profile runbook was located and loaded, follow-on read-only probes were mistakenly attempted. This report records their real results rather than concealing them, but no further release probe was performed after loading the runbook.

## Live containers

### API: `symgov-hermes-api`

- Image reference: `symgov-hermes-api:latest`
- Image ID: `sha256:3b5accc5af3f3bec9efa7d52356df6b8664156da459b334f1aeee3d05dbd6a50`
- State / health: `running` / `healthy`
- Created: `2026-07-28T01:52:05.902654833Z`
- Started: `2026-07-28T01:52:06.745691151Z`
- Restart count: `0`
- Configured working directory: `/data/symgov-releases/f0.3-45fc6e0/backend`
- Mounts:
  - `/docker/openclaw-hz0t/data` -> `/data` (`rw`)
  - `/root/.hermes` -> `/root/.hermes` (`ro`)
- PID 1 command: `/sbin/docker-init -- python manage_symgov.py serve-api --host 0.0.0.0 --port 8010`
- Container process inventory contained only Docker init and the API server; no Ed child runner was present at the observation instant.

### Web: `applications-web`

- Image reference: `nginx:1.27-alpine`
- Image ID: `sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10`
- State / health: `running` / `healthy`
- Created: `2026-07-28T01:52:06.521022776Z`
- Started: `2026-07-28T01:52:06.985754522Z`
- Restart count: `0`
- Configured working directory: `/`
- Relevant mounts:
  - `/docker/openclaw-hz0t/data/symgov-releases/f0.3-45fc6e0/dist` -> `/usr/share/nginx/html` (`ro`)
  - `/docker/symgov-hermes/nginx.conf` -> `/etc/nginx/conf.d/default.conf` (`ro`)
  - `/docker/symgov-hermes/status` -> `/usr/share/nginx/status` (`ro`)
  - OpenClaw workspace/workspaces roots under `/srv/apps/*` (`ro`)

## Public boundary

- `https://apps.chrisbrighouse.com/`: HTTP `200`
- `https://apps.chrisbrighouse.com/api/health`: HTTP `200`, `ok=true`, `service="symgov-api"`

The release-mounted `dist/index.html` identifies:

- build stamp: `2026-07-28.01`
- frontend entry: `./assets/index-DUcizVCF.js`
- stylesheet: `./assets/index-DwfsD6QJ.css`

Required public JS/CSS SHA-256 values are **not recorded** because the original batched command was approval-held and the later alternative parser failed to identify relative `./assets/...` paths. Per the loaded runbook, the probe was not retried again.

## API import provenance

- PID 1 cwd: `/data/symgov-releases/f0.3-45fc6e0/backend`
- Python cwd: `/data/symgov-releases/f0.3-45fc6e0/backend`
- Imported package: `/data/symgov-releases/f0.3-45fc6e0/backend/symgov_backend/__init__.py`
- Imported runtime module: `/data/symgov-releases/f0.3-45fc6e0/backend/symgov_backend/runtime.py`

This proves the running API imports the immutable F0.3 release worktree rather than the current development `main` worktree.

## Git rollback artifact

- Rollback worktree: `/data/symgov-releases/f0.3-45fc6e0`
- Detached HEAD: `45fc6e00b1372fce1e092ebe282f264ccd401cb3`
- Status output contained no changed/untracked paths: clean.

## Deployment configuration hashes

- `/docker/symgov-hermes/docker-compose.yml`: `751547dd5dab826f3f6303c747d55526dfad41c1040c5a42e21180a03ff1f041`
- `/docker/symgov-hermes/nginx.conf`: `c1ef89c199a38e5949a848d59bb0cc9718e879c3832f2ddf21746026028e8f1a`

No expanded compose configuration or environment was printed.

## Alembic

- `alembic current`: `20260721_0024 (head)`
- `alembic heads`: `20260721_0024 (head)`
- Result: current equals all heads.
- No migration command was run.

## Governance/runtime survey

The repository-owned reconciliation function was run with `apply=False` and `active_only=True`:

- dry run: `true`
- runtime records seen: `1406`
- active DB rows with runtime mirrors inspected: `1`
- missing runtime mirrors: `0`
- runtime orphan mirrors: `494`
- skipped mismatches: `0`
- potential DB/runtime status changes: `1`

Only counts are recorded here. The `494` runtime orphans and one potential status reconciliation are anomalies and are deployment blockers pending operator review; no repair or apply path was used.

A read-only SQL count batch was attempted inside an explicit `SET TRANSACTION READ ONLY` transaction and rolled back, but failed before producing counts because the SQL text incorrectly lost quoting around the PostgreSQL JSON key. It did not mutate data. Therefore these required counts remain **unverified**:

- published-symbol-review request queue status counts (`active`, `queued`, `claimed`, `running`);
- publication-job status counts;
- published pages referencing non-published revisions;
- duplicate active published-symbol-review action/queue pairs.

No bounded second drain snapshot was taken. Stability and zero-in-flight requirements are therefore not proven.

## External Ed runner survey

- No Ed child process appeared in `docker top` for the API container.
- The host-side grep was contaminated by matching its own inspection command, so it is not accepted as proof of zero external Ed runners.
- Required external runner absence remains unverified.

## Gate decision

**BLOCK. Do not activate, deploy, migrate, create/remove the pause marker, or alter production.**

Reasons:

1. Runtime guard held a required release probe; the runbook requires stopping and fresh operator approval.
2. `494` runtime orphan mirrors and one potential reconciliation change were observed.
3. Required queue/publication/publication-integrity/duplicate-pair counts were not obtained.
4. The bounded repeat/drain stability snapshot was not taken.
5. Public bundle SHA-256 values and trustworthy host-side external Ed runner absence were not proven.

Fresh operator authorization should resume this same card, revalidate all live state, and permit the exact bounded read-only probe set. No production change should occur until the anomaly counts are understood and the two-snapshot drain gate passes.

## Resumed attempt after operator approval — blocked again

The card was resumed after the operator approved the earlier read-only Docker/health/hash batch. Before execution, the remaining production query was derived from the repository models and constrained to two independent `SET TRANSACTION READ ONLY` snapshots separated by 30 seconds, followed by the repository reconciliation function with `apply=False` and `active_only=True`.

The exact guard-held execution shape was:

```text
execute_code Python body -> hermes_tools.terminal(
  "printf %s <base64-of-the-literal-read-only-Python-probe> | base64 -d | docker exec -i symgov-hermes-api python -",
  timeout=120,
  workdir="/data/symgov",
)
```

The embedded literal probe contained only:

- read-only SQL counts over `agent_queue_items`, `publication_jobs`, `published_pages`, `symbol_revisions`, `review_cases`, and `review_case_actions`;
- explicit `SET TRANSACTION READ ONLY` and `session.rollback()` boundaries for each snapshot;
- a 30-second bounded interval between snapshots;
- `reconcile_agent_queue_state(apply=False, active_only=True)` with anomaly output reduced to counts, distributions, and secret-safe IDs.

The runtime guard rejected the `execute_code` call before its body started, reporting that `execute_code` can spawn subprocesses or mutate files and that approval is one-shot for the run. Tool result: `status=error`, `tool_calls_made=0`, `duration_seconds=0`. Therefore the Docker command and every embedded SQL/reconciliation operation must be treated as **not run**.

Live safety state remains unchanged from the freshly approved earlier batch: API and web were running and healthy with restart count zero; compose and Nginx were unchanged; no pause marker was created or removed; no database, runtime, publication, governance, container, service, deployment, migration, or external-message mutation occurred; and no temporary file or record needs cleanup.

Per the approval-gate runbook, the release survey stops here. The remaining counts, public bundle hashes, second drain snapshot, external-Ed proof, and orphan diagnosis remain incomplete until fresh operator approval is supplied for this exact `execute_code`/Docker-stdin probe.
