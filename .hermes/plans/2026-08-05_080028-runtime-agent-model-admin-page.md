# Runtime Agent Model Administration Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Extend the existing Symgov Admin > Manage LLM page so an administrator can select and persist the runtime provider/model for Scott, Tracy, Vlad, Libby, Daisy, Rupert, Hannah, Reggie, Whitney, and Ed, with new work using the selected assignment without restarting the application.

**Architecture:** Replace the current JSON settings source with a small database-backed runtime configuration model. Store one row per supported agent containing its OpenRouter model and editable description, plus a singleton/global default model row. Expose the configuration through the existing admin LLM API. Centralize runtime resolution so every application agent reads the current assignment at invocation time; each new run snapshots the requested model, while global fallback behavior remains responsible for provider failures and quota exhaustion. The Admin page will load the live OpenRouter catalogue, render one row per agent, show current accessibility/quota health, validate/save changes transactionally, and offer per-agent smoke tests.

**Tech Stack:** FastAPI/Pydantic, SQLAlchemy/Alembic/PostgreSQL, React 19/Vite, existing OpenRouter client and LLM usage ledger, pytest and frontend source-contract tests.

---

## Findings from the current repository

- Admin LLM UI already exists at `frontend/src/App.jsx` as `AdminLlmPage`, routed at `/workspace/llm` and admin-protected.
- Existing API client methods are in `frontend/src/api.js`.
- Existing API routes are in `backend/symgov_backend/routes/llm.py`:
  - `GET/PATCH /admin/llm/settings`
  - `GET /admin/llm/openrouter-models`
  - `POST /admin/llm/test`
  - authenticated `/llm/chat`
- Existing settings service is `backend/symgov_backend/services/llm.py`; it stores `defaultModel` and arbitrary `featureModels` in a JSON file selected by `SYMGOV_LLM_SETTINGS_PATH`. This is the migration source/compatibility surface, not the target persistence design.
- Existing service resolution is `resolve_model_for_feature()` and must be extended rather than bypassed.
- Current application scripts contain hard-coded run-record model strings, notably `ollama/gemma4:e4b`, in:
  - `scripts/run_scott_intake.py`
  - `scripts/run_tracy_provenance.py`
  - `scripts/run_vlad_graphic_edit.py` (verify exact path/name)
  - `scripts/run_libby_classification.py`
  - `scripts/run_daisy_review_coordination.py` (verify exact path/name)
  - `scripts/run_rupert_publication.py`
  - `scripts/run_hannah_curation.py`
  - `scripts/run_whitney_market_intelligence.py`
  - `scripts/run_ed_*.py` / Ed route or runner (discover exact path)
  - Reggie route/runner (discover exact path)
- Existing `llm_usage_events` migration currently constrains `agent_slug` to `libby`, `vlad`, and `ed`; this must be reconciled before claiming complete per-agent telemetry.
- Current settings default path is a legacy `/data/.openclaw/...` path. Treat it as a one-time migration source only; the database becomes the sole runtime source of truth after migration. Do not silently maintain two writable sources.
- Current Admin page only exposes default model and an Ed concierge override, so the feature should extend it rather than create a second Admin page.

## Decisions and non-goals

- Fixed supported agent set for this feature: `scott`, `tracy`, `vlad`, `libby`, `daisy`, `rupert`, `hannah`, `reggie`, `whitney`, `ed`.
- Store provider and model as a pair, while constraining v1 provider to `openrouter`. This prevents ambiguous model IDs without introducing provider configuration prematurely.
- Persist an editable short description per agent in the same database-backed settings row. Seed descriptions from the current Symgov roster, but allow administrators to edit them.
- Do not create a durable admin change-history table for model/description edits. The current value, `updated_at`, and ordinary application audit/logging are sufficient for this feature.
- Initial release supports OpenRouter models returned by the existing `/admin/llm/openrouter-models` endpoint. Do not allow arbitrary client-supplied provider URLs or API keys.
- Changes apply to new requests/runs only. A running agent run keeps its resolved model; the UI must state this explicitly.
- Safe fallback behavior: an absent agent assignment resolves to the global default model. The selected agent model is attempted first; the existing global fallback policy is then used for rate limits, overloads, connection failures, and provider quota/token failures. The admin screen must show whether the configured model is healthy, unavailable, stale/unverified, or recently failed, and must distinguish “configured model” from “currently resolved fallback model.”
- Admin model test must be a real low-token request using the selected agent slug and selected model, without executing the agent's tools or mutating domain data.
- Do not change live deployment, migrations, gateway services, or production settings as part of implementation. The plan includes a migration design, not execution.

---

## Implementation plan

### Task 1: Freeze the runtime agent contract and discover every invocation boundary

**Files:**
- Inspect: `scripts/run_*.py`, `backend/symgov_backend/services/`, `backend/symgov_backend/routes/`, `backend/symgov_backend/models.py`
- Test: add or extend `tests/test_runtime_agent_model_contract.py`

Steps:
1. Enumerate every runtime invocation and exact slug for all ten agents, including Reggie and Ed.
2. Record which paths currently call OpenRouter directly, which use a shared service, and which only write a model field to a run record.
3. Define one canonical `SUPPORTED_RUNTIME_AGENT_SLUGS` tuple and one `agent_slug` validation helper.
4. Add tests asserting the exact ten slugs, lowercase normalization, rejection of unknown slugs, and default-resolution behavior.
5. Run the focused test and preserve the discovery table in the implementation handoff.

Acceptance: every named application agent has an identified runtime call boundary and an unambiguous slug.

### Task 2: Design and migrate database-backed runtime settings

**Files:**
- Create/modify: `backend/symgov_backend/models.py`
- Create: `backend/alembic/versions/<timestamp>_runtime_agent_llm_settings.py`
- Modify: `backend/symgov_backend/services/llm.py`
- Modify: database/session helpers as needed
- Test: existing backend model/migration/service test locations

Steps:
1. Define a table such as `runtime_agent_llm_settings` with a stable `agent_slug` primary key, `provider`, `model`, editable bounded `description`, `updated_at`, and an optional `last_health_check_at` plus health status fields. Use a reserved singleton slug such as `__default__` for the global default, or a separate singleton table if that better matches existing conventions.
2. Add database constraints: exact supported agent slugs, provider `openrouter` only for v1, bounded model/description lengths, non-empty model, and one row per slug.
3. Add a migration that seeds all ten agents and the global default from the existing JSON settings, preserving the current default and Ed feature model where it maps to an agent. Define deterministic seed descriptions from the current roster.
4. Make the database the only writable runtime source. If compatibility fallback to JSON is needed during rollout, make it read-only and temporary, with an explicit migration-complete marker; do not allow divergent writes.
5. Implement transactional read/update functions. A settings update must validate the complete submitted set before changing any row; use a single transaction and return the committed snapshot.
6. Implement `resolve_model_for_agent(agent_slug)` using a fresh database read at invocation time, returning the selected provider/model and a resolution source (`agent_override` or `global_default`).
7. Add a separate health-state service/API cache model only if health status must survive process restarts; otherwise derive status from recent smoke tests and usage events. Do not use health status to mutate the configured model.
8. Write RED tests for seeding, uniqueness, unknown-slug rejection, transactional rollback, default fallback, editable description persistence, and concurrent update behavior; implement GREEN behavior.

Acceptance: the database is the sole runtime source of truth, all ten agents plus the global default are seeded, descriptions persist, and resolution is deterministic without process restart.

### Task 3: Extend admin API schemas and routes

**Files:**
- Modify: `backend/symgov_backend/schemas.py`
- Modify: `backend/symgov_backend/routes/llm.py`
- Modify: `backend/symgov_backend/services/llm.py`
- Test: `backend/tests/test_llm_routes.py` or the existing admin/LLM route test files

Steps:
1. Extend `LLMSettingsResponse` and `LLMSettingsUpdateRequest` with a complete `agents` array or `agentModels` mapping containing `slug`, `description`, `provider`, `configuredModel`, `resolutionSource`, `healthStatus`, `healthCheckedAt`, and recent failure/quota metadata (safe, non-secret fields only).
2. Keep `/admin/llm/settings` admin-only; require the complete submitted agent set and descriptions to validate before one database transaction updates any row. Do not add model-change history.
3. Add a health/status endpoint or include status in settings response. It must distinguish catalogue presence from successful access, recent request failure, rate-limit/quota exhaustion, and unknown/unverified state.
4. Add `POST /admin/llm/test-agent` or extend the existing test endpoint with a required `agentSlug` and `useConfiguredModel=true`. The endpoint performs a bounded, non-mutating OpenRouter request and updates the latest health observation; it must not execute the agent runner or mutate domain data.
5. Pass `agent_slug` and `initiator_kind='admin'` into the usage ledger for smoke tests. Use recent usage/error events to explain fallback status, but do not rewrite the configured model when fallback occurs.
6. Return safe error details for unknown agent, unavailable model, provider failure, and quota/token exhaustion; never expose API keys or provider headers.
7. Add route tests for admin authorization, exact response shape, all ten assignments/descriptions, atomic rejection of unknown keys, fallback-to-default resolution, status classification, and test telemetry attribution.

Acceptance: the API exposes and transactionally updates all ten runtime assignments plus descriptions, and reports whether each configured model is accessible or currently falling back.

### Task 4: Replace hard-coded runtime model selection in each agent

**Files:**
- Modify: exact agent runners discovered in Task 1, including Scott, Tracy, Vlad, Libby, Daisy, Rupert, Hannah, Reggie, Whitney, and Ed
- Modify: shared LLM invocation helper(s)
- Test: focused tests beside each runner plus `tests/test_runtime_agent_model_contract.py`

Steps:
1. Replace every hard-coded model used to make an LLM request with `resolve_model_for_agent('<slug>')` at the beginning of each new run/request.
2. Ensure the selected provider/model is passed to the actual request, not merely recorded after the call.
3. Ensure prompt/tool behavior and deterministic validation remain unchanged.
4. Record both requested and provider-resolved model in the run artifact and usage ledger; use the canonical slug for all ten agents.
5. Ensure a settings change affects the next invocation without process restart or module reload; do not cache assignments globally.
6. Add per-agent tests mocking the provider boundary and asserting the configured model is sent; add a missing-assignment test asserting default fallback.
7. Run all focused agent tests and a static search for remaining hard-coded runtime model use.

Acceptance: each named agent’s next LLM request uses the Admin-selected assignment, while in-flight work is unaffected.

### Task 5: Reconcile telemetry and run-record schema for all agents

**Files:**
- Modify: `backend/symgov_backend/services/llm_telemetry.py`
- Modify: `backend/symgov_backend/models.py` if ORM constraints/models exist
- Modify: `backend/alembic/versions/<new_revision>_expand_llm_agent_slugs.py` only if the current DB constraint is live and needed
- Modify: relevant run-record schemas/models
- Test: telemetry/ledger tests and migration tests

Steps:
1. Verify the deployed database revision and whether the current `llm_usage_events_agent_slug` check is active.
2. If active, add a forward-only migration expanding the allowed agent slugs to the ten names while retaining `NULL` semantics; preserve downgrade safety documentation.
3. Update telemetry validation and service-name/use-case constraints only where required by actual runtime events; do not weaken checks unnecessarily.
4. Add assertions that failed, timed-out, and successful events preserve agent slug and requested/resolved model without secret values.
5. Add a migration test or SQL inspection proving the constraint accepts all ten names and rejects unknown names.

Acceptance: usage reporting and run artifacts accurately identify all ten agents and model/provider selections.

### Task 6: Extend the existing Admin Manage LLM page

**Files:**
- Modify: `frontend/src/App.jsx` (`AdminLlmPage`)
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/styles.css` or the actual stylesheet used by the page, if required
- Test: `tests/test_admin_user_management_ui.py` and frontend tests under `frontend/src/`

Steps:
1. Add a “Runtime agent models” section below the existing shared routing controls.
2. Render exactly ten stable rows/cards: Scott, Tracy, Vlad, Libby, Daisy, Rupert, Hannah, Reggie, Whitney, Ed.
3. Each row shows editable short description, fixed OpenRouter provider, configured model selector, inherited/override state, health badge, last health-check time, latest safe failure/quota message, and a per-agent “Test” action.
4. Populate model selectors from the live OpenRouter catalogue; include currently configured IDs even if the catalogue refresh fails, with visible stale/unavailable state.
5. Show the global fallback policy as read-only status/help text. When a configured model is inaccessible or out of tokens, show “configured model unavailable — global fallback may be used” and, where observable, the current resolved fallback provider/model. Do not imply the configured value changed.
6. Save all assignments and descriptions in one atomic “Save runtime agent models” action and show clear success/error toasts. The UI must not offer API-key or endpoint editing.
7. Add “Reset to default” per row; this clears the row override while preserving the editable description. Avoid destructive bulk reset unless explicitly designed.
8. Explain that changes apply to new runs and that running work is not switched mid-flight.
9. Preserve the existing shared default/Ed concierge controls only where they remain meaningful; migrate Ed to the same agent row without breaking existing API clients.
10. Add source-contract tests for all ten slugs/labels/descriptions, API methods, save payload, health-state rendering, fallback status wording, stale model state, admin-only route, and new-run semantics.

Acceptance: an admin can configure each agent’s OpenRouter model and description, see access/quota health and fallback state, and save without editing files or restarting services.

### Task 7: Implement model health, quota/access status, and global fallback visibility

**Files:**
- Modify: `backend/symgov_backend/services/llm.py` and fallback/error classification helpers
- Modify: `backend/symgov_backend/routes/llm.py`
- Modify: `backend/symgov_backend/schemas.py`
- Test: backend health/fallback tests

Steps:
1. Define safe status categories such as `healthy`, `unverified`, `catalogue_missing`, `access_denied`, `rate_limited`, `quota_exhausted`, `temporarily_failed`, and `fallback_active`.
2. Classify OpenRouter responses without exposing raw provider payloads, keys, or prompts. Treat catalogue presence as only `unverified`, not healthy.
3. Record the latest health observation per agent/model, either in the settings row or a small non-history health table/cache; retain only current status and timestamp because durable admin change history is not required.
4. Use the existing global fallback policy for eligible failures. Preserve the configured agent model and report both configured and actually resolved models in new-run telemetry.
5. Add tests for catalogue missing versus access failure versus 429/quota exhaustion, fallback activation, recovery after a successful test, and no model mutation during fallback.

Acceptance: administrators can tell whether an agent’s configured model is usable, why it is not usable, and whether global fallback is currently serving requests.

### Task 8: Add end-to-end runtime verification

**Files:**
- Test: backend route/service tests, agent runner tests, frontend tests, and a new integration test under `tests/`
- Docs: update the relevant Admin/operations documentation

Steps:
1. Start from database settings containing a distinct test model and description for each of the ten agents.
2. Invoke each runtime boundary with provider calls mocked at the network boundary; assert the exact model/provider pair and slug.
3. Change one assignment through the admin API; invoke that agent again without restarting; assert the new model is used.
4. Verify an in-flight invocation retains its original selected model.
5. Verify an invalid assignment or description is rejected atomically and prior settings remain unchanged.
6. Simulate access denial, rate limiting, and quota exhaustion; assert global fallback is attempted, configured values remain unchanged, status is updated, and the UI/API exposes safe status.
7. Run the repository’s backend and frontend test gates, plus a static scan for unsupported hard-coded model IDs.
8. Document the database settings table, migration/seed behavior, update semantics, health states, fallback policy, rollback-to-default behavior, audit limitation, and deployment boundary.

Acceptance: the feature is proven from Admin save through the next agent invocation, fallback resolution, health status, and usage record.

---

## Clarification: what “runtime boundary” means for Reggie and Ed

A runtime boundary is the actual code path where an application agent begins an LLM request—not merely the place where a run record stores a model name. For Reggie and Ed, the implementation must first determine whether they:

- make a direct OpenRouter request from a dedicated runner;
- call the shared `llm.py` service through an API route or worker;
- run as deterministic/non-LLM orchestration whose model field is provenance only; or
- delegate to another agent/process that owns the actual LLM call.

This matters because changing a provenance string would not change the model used. Task 1 must trace each agent from queue/route dispatch to the network request and assign the model at that request boundary. If Reggie or Ed has no current LLM call, the Admin row should still support a saved future-ready assignment, but the UI should show `not currently invoked` rather than falsely claim the model is active.

## Product decisions incorporated

- Persistence: PostgreSQL settings table is preferred and is now the target design; the legacy JSON file is migration input only.
- Admin history: no separate durable model-change history is required. Store current value, description, `updated_at`, and current health state only.
- Provider scope: OpenRouter only for v1.
- Fallback: one configured model per agent; global fallback policy applies uniformly. The UI reports configured model, health, fallback-active state, and observed resolved fallback model where available.
- Agent descriptions: editable short descriptions are saved transactionally with model settings.
- Runtime semantics: settings are read for each new run; running work is not hot-switched.

## Suggested initial model policy (for discussion, not implementation default)

Use capability tiers rather than arbitrary model-per-name choices:

- Scott, Tracy, Hannah, Whitney: efficient structured/research model.
- Vlad, Libby, Daisy, Rupert, Ed: stronger reasoning/vision or workflow model as applicable.
- Reggie: conservative validation/operations model.

The page should not hard-code this policy; it should show the current assignment and optionally display a recommendation. The admin remains the authority.

## Risks and open questions

1. **Actual runtime boundaries:** Some agents may still be deterministic runners whose `model` field is only provenance, not the request selector. Task 1 must distinguish these before implementation, especially for Reggie and Ed.
2. **Database rollout:** Confirm the live Alembic head, database URL, deployment topology, and whether all agent workers share the same database before migration design is finalized.
3. **Health retention:** Decide whether current health state belongs on each settings row or in a small separate current-status table. No historical model-change table is required.
4. **Secrets:** The page must never display or accept API keys. It should only select from server-fetched model IDs.
5. **Availability:** OpenRouter catalogue membership does not prove the account can use a model. Per-agent test and recent runtime failures are the useful signals.
6. **Fallback observability:** The UI can show the resolved fallback model only when the request telemetry captures it; otherwise show fallback-active without guessing.
7. **Deployment:** Application agents may run in separate processes/containers. Confirm all runners use the shared database and same OpenRouter credential before rollout.

## Verification commands

```bash
cd /docker/openclaw-hz0t/data/symgov
python3 -m pytest tests/test_admin_user_management_ui.py -q
python3 -m pytest backend/tests -q
npm run test:frontend
npm run build:isolated
```

Use the repository’s actual test paths if `backend/tests` is not present. Before any live rollout, verify the running API/container uses the new code and the same settings path, then perform one non-mutating smoke test per agent.

## Recommended execution sequence

1. Review and resolve the remaining Reggie/Ed runtime-boundary and database-topology questions.
2. Implement Tasks 1–5 backend/runtime/telemetry and the database migration first.
3. Implement Task 6 UI and Task 7 health/fallback visibility.
4. Complete Task 8 integration verification.
5. Run two-stage immutable review and final verification.
6. Only after explicit approval, prepare deployment; do not deploy or restart live services as part of planning.

Plan complete. Ready to execute using subagent-driven-development — I'll dispatch a fresh subagent per task with two-stage review (spec compliance then code quality). Shall I proceed?
