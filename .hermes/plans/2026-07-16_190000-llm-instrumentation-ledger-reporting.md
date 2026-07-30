# Symgov LLM instrumentation, authoritative ledger, and reporting plan

> Execute with strict RED→GREEN TDD, isolated from the dirty primary worktree, followed by exact-current specification and code-quality/security review.

## Goal

Instrument the three confirmed direct LLM egress boundaries (OpenRouter application chat/admin tests, Libby Gemini vision, and Vlad Gemini image editing), persist one privacy-safe immutable usage event per provider attempt in a Symgov-owned ledger, export the same normalized event to Langfuse when explicitly enabled, and expose admin-only UTC usage/cost reporting.

## Immutable base and isolation

- Base HEAD: `7e8bcd99e368ef4af57797b8ae3b323a8b15560d`.
- Implementation worktree: `/data/symgov-langfuse-items45` on branch `hermes/langfuse-items-4-5`.
- Primary `/data/symgov` worktree is protected and must remain untouched; it contains unrelated Catalog Developer Hub, user-role, build-stamp, and nginx work.
- No provider calls, production database mutation, deployment, push, service restart, secret inspection, or public Langfuse exposure.
- Commit only files named by this plan after both exact-current reviews pass.

## Privacy and accounting invariants

- Never persist/export prompts, completions, images, documents, filenames, source prose, provider request/response bodies, credentials, emails, names, raw IPs, cookies, or headers.
- One event per actual provider attempt, including failures/timeouts.
- Record actual resolved provider/model where returned; otherwise use requested model and label that fallback truthfully.
- Preserve provider-reported token/image usage only from controlled numeric fields.
- Prefer provider-reported USD cost; otherwise use an immutable price snapshot only when implemented and known. Unknown cost remains explicit—never invent cost.
- Local model calls, if added later, use external cost zero with `local_policy`; this milestone does not add a new Ollama egress.
- Ledger and Langfuse failures must never alter the originating LLM result/error.
- Langfuse and ledger receive the same normalized allowlisted event. Langfuse is disabled unless exact environment configuration is present.
- Production trace retention policy remains 30 days; aggregate reporting policy remains 24 months. This milestone reports ledger data and does not auto-delete ledger events.
- Calendar periods and aggregation boundaries are UTC. Investigate invoice reconciliation differences greater than USD 5.00.
- Budget thresholds remain out of scope until a complete period is reconciled.

## Task 1 — Generalize the normalized telemetry event and Langfuse transport

Files:
- Modify `backend/symgov_backend/services/llm_telemetry.py`
- Modify `tests/test_llm_telemetry.py`
- Create `tests/test_llm_langfuse_transport.py`

Requirements:
1. Expand the controlled event contract to the approved Phase 0 fields: immutable event/time IDs, provider/model/attempt/status, token buckets, image units, cost provenance, safe lineage, feature/prompt/release labels, initiator pseudonym, and safe error codes.
2. Keep strict plain-container, bounded numeric/string, exact-key, categorical, duplicated-provenance, and trace-seed validation.
3. Support approved use cases: `workspace_chat`, `admin_llm_test`, `symbol_property_vision`, and `vlad_graphic_edit`; providers `openrouter`, `google`, and future `ollama`; request kinds text/vision/image_generation.
4. Implement secret-safe HMAC initiator pseudonym derivation from an internal UUID when configured; return null when not configured rather than exposing identity.
5. Implement a Langfuse ingestion transport using the existing tested POC batch shape and Basic authentication, but load credentials only from environment, hide them from repr/errors, bound timeout, and never include raw content.
6. Keep exact `SYMGOV_LLM_TELEMETRY_ENABLED=true` activation. Missing endpoint/public/secret configuration means disabled.
7. Keep bounded non-fatal async export with duplicate/non-sequential attempt rejection and a testable bounded flush/shutdown path for short-lived workers.
8. Offline tests only; no endpoint is contacted.

Verification:
- `PYTHONPATH=backend pytest tests/test_llm_telemetry.py tests/test_llm_langfuse_transport.py -q`

## Task 2 — Add the immutable authoritative usage ledger

Files:
- Create `backend/alembic/versions/20260716_0021_llm_usage_events.py`
- Modify `backend/symgov_backend/models/schema.py`
- Modify `backend/symgov_backend/models/__init__.py`
- Create `backend/symgov_backend/services/llm_usage.py`
- Create `tests/test_llm_usage_model.py`
- Create `tests/test_llm_usage_service.py`

Requirements:
1. Add append-only `llm_usage_events` with UUID event ID, UTC occurred time, environment/trace/observation, use-case/service/agent/provider/model/request-kind, attempt/status/latency, mutually separated usage buckets, provider/calculated costs, currency/basis/pricing version, safe lineage IDs, feature/prompt/release, initiator kind/pseudonym, safe error class/code, and bounded provider-specific numeric usage JSON.
2. Add check constraints for controlled categories, non-negative usage/cost/latency, positive attempts, USD currency, and mutually coherent cost provenance.
3. Add indexes for occurred time; agent/use-case; provider/model; feature; initiator pseudonym; trace/attempt; and human-readable symbol ID.
4. Repository insert accepts only a validated normalized telemetry event, never updates/deletes an event, commits in its own short-lived transaction, and catches all persistence failures at the best-effort boundary.
5. Duplicate `event_id` is idempotently ignored/reported without overwriting prior facts.
6. Add UTC day/week/month/month-to-date boundary calculation and aggregation by agent, use case, provider/model, feature, initiator pseudonym, outcome, and cost basis.
7. Report provider-reported and calculated amounts separately, effective spend, unknown/estimated event counts, retries/fallback attempts, errors, and local/external split.
8. Add pure invoice-reconciliation calculation with absolute USD difference and `requiresInvestigation` only when difference is greater than 5.00.
9. A pricing change never rewrites historical rows.
10. Migration is reversible and linked to `20260714_0020`; validate metadata, graph, upgrade/downgrade/re-upgrade on disposable PostgreSQL.

Verification:
- `PYTHONPATH=backend pytest tests/test_llm_usage_model.py tests/test_llm_usage_service.py -q`
- Alembic head/metadata checks
- Disposable PostgreSQL upgrade→downgrade→upgrade with physical schema inspection

## Task 3 — Instrument actual provider boundaries

Files:
- Modify `backend/symgov_backend/services/llm.py`
- Modify `backend/symgov_backend/routes/llm.py`
- Modify `scripts/run_libby_classification.py`
- Modify `scripts/run_vlad_validation.py`
- Modify/add focused tests: `tests/test_admin_llm_management_routes.py`, `tests/test_libby_symbol_vision.py`, and a Vlad Gemini telemetry test file

Requirements:
1. OpenRouter: create a server request UUID trace; classify admin test vs workspace chat; map prompt/completion/total/cached/reasoning tokens and reported cost only from numeric provider usage fields; record success and failure attempts; never inspect/store prompt/output.
2. Route context supplies safe feature, request/admin use case, and HMAC pseudonym from authenticated user UUID where configured.
3. Libby: use queue-item trace when a valid queue UUID is available, otherwise a generated request trace; map Gemini `usageMetadata` prompt/candidate/total/cached/thought token counts; preserve queue/run/symbol/display-ID lineage; record success/failure without changing heuristic fallback behavior.
4. Vlad: instrument only the Gemini image-edit provider attempt, not local Pillow/Tesseract work; retain image units only when the provider returns controlled usage metadata and never infer text-token cost for image generation; preserve queue/symbol lineage; record failures without changing existing fallback behavior.
5. Every boundary invokes best-effort ledger persistence and optional Langfuse export independently. Either can fail without affecting the LLM operation.
6. Short-lived scripts flush bounded telemetry before exit where practical; flush failure remains non-fatal.
7. Tests prove one event per attempt, no events for deterministic/non-provider paths, exact success/failure status, real resolved model mapping, safe lineage, no content leakage, and non-fatal sink failures.

Verification:
- Focused route/Libby/Vlad tests
- Existing adjacent runner and LLM route suites
- `PYTHONPATH=backend pytest tests/test_llm_*.py tests/test_admin_llm_management_routes.py tests/test_libby_symbol_vision.py -q`

## Task 4 — Admin-only usage and cost reporting API/UI

Files:
- Modify `backend/symgov_backend/routes/llm.py`
- Modify `backend/symgov_backend/schemas.py`
- Modify `frontend/src/api.js`
- Modify minimally `frontend/src/App.jsx` within the existing Admin LLM page
- Modify `frontend/src/styles.css`
- Create/modify tests for route authorization/contracts, UI source behavior, frontend pure formatting if needed

Requirements:
1. Add admin-only `GET /api/v1/admin/llm/usage` with allowlisted period `day|week|month|mtd`, optional ISO date anchor, and UTC boundaries returned explicitly.
2. Return totals and breakdowns by agent, use case, provider/model, feature, initiator pseudonym, status, and cost basis; distinguish provider-reported, calculated, effective, estimated, unknown, local-policy zero, retries/fallbacks, and errors.
3. Non-admin access is 403 and unauthenticated access is 401. Invalid periods/dates are 422 without unbounded parsing.
4. Extend the existing Manage LLM admin page with a period selector, explicit UTC labels, summary cards, unknown/estimated warnings, and accessible tables. Do not expose raw trace metadata or identities.
5. Add invoice reconciliation explanation and the approved `>$5.00` investigation policy; no budget controls.
6. API/client/UI error states are accessible and do not mislabel unknown cost as zero.

Verification:
- Focused backend route tests
- Frontend source/helper tests
- `node --test frontend/src/*.test.js`
- `npm run build`

## Task 5 — Integration verification and exact-current reviews

1. Run all focused telemetry/ledger/reporting tests.
2. Run adjacent auth/admin/agent-runner suites.
3. Run `PYTHONPATH=backend pytest tests -q`.
4. Run all frontend node tests and `npm run build`.
5. Run `git diff --check` and whitespace checks for untracked files.
6. Capture exact changed-path allowlist and immutable digest.
7. Independent specification-compliance review against this plan.
8. Only after spec PASS, independent code-quality/security/privacy/accounting review against the same digest.
9. Any valid finding requires RED regression, fix, full reverification, and both reviews again.
10. Stage only the allowlisted item 4–5 paths, verify staged scope, commit to `hermes/langfuse-items-4-5`, and report the commit SHA. Do not push or deploy.
