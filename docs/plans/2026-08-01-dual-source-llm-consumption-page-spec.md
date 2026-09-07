# Symgov dual-source LLM consumption page — implementation specification

Date: 2026-08-01
Status: APPROVED FOR IMPLEMENTATION BY CHRIS
Production authority: source, migration and production deployment are in scope; no secrets may enter Git or logs.

## Goal

Add an admin-only LLM Consumption section to the existing Manage LLM page. Symgov's append-only `llm_usage_events` ledger remains authoritative. The page also queries the self-hosted Langfuse project and displays source health plus bounded reconciliation, without exposing prompts, completions, images, identities, raw traces or credentials.

## Current state

- Commit `65508719cf075f5baa961f15ec0b704c02f4fddc` added the sanitized ledger, Langfuse export transport, usage route stub and frontend API helper.
- `backend/symgov_backend/routes/llm.py:131-175` currently returns hard-coded zero values.
- `backend/symgov_backend/services/llm_usage_ledger.py` persists ledger events but lacks aggregate reporting.
- `frontend/src/api.js:362-367` can call the route, but `frontend/src/App.jsx:6392-6650` renders no consumption UI.
- Production remains on F0.4 `182430932ae315f472b9e3611d54ad4f08cee038`; migration `20260730_0025` and commit `6550871` are not active there.
- The running self-hosted Langfuse v3 instance currently contains a project named `Symgov Synthetic Telemetry POC`. Production telemetry must not be mixed into that synthetic project. A separate production project named `Symgov` is required.

## Product decisions

1. Sources: show both local ledger and Langfuse.
2. Authority: local ledger totals are authoritative; Langfuse is an observability/export comparison.
3. Langfuse: self-hosted; production project name `Symgov`.
4. Access: existing admin authorization only, with v1 and legacy route parity.
5. Delivery: independently reviewed, committed, migrated and deployed to production.

## API contract

Retain `GET /api/v1/admin/llm/usage` and legacy `/api/admin/llm/usage`.

Inputs:
- `period=day|week|month|mtd`, default `day`.
- optional ISO-8601 `anchor`; normalize to UTC and reject malformed/unbounded input with 422.

Response:
- `period`, `startUtc`, `endUtcExclusive`.
- `ledger.status`: `available|unavailable`.
- `ledger.totals`: attempts, successful, failed, latency, input/output/cached/reasoning tokens, effective cost USD, provider-reported cost USD, calculated cost USD, unknown-cost attempts, retry attempts.
- `ledger.breakdowns`: by provider/model, use case, agent and status. Null agent is labelled `unassigned`, not hidden.
- `langfuse.status`: `available|disabled|unavailable` and a secret-safe message.
- `langfuse.totals`: observations, input/output/total tokens and total cost USD when available; null when unavailable.
- `langfuse.byModel`: bounded model rows when available.
- `reconciliation.status`: `matched|different|unavailable|notComparable`; include absolute token/cost differences only when both values are comparable. Do not turn unknown values into zero. A difference is informational because asynchronous export and provider normalization can produce temporary differences.
- `warnings`: bounded list of operator-readable, secret-safe messages.

The route must never return Langfuse URLs containing credentials, authorization material, raw provider bodies, traces, observation IDs, prompts or identities.

## Local ledger aggregation

- Use bounded SQL aggregation over `occurred_at_utc >= start AND occurred_at_utc < end_exclusive`, filtered by the server-configured environment; do not load all events into Python.
- Count all attempts; successful status is `succeeded`; all other statuses count as failed for summary while remaining distinct in status breakdown.
- Effective cost uses provider-reported cost when present, otherwise calculated cost, otherwise unknown.
- Preserve token buckets separately; null-only values remain distinguishable through unknown counters/warnings.
- Return deterministic ordering by descending attempt count, then stable label.

## Langfuse query

- Support the deployed self-hosted Langfuse v3 legacy Metrics API: `GET /api/public/metrics?query=<bounded JSON>` with Basic Auth (public key as username, secret key as password).
- Query only the `observations` view and allowlisted measures: count, inputTokens, outputTokens, totalTokens and totalCost, grouped by `providedModelName`, for the same UTC bounds as the ledger.
- Enforce bounded timeout, response byte limit and row limit (100). Validate response shape and numeric bounds. Reject redirects so credentials cannot be redirected.
- Network/configuration/API failures degrade only the Langfuse portion; the authoritative ledger response remains usable.
- Reuse protected environment credentials; never expose them in repr, exceptions, logs, tests or API responses.
- Add explicit query configuration rather than guessing a project name. Project selection is determined by the project-scoped public/secret key pair.
- Permit plain HTTP only for the exact approved internal self-hosted service endpoint; external endpoints require HTTPS. Do not allow arbitrary internal-host SSRF destinations.

Expected protected runtime variables:
- `SYMGOV_LLM_TELEMETRY_ENABLED=true`
- `SYMGOV_LLM_TELEMETRY_ENDPOINT=http://symgov-langfuse:3000/api/public/ingestion`
- `SYMGOV_LLM_TELEMETRY_PUBLIC_KEY=[REDACTED]`
- `SYMGOV_LLM_TELEMETRY_SECRET_KEY=[REDACTED]`
- `SYMGOV_LLM_TELEMETRY_TIMEOUT_SECONDS=3`
- `SYMGOV_LANGFUSE_QUERY_ENABLED=true`
- `SYMGOV_LANGFUSE_QUERY_BASE_URL=http://symgov-langfuse:3000`

`symgov-langfuse` is the explicit stable alias assigned to the Langfuse web service on the shared internal Docker network. Runtime configuration must not depend on a Compose-generated container name.

The current synthetic POC keys must not be used for production. A separate `Symgov` project has been created in the self-hosted instance and its project-scoped keys installed in the protected deployment environment; the synthetic project remains unchanged.

## UI

Extend the existing admin Manage LLM page rather than add another navigation item in the first release.

- Heading: `LLM consumption`.
- Period selector: Today, This week, This month, Month to date; show exact UTC range.
- Summary cards: known spend, attempts, successes, failures, input tokens, output tokens and unknown-cost attempts.
- Source-status strip: authoritative Symgov ledger and Langfuse export/query status.
- Reconciliation callout that clearly explains local authority and asynchronous Langfuse comparison.
- Accessible responsive tables for provider/model, use case and agent breakdowns.
- Explicit loading, empty, partial/degraded and error states.
- Unknown cost is `Unknown`, never `$0.00`.
- Use existing visual tokens/classes where suitable; no unrelated redesign.

## Tests

Strict RED-GREEN implementation:

Backend:
- UTC period bounds, including aware/naive anchor handling.
- aggregate totals, null cost/token semantics, retries, status classification and deterministic breakdown order.
- admin 200 contract, non-admin 403, unauthenticated 401, alias parity, invalid period/anchor 422.
- Langfuse request shape/auth, exact approved endpoints, timeout, no redirects, response bounds and malformed response handling.
- Langfuse disabled/unavailable leaves ledger 200 and emits safe status/warnings.
- no secrets/raw content in output or errors.

Frontend:
- API helper contract.
- page contains period selector, summary/source/reconciliation states and accessible tables.
- formatter tests prove unknown values are not rendered as zero.
- loading, empty and degraded states.

Gates:
- focused usage/telemetry/route/UI tests.
- `./scripts/test-backend.sh --full`.
- `./scripts/test-frontend.sh`.
- `./scripts/test-langfuse-poc.sh`.
- isolated frontend production build.
- migration upgrade/downgrade/re-upgrade on disposable PostgreSQL from production predecessor.
- immutable specification review, then immutable security/code-quality review of the unchanged snapshot.

## Deployment and rollback

1. Create a clean immutable release worktree from the accepted commit.
2. Create/select production Langfuse project `Symgov`; preserve the synthetic project unchanged.
3. Install production project credentials only in the protected deployment environment and verify permissions without printing values.
4. Back up PostgreSQL and validate backup.
5. Apply Alembic through `20260801_0026` with the migration owner; verify current=heads, ledger table constraints/indexes, and the append-only runtime grants introduced by `0026`.
6. Validate compose configuration without printing expanded secrets.
7. Build/publish frontend from the exact accepted commit and recreate only the required API/web services.
8. Verify container import/worktree provenance, health, unauthenticated 401 on both usage aliases, authenticated admin response via a secret-safe temporary verifier, public bundle hash/feature marker, and Langfuse source status.
9. Confirm telemetry reaches the production `Symgov` project without sending a paid provider test solely for verification; use existing post-activation traffic or an explicitly synthetic ingestion event if required and label it synthetic.

Rollback:
- Restore the prior API/frontend release mounts and service definitions.
- Downgrade `20260801_0026` to revoke its runtime grants when reverting this release. Downgrade `20260730_0025` only if rolling back beyond commit `6550871`; that second downgrade deletes the ledger table and therefore requires explicit confirmation against the validated backup.
- Removing/disabling Langfuse query/export variables must leave core LLM operation and the ledger reporting path functional.

## Completion gate

Complete only when the exact commit, tests, review hashes, backup validation, migration revision, production container provenance, public bundle identity, authenticated page/API smoke evidence, Langfuse project identity/status and residual risks are recorded. No approval from a synthetic project or stale code snapshot may be reused.
