# F0.4 persisted deployment evidence audit (secret-safe)

Audit task: `t_8b7dfb2a`<br>
Audit scope: persisted preflight, paused API-stage, frontend-stage, post-deployment, rollback, runbook, and migration evidence<br>
Audit method: read-only review only; no deployment change, service recreation, migration, credential creation, pause-marker change, or rollback

## 1. Audit verdict

**PASS for the authorized paused-deployment scope, with evidence-provenance caveats and final activation/resumption still pending.**

The latest authoritative evidence chain records every gate in the authorized paused-deployment scope as passed for immutable release `f0.4-1824309`. The API and frontend resolve to one release and package version; all public assets are byte-equal to the prepared release; the pause contract, zero-delta, published-read, worker-health, Ed-claim, OpenAPI, cleanup, and Alembic gates pass; and `published-feedback.pause` remains installed. Marker removal and the subsequent unpaused endpoint/worker-health proof were not authorized or executed by this evidence chain and are therefore pending rather than PASS.

Two earlier records are intentionally non-green but are not current release failures:

1. the sealed deployment manifest and 2026-07-28 preflight preserve their historical `BLOCKED` verdicts;
2. the later post-remediation addendum cleared the blockers and recorded `PREPARED`, after which the persisted deployment-stage evidence recorded `PASS`.

The sealed history was correctly retained rather than rewritten. No evidence reviewed here requires rollback.

## 2. Authority and evidence chain

| Evidence | Persisted reference | Observed verdict | Audit use |
|---|---|---:|---|
| Applicable runbook | `/root/.hermes/profiles/symgov/skills/symgov/symgov-release-operations/references/paused-atomic-governance-release.md` | applicable | Defines mixed-version, pause, immutable-artifact, live-gate, rollback, and secret-safety requirements; see lines 5-76 and 88-101. |
| F0.4 deployment/rollback specification | `docs/plans/2026-07-28-f0-4-review-without-unpublication-spec.md` | applicable | Section 10 requires backend-first paused activation, exact 503 behavior, zero DB/runtime delta, worker-claim pause, one-version frontend activation, `alembic current == heads`, and migration-free handling; see lines 468-497. |
| Historical preflight | `docs/plans/2026-07-28-f0-4-production-preflight-report-secret-free.md` | BLOCKED (historical) | Preserves the original orphan/runtime/publication blockers; see lines 146-183. |
| Post-remediation final read-only gate | `/data/symgov-release-backups/f0.4-daisy-row-deletion-20260729T112002Z/final-read-only-release-gate.md` | PREPARED | Clears the historical blockers with zero runtime orphans, active review queues, active publication jobs, publication-integrity defects, and reconciliation changes; see lines 9-23. |
| Stale generated preflight lane | `/data/symgov-release-state/f0.4-20260728T221111.687815566Z/t_db81615d-preflight-evidence.json` | BLOCKED_STAGE_OVERTAKEN | Correctly records that a duplicate preflight started after the original activation lane had advanced; it is a stage-order warning, not a candidate defect; see lines 5-18 and 131-165. |
| Consolidated prerequisites/API-stage evidence | `docs/plans/2026-07-29-f0-4-deployment-prerequisites-evidence-redacted.json` | PASS | Candidate, rollback, pause, API provenance, 503, zero-delta, reads, worker health, cleanup, and current live identity; see lines 16-145. SHA-256: `768e66123bfbe9f9aec02d534cebbbe5a019b26f5515b3d0e0770e60547f03b2`. |
| Original API-stage board evidence | Kanban `t_3b9962b8` comments and accepted child `t_a14357dc` | PASS | Persists the exact operator-approved API activation outcome and the finally-cleaned authenticated verifier summary. |
| Frontend activation evidence | Kanban `t_3b9962b8` operator evidence and accepted child `t_0a129aca` | PASS | Records one approved recreation, running/healthy state, F0.4 read-only dist mount, public health 200, retained pause, and no rollback. |
| Post-deployment validation | `docs/plans/2026-07-29-f0-4-post-deployment-validation-evidence-redacted.json` | PASS | Public assets, feature markers, OpenAPI, API/frontend identity, Alembic, pause, cleanup; see lines 22-119. SHA-256: `8e337eed85a2cd7e6a58ad552884e43a92185c6a0d04ae81f67dedce666534af`. |
| Final consistency evidence | `docs/plans/2026-07-29-f0-4-release-consistency-evidence-redacted.json` | PASS (paused-deployment scope) | Chains prerequisite/API-stage and post-deployment hashes and attests one immutable candidate while explicitly leaving final resumption pending; see lines 6-156. SHA-256: `0c10fe328e28288c4af72d9a2ae245fc0ebd467b4ce6c7e16c8cede6279dd7a5`. |
| Final human-readable report | `docs/plans/2026-07-29-f0-4-final-deployment-report-secret-safe.md` | SUCCESS (paused deployment) | Consolidated check categories and final paused-deployment outcome; see lines 21-187. SHA-256: `f21242ffe182499597059ba97b22463562c59588b9d76be80de5e1782c4ccbe1`. |

## 3. Command/check categories

Status: **PASS**. Persisted evidence records these bounded categories without expanded Compose or secrets:

1. immutable release, detached worktree, candidate Compose, rollback artifact, image, prepared-asset, and checksum identity;
2. `docker compose config --quiet` only, with the known obsolete top-level `version` warning and no expanded configuration;
3. bounded API/frontend state, health, restart count, image ID, cwd, import path, and selected mount inspection;
4. host/container `published-feedback.pause` `stat` checks;
5. authenticated pause-contract probes for both browser aliases and Catalog feedback;
6. pre/post database, governance audit, queue, workflow, publication, API-key, Ed-claim, and runtime-file comparison across a 15-second observation interval;
7. published-read and authenticated worker-health probes;
8. public root, API health, index, JavaScript, and CSS status/hash checks;
9. generated OpenAPI assertions;
10. `alembic current` versus `alembic heads`;
11. temporary credential and verifier cleanup/residue checks.

Primary citation: final report lines 21-37.

## 4. Gate-by-gate findings

### 4.1 HTTP status codes and required headers

| Surface | Required | Observed | Result | Citation |
|---|---|---|---:|---|
| Browser v1 alias | HTTP 503; `Retry-After: 60`; bounded `published_feedback_paused` response; retryable | exact match | PASS | prerequisites lines 78-103; final report lines 82-90 |
| Browser legacy alias | HTTP 503; `Retry-After: 60`; same bounded response | exact match | PASS | prerequisites lines 78-103; final report lines 82-90 |
| Catalog feedback | HTTP 503; `Retry-After: 60`; same bounded response | exact match | PASS | prerequisites lines 78-103; final report lines 82-90 |
| Public root | HTTP 200 | 200 | PASS | final report lines 72-80 |
| Public API health | HTTP 200 | 200 | PASS | final report lines 72-80 |
| Public index | HTTP 200 | 200 | PASS | post-deployment lines 22-29 |
| Published reads | HTTP 200 with published items | 200 with items | PASS | prerequisites lines 87-103; final report lines 72-80 |
| Authenticated worker health | HTTP 200 and healthy fields | 200 | PASS | final report lines 72-80 and 108-114 |

The exact paused body is retained only in the already-redacted prerequisite artifact; no request credentials or sensitive payloads are reproduced here.

### 4.2 API process cwd and import provenance

Status: **PASS**.

- configured/live cwd: `/data/symgov-releases/f0.4-1824309/backend`;
- imported package: `/data/symgov-releases/f0.4-1824309/backend/symgov_backend/__init__.py`;
- API state/health: `running/healthy`;
- restart count: `0`;
- candidate API image ID: `sha256:7edf094a0bfb805a6ed1c092d18e6c82294b944ea88a83f40988ff75a9dd929a`.

Citations: post-deployment lines 73-86; final report lines 39-59.

### 4.3 Image, release, and one-version identity

Status: **PASS**.

| Identity | Observed |
|---|---|
| Release identity | `f0.4-1824309` |
| Release SHA | `182430932ae315f472b9e3611d54ad4f08cee038` |
| Candidate Compose SHA-256 | `fc1ef48d7b3202d3d23ea4b49d85e1e1dc60a4418f51ce122c0084f224a77902` |
| API image tag | `symgov-hermes-api:f0.4-1824309` |
| API image ID | `sha256:7edf094a0bfb805a6ed1c092d18e6c82294b944ea88a83f40988ff75a9dd929a` |
| Frontend image ID | `sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10` |
| API package version | `0.1.6` |
| Frontend package version | `0.1.6` |
| Frontend build stamp | `2026-07-28.01` |
| Frontend source | F0.4 dist mounted read-only to `/usr/share/nginx/html` |
| Mixed version | absent |

Citations: consistency evidence lines 30-55 and 105-114; final report lines 39-68.

### 4.4 Public index and bundle comparisons

Status: **PASS**. Each public response returned HTTP 200 and was byte-identical to the prepared release.

| Asset | Prepared/public SHA-256 | Equality |
|---|---|---:|
| `index.html` | `8d7307ed3848daefb6ccb903f78eb6f8484741027275df46d45e3a23e8cad82d` | PASS |
| `assets/index-BJYUGZdx.js` | `d733c597e957b8ef2bcd609d5f763e6b12c2b8e859c232b81ac6b61debcfc01b` | PASS |
| `assets/index-DwfsD6QJ.css` | `594886e833e0231dc1ee99fd142ffc37b7ab4a0b5fbb1b867d981171d7cb6133` | PASS |

Citations: post-deployment lines 22-43; consistency evidence lines 57-78; final report lines 118-126.

### 4.5 Feature marker

Status: **PASS**.

The immutable F0.4 JavaScript passed exact-string checks for both required reviewed messages: review-success copy and accepted/pending-delivery copy. The final report records that two later shortened ad-hoc phrase needles returned zero, but those were not the sealed required strings; because the public JavaScript remained byte-identical to the sealed passing artifact, this is not a gate failure.

Citations: post-deployment lines 44-48; consistency evidence lines 80-83; final report line 126.

### 4.6 Generated OpenAPI 201/202/503 behavior

Status: **PASS** for `POST /api/v1/catalog/symbols/{symbol_ref}/feedback`.

- OpenAPI `3.1.0`;
- exact response statuses: `201`, `202`, `400`, `401`, `403`, `404`, `409`, `503`;
- response `200` absent;
- `201` and `202` use `#/components/schemas/FeedbackResponse`;
- `503` uses `#/components/schemas/PublishedFeedbackPaused`;
- `Retry-After` schema constant `60`;
- `Idempotency-Key` is a required header with string/UUID schema;
- `mutatesPublishedState=false` and `remainsPublished=true` are fixed;
- private workflow IDs absent;
- paused schema exact.

Citations: post-deployment lines 50-71; consistency evidence lines 85-103; final report lines 128-144.

### 4.7 Every required zero-delta check

Status: **PASS**. The finally-cleaned API-stage verifier compared pre/post state over the paused mutation probes and a 15-second worker observation interval.

| Required state category | Observed delta/result |
|---|---:|
| Database feedback/intake records | `0` |
| Governance audit records | `0` |
| Agent queue records | `0` |
| Agent queue statuses | unchanged / `0` delta |
| Review workflow state | `0` |
| Review case state | `0` |
| Review action state | `0` |
| Publication/read-model state | `0` |
| API-key `last_used_at` | unchanged |
| Matching Ed claim signatures | `0` |
| Matching Ed claim starts | none |
| Runtime published-review file count | `0` delta |
| Runtime published-review file digest | unchanged |

Citations: original API-stage board evidence on `t_3b9962b8`; prerequisites lines 73-103; final report lines 92-116.

### 4.8 Published-read, worker-health, and Ed-claim checks

Status: **PASS**.

- published reads remained HTTP 200 and returned items;
- worker health returned `publishedFeedbackClaimsPaused=true`;
- repository worker reported `taskRunning=true`;
- worker `lastError=null`;
- no matching `published_symbol_review_request` Ed claim started;
- unrelated/public read availability remained intact while intake was paused.

Citations: prerequisites lines 87-103; final report lines 108-116.

### 4.9 Pause installation and retention

Status: **PASS**.

- marker: `/data/symgov-runtime/maintenance/published-feedback.pause`;
- host evidence: regular empty file, mode `0600`, size `0`;
- API-container evidence: regular empty file, mode `0600`, size `0`;
- retained through successful completion: yes;
- audit-time read-only `stat`: still a regular empty mode-`0600` file of size `0`.

Citations: prerequisites lines 105-126; post-deployment lines 98-107; consistency evidence lines 129-134; final report lines 146-153.

### 4.10 Alembic and migration determination

Status: **PASS**.

| Check | Observed |
|---|---|
| `alembic current` | `20260721_0024` |
| `alembic heads` | `20260721_0024` |
| Current equals all heads | yes |
| Migration required | no |
| Migration occurred/executed | no |
| Migration backup required | no |
| Migration backup occurred/created | no |

**Determination:** F0.4 remained migration-free. No migration occurred and none was required. Consequently, no migration backup occurred and none was required. This exactly matches specification section 10.2, which requires only that production `current == heads` for a migration-free implementation.

Citations: specification lines 486 and 491; post-deployment lines 88-96; consistency evidence lines 116-127; final report lines 155-163.

## 5. Retained rollback metadata (secret-safe)

Status: **PASS / retained, not used**.

- rollback Git SHA: `45fc6e00b1372fce1e092ebe282f264ccd401cb3`;
- rollback identity: detached clean worktree `/data/symgov-releases/f0.3-45fc6e0`;
- audit-time HEAD check: exact SHA match; status path count `0`;
- Compose backup reference: `/data/symgov-release-state/f0.4-20260728T221111.687815566Z/docker-compose.rollback-evidence.yml`;
- backup metadata only: regular file, mode `0600`, size `2459`, SHA-256 `751547dd5dab826f3f6303c747d55526dfad41c1040c5a42e21180a03ff1f041`;
- backup contents: intentionally not inspected or reproduced in this artifact;
- rollback required: no;
- rollback executed: no.

Citations: prerequisites lines 105-126 and 141-145; consistency evidence lines 136-142; final report lines 175-181. The rollback ordering and prohibition on exposing the pre-F0.4 backend are retained in specification lines 493-497 and deployment manifest lines 257-267.

## 6. Missing, stale, contradictory, and hard-gate assessment

| Finding | Classification | Effect |
|---|---|---|
| Historical preflight and sealed manifest say `BLOCKED`; later addendum says `PREPARED`; deployment evidence says `PASS`. | Stale historical verdict, explicitly superseded by later authority; not rewritten. | No current hard-gate failure. The chronology and authority hierarchy are explicit. |
| `t_db81615d-preflight-evidence.json` says `BLOCKED_STAGE_OVERTAKEN`. | Stale duplicate-lane/stage-order finding. | No candidate or deployment failure; it correctly prevented duplicate mutation. |
| `t_0a129aca` contains a post-completion comment about a later duplicate read-only parser that did not execute. | Duplicate-lane provenance noise. | Does not negate the earlier operator-approved frontend activation or later post-deployment PASS evidence; no duplicate recreation occurred. |
| API-stage detailed evidence is persisted chiefly in the original board comment/handoff and summarized redacted JSON, rather than as a separate raw verifier-output file in the repository. | Evidence-packaging caveat. | Required paused-scope categories, exact statuses/header/body contract, zero-delta outcomes, reads, worker health, Ed claim, and cleanup are all persisted; no gate within that scope is unknown. |
| Post-deployment public-asset record notes that it accepted operator handoff rather than performing a duplicate fetch solely for provenance. | Evidence-provenance caveat. | Exact hashes are independently chained into the final consistency evidence and final live report; no gate within the paused-deployment scope is unknown. |
| Two ad-hoc shortened feature phrases returned zero in the final live command. | Apparent contradiction resolved by exact-marker authority. | Not a failure: sealed required exact strings passed, and public JS was byte-identical to that artifact. |
| Missing required gate evidence within the authorized paused-deployment scope | None found. | No gate in that scope classified `UNKNOWN`; final marker removal and unpaused verification are explicitly pending. |
| Contradictory current identity, health, hash, pause, OpenAPI, zero-delta, cleanup, or Alembic evidence | None found. | No unresolved contradiction. |
| Hard-gate failure requiring rollback | None found. | Rollback remained retained but unexecuted. |

## 7. Secret-safety review

This artifact intentionally excludes:

- credentials, passwords, tokens, API keys, cookies, sessions, connection strings, and environment values;
- expanded Compose configuration;
- sensitive request or response bodies;
- temporary credential values and user identifiers;
- private payloads or production record contents;
- rollback Compose contents.

Only non-secret release identities, hashes, bounded status/header facts, counts/deltas, file metadata, and persisted evidence references are included.

## 8. Final audit conclusion

The persisted F0.4 evidence is sufficient to classify every gate in the authorized paused-deployment scope as **PASS**, with no `FAIL` or `UNKNOWN` gate in that scope. Historical `BLOCKED` records and duplicate-lane holds are preserved and correctly explained rather than erased. The deployed API/frontend identity is one immutable F0.4 version, public assets match the prepared release, pause behavior and zero-delta protections passed, published reads and worker health remained good, matching Ed claims did not start, OpenAPI documents the exact 201/202/503 behavior, the pause marker remains installed, and Alembic current equals heads. Final marker removal and unpaused endpoint/worker-health verification were pending separate authorization and are not represented here as completed gates.

No migration occurred or was required. No migration backup occurred or was required. Rollback SHA and Compose backup remain retained and verified; rollback was neither required nor executed.
