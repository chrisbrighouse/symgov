# F0.4 production preflight report (secret-free)

Compiled at: 2026-07-28T19:24:00Z
Kanban task: `t_7486ccc6`
Overall result: **BLOCKED**

This report consolidates completed read-only evidence from:
- `t_8f1a1bf0` (runtime/public survey; accepted by admin verdict)
- `t_b942ec6e` (governance/drain audit)
- `t_72995efe` (deployment-integrity verification)

No new production mutation was performed for this compile step.

## Evidence map

- E1 = `t_8f1a1bf0` runtime/public survey outputs and operator addendum (observed 2026-07-28T19:08:46Z to 2026-07-28T19:11:59Z)
- E2 = `t_b942ec6e` two-snapshot drain/governance audit (observed 2026-07-28T18:12:02.723668Z and 2026-07-28T18:12:33.099813Z; delta 30.376145s)
- E3 = `t_72995efe` deployment-integrity verification (inspection window 2026-07-28T18:15:04Z to 2026-07-28T18:17:04Z)

## Consolidated observations

### 1) Runtime containers, images, lifecycle, workdirs, mounts (E1)

API container (`symgov-hermes-api`):
- Image ref: `symgov-hermes-api:latest`
- Image ID: `sha256:3b5accc5af3f3bec9efa7d52356df6b8664156da459b334f1aeee3d05dbd6a50`
- State/health: `running` / `healthy`
- Created: `2026-07-28T01:52:05.902654833Z`
- Started: `2026-07-28T01:52:06.745691151Z`
- Restart count: `0`
- Configured working directory: `/data/symgov-releases/f0.3-45fc6e0/backend`
- Mounts:
  - `/docker/openclaw-hz0t/data -> /data` (bind, rw)
  - `/root/.hermes -> /root/.hermes` (bind, ro)

Web container (`applications-web`):
- Image ref: `nginx:1.27-alpine`
- Image ID: `sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10`
- State/health: `running` / `healthy`
- Created: `2026-07-28T01:52:06.521022776Z`
- Started: `2026-07-28T01:52:06.985754522Z`
- Restart count: `0`
- Configured working directory: `/`
- Mounts:
  - `/docker/openclaw-hz0t/data/.openclaw/workspace -> /srv/apps/workspace` (bind, ro)
  - `/docker/symgov-hermes/nginx.conf -> /etc/nginx/conf.d/default.conf` (bind, ro)
  - `/docker/openclaw-hz0t/data/symgov-releases/f0.3-45fc6e0/dist -> /usr/share/nginx/html` (bind, ro)
  - `/docker/symgov-hermes/status -> /usr/share/nginx/status` (bind, ro)
  - `/docker/openclaw-hz0t/data/.openclaw/workspaces -> /srv/apps/workspaces` (bind, ro)

Restart-history signal:
- Reported restart counts are zero for both containers.
- No create/start/restart/die events were returned in the 48h event query window included in E1 output.

### 2) Public boundary and served frontend artifacts (E1)

Observed at 2026-07-28T19:11:58Z to 2026-07-28T19:11:59Z:
- `GET /` (public root): HTTP 200
  - Root body SHA-256: `fb6017e1e3f5be6cd2ca218a709d5f7cec808bbfe9e44be642d05408a392081d`
- `GET /api/health`: HTTP 200
  - Secret-safe facts: `ok=true`, `service=symgov-api`

Served frontend entry + bundle hashes:
- Entry JS: `https://apps.chrisbrighouse.com/assets/index-DUcizVCF.js`
  - HTTP 200, SHA-256 `f32bb1263f7e2499e9873954d839e9117c3df0798f6837d759fa4ba8e0487847`
- CSS bundle: `https://apps.chrisbrighouse.com/assets/index-DwfsD6QJ.css`
  - HTTP 200, SHA-256 `594886e833e0231dc1ee99fd142ffc37b7ab4a0b5fbb1b867d981171d7cb6133`

### 3) API cwd and import provenance (E1 + E3)

Observed at 2026-07-28T19:09:49Z:
- PID1 cwd: `/data/symgov-releases/f0.3-45fc6e0/backend`
- PID1 cmdline: `/sbin/docker-init -- python manage_symgov.py serve-api --host 0.0.0.0 --port 8010`
- Python probe cwd: `/data/symgov-releases/f0.3-45fc6e0/backend`
- Imported package file: `/data/symgov-releases/f0.3-45fc6e0/backend/symgov_backend/__init__.py`
- Imported runtime module file: `/data/symgov-releases/f0.3-45fc6e0/backend/symgov_backend/runtime.py`

Integrity cross-check (E3): imported module hash matched between container and host for the deployed worktree.

### 4) Compose/nginx identities, rollback cleanliness, Alembic (E3)

Artifacts/hashes:
- `/docker/symgov-hermes/docker-compose.yml`
  - SHA-256 `751547dd5dab826f3f6303c747d55526dfad41c1040c5a42e21180a03ff1f041`
- `/docker/symgov-hermes/nginx.conf`
  - SHA-256 `c1ef89c199a38e5949a848d59bb0cc9718e879c3832f2ddf21746026028e8f1a`

Rollback release worktree:
- Worktree: `/docker/openclaw-hz0t/data/symgov-releases/f0.3-45fc6e0`
- Expected SHA: `45fc6e00b1372fce1e092ebe282f264ccd401cb3`
- Actual HEAD SHA: `45fc6e00b1372fce1e092ebe282f264ccd401cb3`
- Detached HEAD: yes
- Clean status: yes (no dirty/untracked paths reported in that worktree)

Alembic read-only verification:
- `alembic current`: `20260721_0024`
- `alembic heads`: `20260721_0024`
- Current equals all heads: true

### 5) Governance/drain snapshots, interval, and counts (E2)

Snapshot interval:
- Snapshot 1: `2026-07-28T18:12:02.723668Z`
- Snapshot 2: `2026-07-28T18:12:33.099813Z`
- Intended interval: 30s
- Observed delta: 30.376145s
- Count stability across snapshots: true

Published-symbol-review queue counts:
- Snapshot 1: active 0, queued 0, claimed 0, running 0, completed 8
- Snapshot 2: active 0, queued 0, claimed 0, running 0, completed 8
- Older-than-30s active: 0 (both snapshots)
- Older-than-30s claimed/running: 0 (both snapshots)

Publication job counts:
- Snapshot 1: active 0, completed 81, older-than-30s active 0
- Snapshot 2: active 0, completed 81, older-than-30s active 0

Runtime mirror/reconciliation counts:
- Orphans: 494
- Active orphans: 196
- By status: completed 296, escalated 2, running 196
- Missing runtime mirrors: 0
- Potential reconciliation changes: 1
- Skipped mismatches: 0

Published-page integrity count:
- Published pages referencing non-published revisions: 3

Duplicate active pair count:
- Duplicate active published-symbol-review action/queue pair groups: 0

Age evidence for old in-flight work (anomaly IDs included per requirement):
- Oldest active runtime orphan: `ac7a3fce-aa31-5926-b503-165c834ee02d` at `2026-06-17T09:01:29Z`
- Newest active runtime orphan: `5e069ef7-8e1b-5fc7-a54e-fc4d3a4b45b4` at `2026-06-17T16:53:28Z`
- Potential reconciliation-change queue item: `68a9798a-83b1-5844-b38f-10d6beeaf32f`

### 6) External Ed process/runner counts (E1)

Observed at `2026-07-28T19:11:59Z`:
- `api_container_ed_children`: 0
- `external_ed_runners`: 0

No anomaly process identifiers were required because counts were zero.

## Anomalies and blockers

1) Drain/runtime blocker: 494 runtime orphan mirrors, including 196 still marked running and older than the 30s bounded interval evidence (E2).
2) Data-integrity blocker: 3 published pages reference non-published revisions (E2).
3) Reconciliation blocker: 1 potential runtime reconciliation change pending explicit handling (E2).
4) Global blocker rule: runbook requires BLOCKED overall result when old work is in flight, unmet drain requirements, or similar blockers remain (E2).

Non-blocking observation:
- Docker Compose warned that top-level `version` is obsolete/ignored during integrity inspection (E3).

## Checklist (requested checks -> status)

- Runtime API image reference + immutable image ID: PASS (E1)
- Runtime web image reference + immutable image ID: PASS (E1)
- Container health and lifecycle timestamps: PASS (E1)
- Restart counts and relevant restart-history signal: PASS (E1)
- Working directories and mounts: PASS (E1)
- Public root result with timestamp: PASS (E1)
- Public `/api/health` result with timestamp and non-secret facts: PASS (E1)
- Served frontend entry and hashed bundles + SHA-256: PASS (E1)
- API cwd and import provenance: PASS (E1, E3)
- Compose and nginx hashes: PASS (E3)
- Rollback SHA and rollback-worktree cleanliness: PASS (E3)
- Alembic current versus all heads: PASS (E3)
- Queue/publication/runtime/integrity requested counts: PASS (E2)
- Two drain snapshots + bounded interval + stability: PASS (E2)
- External Ed process/runner counts: PASS (E1)
- Drain requirement satisfied (no old in-flight work): BLOCKED (E2)
- No migration-head mismatch: PASS (E3)
- Runtime health gate: PASS (E1, E3)
- Rollback-worktree contamination absent: PASS (E3)
- Another runbook blocker present: BLOCKED (E2 anomalies above)

## Final preflight decision

**BLOCKED** — do not proceed with activation/remediation steps from this report.

Rationale: even with healthy runtime and verified deployment provenance, the governance/drain audit shows old work still in flight (runtime orphans), unresolved published-page integrity anomalies, and an outstanding potential reconciliation change. That meets explicit runbook block conditions.

## Artifact location

Secret-free report saved at:
`/data/symgov/docs/plans/2026-07-28-f0-4-production-preflight-report-secret-free.md`

## Secret-safety check

A post-write scan was performed for credentials/tokens/environment values/connection strings/sensitive payload patterns. None are included in this report.