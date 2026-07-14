# Symgov Langfuse Phase 0: Telemetry Governance Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Approve a privacy-safe, auditable telemetry contract for every Symgov LLM call before Langfuse infrastructure or application instrumentation is introduced.

**Architecture:** Langfuse will be the trace/observability system; Symgov will retain a future append-only `llm_usage_events` ledger as the business-reporting and reconciliation record. The Phase 0 contract makes every LLM request attributable to a use case, agent/service, execution lineage, provider/model, usage and cost basis without sending raw project material by default.

**Tech Stack:** Symgov FastAPI/Python workers, PostgreSQL, direct OpenRouter and Gemini HTTP clients, Langfuse self-hosted (future), Docker Compose, MinIO, ClickHouse, Redis/Valkey.

---

## Scope and guardrails

### In scope

1. Define the controlled use-case taxonomy and telemetry fields.
2. Define cost/accounting semantics, including local Ollama usage and non-token image billing.
3. Define the minimum privacy, redaction and retention policy.
4. Define trace and correlation-ID conventions that preserve existing Symgov queue/run/symbol lineage.
5. Define Phase 1 proof-of-concept acceptance criteria.

### Explicitly out of scope

- Deploying Langfuse, ClickHouse, Redis/Valkey, or a new database.
- Installing Python dependencies or adding Langfuse SDK calls.
- Modifying Symgov routes, runners, containers, environment files, secrets, or models.
- Routing traffic through LiteLLM Proxy.
- Capturing old/historical LLM usage retroactively.

### Baseline facts verified on 2026-07-14

- The live Symgov API worker runtime is `direct`, not a shared LLM gateway.
- `backend/symgov_backend/services/llm.py` calls OpenRouter directly and returns provider `usage` and `latencyMs` to callers, but does not persist them.
- `scripts/run_libby_classification.py` directly calls Gemini vision for symbol-property review.
- `scripts/run_vlad_validation.py` can directly call Gemini for image editing.
- `agent_runs` stores agent model/timing/trace information but not request-level token or cost facts. A configured agent model must not be interpreted as proof that an LLM call was made.
- Existing business lineage includes queue items, agent runs, review cases, intake records, artifacts and human-readable symbol IDs.
- An existing integration plan already requires query/message sanitisation/truncation and avoidance of raw-IP logging: `docs/plans/2026-07-10-api-and-application-integrations.md:985-994`.

## Proposed decisions for approval

These are deliberately conservative defaults. They should be approved or amended before Phase 1 begins.

| Area | Proposed decision | Reason |
|---|---|---|
| Accounting currency | Store and report all source cost fields in USD decimal values; future UI may show a converted GBP view labelled as an estimate with FX date/rate. | Provider prices and Langfuse cost fields are USD-based; avoid irreversible FX ambiguity. |
| Source of truth | Provider-reported usage/cost wins; otherwise calculate from an immutable versioned price sheet recorded with the event; Langfuse's inferred dashboard cost is operational evidence, not the sole financial record. | Model pricing can change after ingestion. |
| Local model cost | Record Ollama token/request volume and `provider_cost_usd=0.000000`, `cost_basis=local_policy`; do not blend estimated server/electricity cost into provider spend. | Separates billable API spend from optional operational allocation. |
| Image/multimodal calls | Record all provider-supplied text/image/other units and a direct reported/calculated USD amount. Never substitute text-token pricing for image-unit billing. | Gemini image generation/editing can have non-text billing. |
| Prompt/output capture | Disabled by default in production telemetry. Do not emit raw prompts, completions, images, source documents, filenames, API keys, bearer tokens, emails, customer project prose or raw IP addresses. | Engineering/governance work may contain sensitive material. |
| Debug capture exception | A future admin-approved, time-bound debug flag may retain masked/truncated text only for a specific incident; default expiry 7 days and no attachments. | Supports diagnosis without creating a permanent content log. |
| Identity | Use internal UUIDs in the protected Symgov ledger; use a stable HMAC-derived pseudonymous ID in Langfuse where user/tenant attribution is needed. Never send email, Telegram/Discord ID, name or API key. | Enables aggregation without disclosing personal identifiers to observability tooling. |
| Trace relationship | One trace per user request or queue work item; one nested span per agent run; one generation observation per provider request/attempt. | Preserves retries, fallbacks and multi-agent work without double-counting. |
| Environment separation | Separate Langfuse projects and credentials for development/staging/production. | Prevents test data and lower-environment access from mixing with production telemetry. |
| Raw telemetry retention | 30 days for production trace data, 14 days for development/staging. Monthly aggregate ledger reports retained 24 months; future legal/contractual requirements may revise this. | Sufficient operational window with bounded sensitive-metadata exposure and storage growth. |
| Cost reconciliation | Monthly provider-invoice reconciliation is required before financial decisions rely on telemetry; investigate a difference greater than $5.00. | Handles provider rounding, delayed billing and unrecognised/non-token charges. |

## Controlled use-case taxonomy (v1)

Instrumentation must emit exactly one `use_case` from this list. New values require a code review and this document's taxonomy update.

| Use case | Owner/path today | Trigger and accounting intent |
|---|---|---|
| `workspace_chat` | `services/llm.py` via `/api/v1/llm/chat` | Authenticated product chat/assistant request. Attribute to feature and initiating user/integration where present. |
| `admin_llm_test` | `services/llm.py` via `/api/v1/admin/llm/test` | Administrator connectivity/model test; distinguish from user-facing consumption. |
| `symbol_property_vision` | Libby `call_gemini_symbol_property_review()` | Vision classification of a symbol image; link to queue/run/symbol/review lineage. |
| `vlad_graphic_edit` | Vlad `edit_image_with_gemini()` | Optional Gemini image-generation/editing fallback for requested graphic changes. |
| `agent_reasoning` | Future specialist agent text/model call | Reasoning/planning call made by a named Symgov specialist. |
| `agent_research` | Future research/provenance/curation call | Research/citation/source-analysis call made by a named specialist. |
| `embedding_or_retrieval` | Future only | Embedding/vector/reranking use; must not be used for chat generations. |
| `other_approved` | Exceptional only | Requires `use_case_detail` and an issue/decision reference; remove once a stable taxonomy value exists. |

Non-LLM deterministic tooling (OCR, Tesseract, Pillow, local file conversion, web download, rule-based classification) must not create LLM-cost events merely because it appears in an agent run.

## Telemetry data contract (v1)

### Required on every LLM request event

```text
event_id                    UUID, generated once per provider request attempt
occurred_at_utc             ISO-8601 UTC timestamp
environment                 development | staging | production
trace_id                    deterministic trace identifier
observation_id              provider-request attempt identifier
use_case                    controlled taxonomy value
service_name                symgov-api | specialist runner name
agent_slug                  null | scott | vlad | tracy | libby | daisy | rupert | ed | hannah | whitney
provider                    openrouter | google | openai | anthropic | ollama | other
requested_model             model requested by Symgov
resolved_model              provider-reported/routed model when supplied, otherwise requested model
request_kind                text | vision | image_generation | embedding | other
attempt_number              integer >= 1
status                      succeeded | failed | timed_out | cancelled
latency_ms                  integer/null
cost_currency               USD
cost_basis                  provider_reported | price_snapshot | local_policy | estimated
pricing_version             immutable price-sheet identifier/null
provider_reported_cost_usd  decimal/null
calculated_cost_usd         decimal/null
```

### Required when the corresponding provider reports them

```text
input_tokens
output_tokens
cached_input_tokens
cache_write_input_tokens
reasoning_tokens
image_input_units
image_output_units
other_usage_json            small structured provider-specific quantities only; no raw response body
```

### Correlation fields

All are optional only when the call genuinely lacks that upstream context. Use opaque UUIDs in Langfuse, except the approved human-readable display ID below.

```text
queue_item_id
agent_run_id
review_case_id
intake_record_id
source_package_id
symbol_id
symbol_display_id           e.g. 0003-12, only if already public/approved in Symgov UI
feature                     short controlled feature label, e.g. edConcierge
prompt_version              stable code/prompt version identifier
release                     deployed application version/commit when available
initiator_kind              user | api_key | admin | scheduled_worker | system
initiator_pseudonym         HMAC-derived opaque stable identifier/null
```

### Fields forbidden from Langfuse and the future ledger

```text
raw prompt or completion text
full image/document payloads or object URLs
API keys, bearer tokens, cookies and request headers
email addresses, phone numbers, Telegram/Discord identifiers, names
raw IP addresses
full provider response/request bodies
unredacted filenames or source notes
```

`error_class` and `error_code` are permitted; `error_detail` must be a predefined safe error code or a bounded/redacted diagnostic string.

## Cost calculation rules

1. Store provider usage exactly as reported, after normalising token buckets to mutually exclusive values where required by the provider schema.
2. Store `provider_reported_cost_usd` only when the provider/gateway returns a numeric USD cost for that request.
3. If no reported cost is available, calculate with the active immutable price-sheet version and store both `calculated_cost_usd` and `pricing_version`.
4. For a retry or fallback, write a distinct event for each provider request attempt. Sum events, never overwrite an earlier failed/succeeded attempt.
5. Monthly totals must sum `COALESCE(provider_reported_cost_usd, calculated_cost_usd, 0)` and separately count events whose cost is unknown or estimated.
6. A local Ollama event contributes to token/request/latency reports but to external provider spend as $0.00 under `local_policy`.
7. Image generation/editing must use provider-reported cost or a documented price-sheet unit calculation; its image units must remain separate from text-token columns.
8. Price-sheet changes create a new version. No historical event is rewritten merely because a provider changes its public price.

## Trace and metadata conventions

### Trace seed

Use a deterministic but non-secret seed based on one of:

```text
queue:<queue_item_uuid>
request:<server_generated_request_uuid>
```

Do not seed from user input, source filename, document contents or a provider request ID. The resulting Langfuse trace ID must be stored in the ledger and must not be treated as a business identifier.

### Langfuse-safe metadata

Metadata keys must use alphanumeric names because propagated Langfuse metadata has that constraint. Values must be compact strings of 200 characters or fewer.

```text
environment
service
agent
usecase
provider
model
requestkind
queueitemid
agentrunid
symbolid
symboldisplayid
feature
initiatorkind
initiatorpseudonym
pricingversion
costbasis
release
```

Do not use metadata to encode JSON blobs, long filenames, user text or provider payloads.

## Phase 1 proof-of-concept acceptance criteria

Phase 1 may begin only after the above decisions are approved. It succeeds only if all of the following are demonstrated in an isolated non-production Langfuse project:

1. A synthetic OpenRouter text call is recorded with input/output usage, latency, provider/model and a valid USD cost basis.
2. A synthetic Gemini vision call is recorded with its provider usage schema mapped without storing image or prompt payloads.
3. A synthetic image-generation/edit event is recorded with non-token units/cost basis without inventing text-token counts.
4. One trace can be filtered by `agent`, `usecase`, `queueitemid` and `symboldisplayid` using only approved metadata.
5. A deliberately injected fake e-mail, bearer token and long source note are absent or redacted from exported/displayed trace data.
6. A failed/retried request creates two attempt observations and the aggregate cost totals the attempts correctly.
7. A weekly/monthly sample aggregate by agent, use case, model and initiator pseudonym is reproducible from raw events.
8. A provider statement/test fixture reconciles to the event total within the approved $5.00 tolerance.
9. A retention deletion test proves data expires according to the Phase 0 policy.
10. No production API key, production document, actual customer prompt or live user identity is used in the proof-of-concept.

## Implementation sequence after approval

### Task 1: Record the approved policy in product documentation

**Objective:** Publish the approved Phase 0 decisions in a durable Symgov design document before code changes.

**Files:**
- Create: `docs/plans/YYYY-MM-DD-langfuse-phase-0-decision.md`
- Reference: `docs/plans/2026-05-31-beta-gtm-roadmap.md`
- Reference: `docs/plans/2026-07-10-api-and-application-integrations.md`

**Steps:**
1. Copy the approved decisions, taxonomy, retention schedule and data contract from this plan.
2. Record any deviations as explicit decision records with owner/date.
3. Link the Beta & GTM roadmap's per-user LLM-cost requirement to this contract.

**Verification:** Review confirms all required decisions have an owner and no raw prompt/content field has been approved by accident.

### Task 2: Create a synthetic telemetry fixture set

**Objective:** Establish provider-neutral, secret-free examples for the POC and tests.

**Files:**
- Create: `backend/tests/fixtures/llm_usage/`
- Create: `backend/tests/test_llm_telemetry_contract.py`

**Steps:**
1. Add synthetic OpenRouter-like, Gemini-like, image-unit and retry response fixtures.
2. Add fixture data containing intentionally disallowed sensitive values to prove redaction.
3. Write contract tests for required fields, forbidden keys, cost-basis selection and mutually exclusive token buckets.

**Verification:** `PYTHONPATH=backend pytest backend/tests/test_llm_telemetry_contract.py -q` passes without network access.

### Task 3: Deploy an isolated Langfuse POC stack

**Objective:** Demonstrate the contract against Langfuse without touching production routing or production data.

**Files:**
- Create: a dedicated isolated Compose project outside the current Symgov production Compose file.
- Create: an environment template containing placeholders only; never store secrets in Git.
- Create: a short operator runbook with backup/retention configuration and teardown instructions.

**Steps:**
1. Use a separate project/network, dedicated database/user, object-storage bucket and service credentials.
2. Restrict access to the host/private network until auth and access scope are verified.
3. Configure explicit non-production retention and synthetic data only.
4. Verify health, persistence and data deletion using no live workload.

**Verification:** POC acceptance criteria 1–10 above pass; stack teardown leaves no Symgov production data in the POC stores.

### Task 4: Implement a shared Symgov telemetry adapter under TDD

**Objective:** Make later LLM instrumentation consistent and non-blocking.

**Files:**
- Create: `backend/symgov_backend/services/llm_telemetry.py`
- Create: `backend/tests/test_llm_telemetry.py`
- Modify later: `backend/symgov_backend/services/llm.py`
- Modify later: `scripts/run_libby_classification.py`
- Modify later: `scripts/run_vlad_validation.py`

**Steps:**
1. Write failing unit tests for validation, event construction, redaction, trace seed generation and no-op behaviour when telemetry is disabled.
2. Implement a provider-neutral event builder and validation layer.
3. Implement async/non-fatal exporter semantics: telemetry failure must not fail a user/agent LLM request.
4. Add the Langfuse transport only after the contract tests pass.
5. Instrument OpenRouter, Libby Gemini vision, and Vlad Gemini image edit in that order.

**Verification:** targeted unit tests, mocked provider responses, redaction tests, and existing LLM route tests all pass. No network endpoint is contacted in normal test runs.

### Task 5: Add the Symgov authoritative ledger and reporting view

**Objective:** Persist auditable rollups and create the weekly/monthly reporting path.

**Files:**
- Create: migration/model for `llm_usage_events`
- Create: repository/service and aggregation query
- Create: admin-only API endpoint and UI report
- Create: tests for event immutability, aggregation, identity pseudonymisation and invoice reconciliation

**Steps:**
1. Insert one immutable event per provider request attempt.
2. Create day/week/month aggregation queries by agent, use case, provider/model, feature and initiator pseudonym.
3. Mark unknown/estimated costs separately from provider-reported costs.
4. Add budget threshold evaluation only after at least one complete reconciled reporting period.

**Verification:** synthetic monthly report totals reconcile to fixture sums; access control blocks non-admin access; a price-sheet update does not alter historical events.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Telemetry inadvertently stores sensitive engineering content | Default content capture off; strict forbidden-field tests; masking/redaction POC; short retention. |
| Cost figures do not equal invoices | Preserve basis and pricing version; provider-reported amount preferred; monthly reconciliation threshold. |
| Langfuse stack grows beyond VPS capacity | Start synthetic/low-volume; explicit retention; monitor ClickHouse/disk; do not retain attachments. |
| Direct calls bypass instrumentation | Inventory all egress sites; add code-review rule; later consider LiteLLM mandatory egress. |
| Agent configuration is mistaken for usage | Only provider request events count toward consumption totals. |
| Trace metadata becomes high-cardinality or exposes business material | Restrict to listed compact IDs and controlled taxonomy; never send arbitrary JSON/text. |
| Observability outages affect Symgov | Export is asynchronous/non-fatal; ledger/export failures surface monitoring alerts but do not fail the underlying task. |

## Approved Phase 0 decisions

Approved by Chris on 2026-07-14:

1. Retain production trace data for 30 days and monthly aggregate ledger reports for 24 months.
2. Use calendar months and all source/reporting period boundaries in UTC.
3. Investigate monthly invoice reconciliation differences greater than $5.00. The prior percentage threshold is not adopted for the early pilot.
4. Include the existing approved human-readable `symbol_display_id` (for example, `0003-12`) in production telemetry when the LLM call has symbol lineage.
5. Grant Langfuse production-project access to authenticated Symgov users with the existing `admin` role only. No specialist/operator access is granted in Phase 1.

These decisions complete the policy choices required to begin the isolated Phase 1 proof of concept. Any exception requires a dated amendment to this document before implementation.

## Phase 0 completion checklist

- [x] Cost currency and three cost/basis semantics approved.
- [x] Use-case taxonomy v1 approved.
- [x] Required/forbidden telemetry fields approved.
- [x] Identity pseudonymisation and trace-seed scheme approved.
- [x] Retention and debug-exception policy approved.
- [x] Invoice reconciliation threshold approved ($5.00).
- [x] Phase 1 POC acceptance criteria approved.
- [x] No infrastructure or application changes have been made as part of Phase 0.
