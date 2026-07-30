# F0.4 final deployment report (secret-safe)

Final live confirmation: 2026-07-29T20:31:48Z<br>
Release outcome: **SUCCESS — F0.4 deployed as one version; published-feedback intake remains paused**<br>
Release identity: `f0.4-1824309`<br>
Release SHA: `182430932ae315f472b9e3611d54ad4f08cee038`

## Scope and evidence

This report consolidates the persisted, redacted prerequisites/API-stage, frontend-stage, post-deployment, and release-consistency evidence, plus the operator-approved final non-invasive live confirmation. The genuine historical preflight reports remain `BLOCKED`; later remediation and prerequisite/API-stage evidence are identified separately rather than relabelling those preflights. The final confirmation exited 0. It did not mutate production, recreate a service, create credentials, remove the pause marker, run a migration, or execute rollback.

Evidence chain:

- deployment prerequisites: `docs/plans/2026-07-29-f0-4-deployment-prerequisites-evidence-redacted.json` — PASS;
- post-deployment validation: `docs/plans/2026-07-29-f0-4-post-deployment-validation-evidence-redacted.json` — PASS;
- release consistency: `docs/plans/2026-07-29-f0-4-release-consistency-evidence-redacted.json` — PASS;
- final operator-session confirmation at 2026-07-29T20:31:48Z — exit 0, PASS.

The historical sealed manifest's earlier BLOCKED verdict was not rewritten. The later post-remediation PREPARED evidence and the deployment-stage evidence are the authority for this completed release.

## Command and check categories

Only bounded, secret-safe categories were used and reported:

1. immutable release, worktree, candidate Compose, rollback artifact, image, and prepared-asset identity checks;
2. `docker compose config --quiet` validation, with no expanded Compose output;
3. bounded API and frontend container state, health, restart-count, image, workdir, import-path, and selected mount inspection;
4. host/container pause-marker `stat` checks;
5. authenticated pause-contract probes against both browser aliases and the Catalog feedback route;
6. pre/post database, audit, queue, workflow, publication, API-key, Ed-claim-signature, and runtime-file comparisons across a 15-second observation interval;
7. published-read and authenticated worker-health probes;
8. public root, API health, index, JavaScript, and CSS status/hash checks;
9. generated OpenAPI contract assertions;
10. `alembic current` and `alembic heads` comparison;
11. host/container temporary-verifier residue checks and temporary-credential cleanup proof.

No credential, token, key, cookie, connection string, expanded configuration, or sensitive response data is reproduced here.

## Immutable candidate and live one-version identity

| Check | Expected | Observed | Result |
|---|---|---|---|
| Release root | `/data/symgov-releases/f0.4-1824309` | API backend and frontend dist resolve to this release | PASS |
| Release SHA | `182430932ae315f472b9e3611d54ad4f08cee038` | immutable detached release worktree matched | PASS |
| Candidate Compose SHA-256 | `fc1ef48d7b3202d3d23ea4b49d85e1e1dc60a4418f51ce122c0084f224a77902` | installed/live candidate matched | PASS |
| API image tag | `symgov-hermes-api:f0.4-1824309` | selected candidate tag | PASS |
| API image ID | `sha256:7edf094a0bfb805a6ed1c092d18e6c82294b944ea88a83f40988ff75a9dd929a` | live container matched | PASS |
| Frontend image ID | `sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10` | live container matched | PASS |
| API package version | `0.1.6` | `0.1.6` | PASS |
| Frontend package version | `0.1.6` | `0.1.6` | PASS |
| Frontend build stamp | `2026-07-28.01` | `2026-07-28.01` | PASS |
| Mixed version | absent | absent | PASS |

API process provenance:

- configured and live runtime cwd: `/data/symgov-releases/f0.4-1824309/backend`;
- imported package: `/data/symgov-releases/f0.4-1824309/backend/symgov_backend/__init__.py`;
- API state/health: `running/healthy`;
- final restart count: `0`.

Frontend provenance:

- active mount: `/docker/openclaw-hz0t/data/symgov-releases/f0.4-1824309/dist -> /usr/share/nginx/html`;
- mount is read-only;
- frontend state/health: `running/healthy`;
- final restart count: `0`.

The API and frontend therefore expose the same immutable F0.4 release root and package version.

## HTTP and pause-contract results

Public/read health:

| Probe | Status | Required result | Result |
|---|---:|---|---|
| Public root | 200 | readable | PASS |
| Public API health | 200 | healthy | PASS |
| Public index | 200 | readable | PASS |
| Published reads | 200 | returned published items | PASS |
| Authenticated worker health | 200 | healthy payload | PASS |

Paused mutation surfaces:

| Surface | Status | Required header | Secret-safe contract | Result |
|---|---:|---|---|---|
| Browser v1 alias | 503 | `Retry-After: 60` | `published_feedback_paused`, retryable | PASS |
| Browser legacy alias | 503 | `Retry-After: 60` | `published_feedback_paused`, retryable | PASS |
| Catalog feedback | 503 | `Retry-After: 60` | `published_feedback_paused`, retryable | PASS |

The bounded paused body was exactly the reviewed secret-safe schema. Sensitive request/response material is intentionally omitted.

## Zero-delta and worker results

A single finally-cleaned verifier compared state before and after all three paused mutation probes and across a 15-second worker observation interval. Every required delta was zero:

| State category | Delta/result |
|---|---:|
| Database feedback/intake records | 0 |
| Governance audit records | 0 |
| Agent queue records and statuses | 0 |
| Review workflow/case/action state | 0 |
| Publication/read-model state | 0 |
| API-key `last_used_at` | unchanged |
| Matching Ed claim signatures / claim starts | 0 / none |
| Runtime published-review file count | 0 delta |
| Runtime published-review file digest | unchanged |

Additional worker checks:

- `publishedFeedbackClaimsPaused=true`;
- repository worker `taskRunning=true`;
- worker `lastError=null`;
- no matching `published_symbol_review_request` Ed claim started;
- published reads remained available and returned items while intake was paused.

These results prove the pause blocked governed intake and matching Ed claims without changing durable state or blocking published reads.

## Public asset comparison and feature marker

| Asset | HTTP | Prepared SHA-256 | Public SHA-256 | Byte-equal |
|---|---:|---|---|---|
| `index.html` | 200 | `8d7307ed3848daefb6ccb903f78eb6f8484741027275df46d45e3a23e8cad82d` | `8d7307ed3848daefb6ccb903f78eb6f8484741027275df46d45e3a23e8cad82d` | yes |
| `assets/index-BJYUGZdx.js` | 200 | `d733c597e957b8ef2bcd609d5f763e6b12c2b8e859c232b81ac6b61debcfc01b` | `d733c597e957b8ef2bcd609d5f763e6b12c2b8e859c232b81ac6b61debcfc01b` | yes |
| `assets/index-DwfsD6QJ.css` | 200 | `594886e833e0231dc1ee99fd142ffc37b7ab4a0b5fbb1b867d981171d7cb6133` | `594886e833e0231dc1ee99fd142ffc37b7ab4a0b5fbb1b867d981171d7cb6133` | yes |

The sealed exact-string check of the immutable release JavaScript found one occurrence of each required F0.4 marker: the reviewed success copy and the accepted/pending-delivery copy. The final live command's two ad-hoc phrase needles returned zero because those shortened phrases were not the sealed required strings; this is not a failed gate. Public JavaScript remained byte-identical to the sealed artifact that passed the exact marker check.

## Generated OpenAPI contract

Generated read-only from the running API for `POST /api/v1/catalog/symbols/{symbol_ref}/feedback`:

- OpenAPI version: `3.1.0`;
- canonical operation JSON SHA-256: `f422d93b4930d3df77ecdb7d605972780f9e3f1a891d2bc63636f16b2d2c3015`;
- exact documented statuses: 201, 202, 400, 401, 403, 404, 409, 503;
- status 200 is absent;
- 201 and 202 reference `#/components/schemas/FeedbackResponse`;
- 503 references `#/components/schemas/PublishedFeedbackPaused`;
- 503 `Retry-After` schema constant is `60`;
- `Idempotency-Key` is a required header with string/UUID schema;
- `mutatesPublishedState=false` and `remainsPublished=true` are fixed contract values;
- private workflow IDs are absent;
- paused schema is exact.

Result: **PASS** for the required 201/202/503 contract.

## Pause marker, migrations, cleanup, and rollback

Pause marker:

- path: `/data/symgov-runtime/maintenance/published-feedback.pause`;
- host: regular empty file, mode `0600`, size 0;
- API container: regular empty file, mode `0600`, size 0;
- retained at successful completion: **yes**.

Migration state:

- `alembic current`: `20260721_0024`;
- `alembic heads`: `20260721_0024`;
- current equals all heads: **yes**;
- migration required/executed: **no/no**;
- migration backup required/created: **no/no**.

**F0.4 is migration-free. No migration and no migration backup were required.**

Cleanup:

- temporary browser/admin credentials: removed;
- temporary Catalog credential/key: removed;
- matching temporary credential residue: 0;
- host `/tmp/hermes-verify-*` residue: 0;
- API-container `/tmp/hermes-verify-*` residue: 0;
- expanded Compose/configuration output retained: none;
- credentials, tokens, keys, cookies, and sensitive response data retained in this report: none.

Rollback readiness was retained but not used:

- rollback SHA: `45fc6e00b1372fce1e092ebe282f264ccd401cb3`;
- rollback Compose backup: `/data/symgov-release-state/f0.4-20260728T221111.687815566Z/docker-compose.rollback-evidence.yml`;
- rollback required: no;
- rollback executed: no;
- reason: no hard gate failed.

## Final outcome

**SUCCESS for the authorized paused-deployment scope.** Every gate in that scope—remediated readiness, paused API-stage, frontend-stage, and final non-invasive confirmation—passed for the same immutable candidate. The genuine historical preflight reports remain `BLOCKED` records and were not rewritten. API and frontend are healthy and expose one F0.4 version; public assets match the prepared release byte-for-byte; the pause, OpenAPI, zero-delta, worker, cleanup, and Alembic gates pass.

`published-feedback.pause` remains deliberately installed. Published reads remain available, while published-feedback intake and matching Ed claims remain paused. Resuming intake by removing the marker is a separate production change requiring explicit authorization and is not part of this completed deployment.
