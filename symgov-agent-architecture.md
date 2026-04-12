# symgov Agent Architecture

Last updated: 2026-04-09

## Purpose

This document defines how `symgov` should introduce independent specialist agents into the product and operating model.

The design direction for this pass is:

- Agents should be independent as much as possible.
- Each agent should own its own queue and produce explicit outputs.
- The default low-cost model path should use the local Ollama provider with `ollama/gemma4:e4b`.
- Human review remains the final authority for high-risk governance and publication actions.

## Current fit with OpenClaw

The current OpenClaw environment already supports the model and specialist-agent pattern needed for Symgov:

- Local Ollama is configured at `http://ollama:11434`.
- OpenClaw agent defaults already point to `ollama/gemma4:e4b`.
- Existing specialist agents `Pat` and `Carly` already run on `ollama/gemma4:e4b`.
- Agent-to-agent routing is enabled, and `Vlad` is now included in the current allow-list with `cody`, `pat`, `carly`, and `codex`.

This means Symgov agent work does not need a new model strategy first. It needs agent definitions, queue contracts, and service boundaries.

Current implementation baseline:

- baseline `agent_definitions` rows for `scott`, `vlad`, and `tracy` now exist in the live Symgov database
- the current local runners remain file-backed first, but now support a first verified PostgreSQL write-through bridge for queue items, agent runs, output artifacts, and durable agent-specific records
- the current verified smoke path is `Scott` intake -> downstream enqueue -> `Vlad` validation + `Tracy` provenance
- the current bootstrap and inspection entrypoint is `backend/manage_symgov.py`

## Product alignment

The existing Symgov architecture is still the system of record:

- `Standards View` remains published-only.
- `Governance Workspace` remains the internal review and publication system.
- Clarifications still route back into governance review.
- Voting remains out of scope.

Agents should strengthen the existing governance pipeline, not replace the core product split.

## Operating model vs implementation model

The spreadsheet under `Documentation/Symgov` describes a future operating model with named specialist agents:

- `Scott` intake
- `Tracy` provenance and rights
- `Vlad` technical validation
- `Libby` classification and discoverability
- `Daisy` review coordination
- `Rupert` publication and release management
- `David` curation
- `Reggie` audit and compliance
- `Whitney` market intelligence
- `Ed` documentation and policy

None of these exist yet in the current prototype or backend architecture.

The implementation direction should be:

- model them as first-class backend agents with clear queue ownership
- keep outputs as durable records in the system of record
- keep approvals and policy exceptions human-authorized unless explicitly automated later

## Core design principles

### 1. Queue ownership

Each agent owns a queue of work items that match its responsibility.

The queue should include:

- item ID
- source entity type and ID
- status
- priority
- assigned agent
- input payload reference
- created time
- started time
- completed time
- confidence score
- escalation reason

No agent should consume another agent's internal prompt history as its primary state. Shared state should live in explicit records.

Current bridge note:

- the local runner bridge currently maps legacy string queue IDs and source IDs into deterministic UUIDs so the existing file-backed queue payloads can be mirrored into the live UUID-based schema without changing local runtime fixtures first

### 2. Durable outputs

Each agent should write structured outputs, not just chat text.

Examples:

- `Scott` writes intake records, extracted metadata, routing flags
- `Tracy` writes provenance reports, rights status, evidence notes
- `Vlad` writes validation reports, defect lists, normalized technical metadata
- `Libby` writes taxonomy assignments, aliases, search terms
- `Daisy` writes review cases, reviewer assignments, stage transitions
- `Rupert` writes release packages, publication logs, withdrawal actions
- `Reggie` writes anomaly alerts, control exceptions, audit summaries

Free-text summaries can exist, but downstream workflow should key off structured fields.

### 3. Tool-first execution

Agents should use deterministic tools first and LLM reasoning second.

Examples:

- file detection
- SVG parsing
- metadata extraction
- schema validation
- duplicate checks
- pack assembly
- audit event generation

Gemma should be used for classification, explanation, summarization, anomaly triage, and ambiguous routing, not for pretending to validate what a deterministic tool can prove.

### 4. Confidence-gated escalation

Every agent should return:

- decision
- confidence
- evidence
- escalation target

If confidence is below threshold or a policy rule trips, the item escalates to:

- a stronger model path later if needed
- a human reviewer
- or a coordinating agent such as `Daisy`

### 5. Human authority at governance edges

The following should stay human-authorized initially:

- final approval decisions
- publication approval
- rollback or withdrawal
- policy exceptions
- licensing conflict overrides

## Recommended Symgov data additions

The current architecture already has `change_requests`, `review_decisions`, `publication_packs`, `clarification_records`, and `audit_events`.

To support independent agents cleanly, add these families of records:

### Agent runtime records

- `agent_definitions`
  - id, slug, display_name, role, model, status
- `agent_queue_items`
  - id, agent_id, source_type, source_id, status, priority, payload_json, confidence, escalation_reason, created_at, started_at, completed_at
- `agent_runs`
  - id, queue_item_id, model, prompt_version, tool_trace_json, result_status, started_at, completed_at
- `agent_output_artifacts`
  - id, queue_item_id, artifact_type, schema_version, payload_json, created_at

Current status:

- these tables are no longer only planned; the first live schema exists and is now being exercised by the bootstrap CLI plus the `Scott` and `Vlad` write-through smoke path

### Intake and assessment records

- `intake_records`
  - id, source_type, source_ref, submitter, raw_object_key, status, created_at
- `provenance_assessments`
  - id, intake_record_id, rights_status, confidence, summary, evidence_json, assessed_at
- `validation_reports`
  - id, intake_record_id or symbol_revision_id, validation_status, defect_count, normalized_payload_json, report_json, created_at
- `classification_records`
  - id, symbol_id or intake_record_id, category, discipline, aliases_json, search_terms_json, confidence, created_at

### Coordination records

- `review_cases`
  - id, source_entity_type, source_entity_id, current_stage, owner_id, escalation_level, opened_at, closed_at
- `publication_jobs`
  - id, pack_id, status, requested_by, approved_by, artifact_manifest_json, created_at, completed_at
- `control_exceptions`
  - id, source_type, source_id, severity, rule_code, detail, status, created_at

## Agent roster and boundaries

## Wave 1 agents

### `Scott` - intake agent

Owns:

- intake queue
- submission normalization
- basic metadata extraction
- routing flags

Inputs:

- uploads
- contributor submissions
- imported symbol libraries

Outputs:

- `intake_records`
- extracted metadata
- routing recommendation
- corruption or eligibility flags

Why first:

- clean entry point
- bounded scope
- strong queue ownership
- low publication risk

### `Vlad` - technical validation agent

Owns:

- validation queue
- file integrity checks
- format checks
- geometry and rule checks
- duplicate detection

Inputs:

- accepted intake records
- draft symbol revisions

Outputs:

- `validation_reports`
- normalized technical metadata
- pass or fail recommendation
- for PNG multi-symbol sheets in Phase 1:
  - `split_plan` artifacts
  - `derivative_manifest` artifacts
  - proposed child crop assets
  - review escalation into `raster_split_review`

Why first:

- most deterministic candidate
- easiest to backstop with tool-first checks
- strong fit for low-cost Gemma orchestration around deterministic validators

### `Tracy` - provenance and rights agent

Owns:

- provenance queue
- source lineage assessment
- rights and risk triage

Inputs:

- accepted intake records
- contributor declarations
- standards source references

Outputs:

- `provenance_assessments`
- rights status
- risk notes
- escalation for unclear ownership

Why in wave 1:

- highly important before publication

## Current bootstrap and usage notes

- Seed baseline agent rows with:
  - `python backend/manage_symgov.py seed-agent-definitions`
- Inspect the live database with:
  - `python backend/manage_symgov.py check-db`
- Inspect local MinIO with:
  - `python backend/manage_symgov.py check-storage`
- Mirror a local runner execution into PostgreSQL with:
  - `python /data/.openclaw/workspaces/scott/run_scott_intake.py --queue-item ... --runtime-root ... --persist-db`
  - `python /data/.openclaw/workspaces/vlad/run_vlad_validation.py --queue-item ... --runtime-root ... --persist-db`
  - `python /data/.openclaw/workspaces/tracy/run_tracy_provenance.py --queue-item ... --runtime-root ... --persist-db`

## Current verified live state

- `agent_definitions` contains seeded rows for `scott`, `vlad`, and `tracy`
- `Scott` has been verified writing `agent_queue_items`, `agent_runs`, `agent_output_artifacts`, and `intake_records`
- `Vlad` has been verified writing `agent_queue_items`, `agent_runs`, `agent_output_artifacts`, and `validation_reports`
- `Vlad` Phase 1 raster split persistence has now also been verified for:
  - additional `agent_output_artifacts`
  - derivative child `attachments`
  - `review_cases`
- `Tracy` is wired for the same bridge path, but a live DB-backed provenance smoke write has not yet been verified in this pass
- independent output contract
- manageable even before advanced orchestration exists

### `Daisy` - review coordination agent

Owns:

- case coordination queue
- assignment suggestions
- stage movement proposals
- exception routing

Inputs:

- failed or passed validation
- provenance findings
- clarification links
- change request state

Outputs:

- `review_cases`
- reviewer assignment proposals
- stage movement recommendations
- contributor evidence requests

Why not first:

- depends on upstream outputs
- better once intake, validation, and provenance contracts exist

## Wave 2 agents

- `Libby` for classification and discoverability
- `Rupert` for publication jobs and release control
- `Reggie` for compliance monitoring and control rules

## Wave 3 agents

- `David` for catalogue curation
- `Whitney` for market intelligence
- `Ed` for documentation and policy maintenance

## Recommended first implementation slice

Build `Vlad` first, but define the shared queue and artifact model in a way that `Scott` and `Tracy` can adopt immediately after.

Reasoning:

- `Vlad` has the clearest deterministic tool path.
- It creates fast value without exposing public publishing risk.
- It proves the agent pattern without requiring full contributor workflow automation first.
- It is well suited to local Gemma as the primary reasoning model because validation should be tool-led and evidence-heavy.

Practical sequence:

1. Add shared agent runtime tables and schemas.
2. Implement `Vlad` queue, outputs, and validation toolchain.
3. Add `Scott` intake queue and routing outputs.
4. Add `Tracy` provenance queue and evidence outputs.
5. Add `Daisy` to coordinate exceptions and stage movement across those outputs.

## Gemma usage policy

Default model policy:

- Use `ollama/gemma4:e4b` for routine queue processing, extraction summaries, classification proposals, and escalation drafting.
- Prefer deterministic local tools for validation, parsing, and packaging.
- Reserve premium external models only for narrow exception paths if later needed.

Good Gemma tasks:

- summarize extracted evidence
- classify likely category and discipline
- explain validation failures in reviewer-friendly language
- draft provenance risk summaries
- recommend queue routing based on structured inputs

Bad Gemma tasks:

- inventing technical validation results without running validators
- making final publication decisions
- overriding rights conflicts without evidence
- silently repairing malformed data without traceability

## OpenClaw integration notes

To make Symgov agents real in the current environment, add:

- new OpenClaw agent entries for each Symgov agent
- workspaces such as `/data/.openclaw/workspaces/scott`, `/data/.openclaw/workspaces/vlad`, `/data/.openclaw/workspaces/tracy`, `/data/.openclaw/workspaces/daisy`
- `AGENTS.md` instructions per workspace
- helper scripts for structured task execution where deterministic tools exist
- agent-to-agent allow-list updates for the new agent IDs

If asynchronous external triggers are needed later, current hook allow-lists will also need extending because only `main` and `pat` are currently hook-enabled.

## Minimal rollout decision

For the next implementation pass, the recommended committed direction is:

- independent agents with owned queues and explicit outputs
- local `ollama/gemma4:e4b` as the default model path
- `Vlad` as the first implemented Symgov agent
- `Scott` and `Tracy` immediately after
- `Daisy` as the first coordination agent after those outputs exist

## Deferred decisions

- whether agent queues live inside the main Symgov database only or also map to OpenClaw session state
- whether `Daisy` should be a true orchestrator agent or a workflow service plus human UI affordance
- whether `Rupert` publication should ever auto-run below a risk threshold
- whether higher-cost external models should be allowed for policy ambiguity, rights ambiguity, or only for offline review

## Shared runtime contract for the first local implementation

The first runnable Symgov agent slice is file-backed in the `Vlad` OpenClaw workspace, but the contract should match the future backend record families as closely as possible.

Shared runtime records for this phase:

- `agent_definitions`
  - `id`, `slug`, `display_name`, `role`, `model`, `status`, `queue_family`, `created_at`, `updated_at`
- `agent_queue_items`
  - `id`, `agent_id`, `source_type`, `source_id`, `status`, `priority`, `payload_json`, `confidence`, `escalation_reason`, `created_at`, `started_at`, `completed_at`
- `agent_runs`
  - `id`, `queue_item_id`, `model`, `prompt_version`, `tool_trace_json`, `result_status`, `started_at`, `completed_at`
- `agent_output_artifacts`
  - `id`, `queue_item_id`, `artifact_type`, `schema_version`, `payload_json`, `created_at`

Queue item status values for the first local contract:

- `queued`
- `running`
- `completed`
- `escalated`
- `failed`

Local file-backed mapping for the first implementation:

- `runtime/agent_definitions/<id>.json`
- `runtime/agent_queue_items/<id>.json`
- `runtime/agent_runs/<id>.json`
- `runtime/agent_output_artifacts/<id>.json`

Execution sequence for the first local contract:

1. Load `agent_queue_items/<id>.json`
2. Verify ownership by the target agent
3. Mark the item `running`
4. Execute deterministic tools against `payload_json`
5. Write an `agent_runs` record
6. Write an `agent_output_artifacts` record
7. Write the agent-specific durable output record
8. Update the queue item with final status, confidence, escalation state, and completion time

Interpretation rules:

- `completed` means the queue item was processed and produced a durable output, even if the validation decision is `fail`
- `escalated` means the item needs human or coordinating-agent follow-up rather than a conclusive automated result
- `failed` is reserved for runtime or contract failures, not for normal validation defects

## `Vlad` first concrete queue contract

`Vlad` uses `agent_queue_items.payload_json` as the executable validation payload.

Required payload fields:

- `asset_path`
- `expected_checks`

Recommended payload fields:

- `compare_root`
- `submitted_by`
- `submission_context`
- `intake_record_id`
- `attachment_id`
- `attachment_ids`
- `raw_object_key`
- `submission_batch_id`

Allowed `expected_checks` values in the first slice:

- `integrity`
- `svg_parse`
- `accessibility`
- `geometry`
- `duplicates`
- `raster_sheet_analysis`

`Vlad` writes the following durable output families:

- `agent_runs`
- `agent_output_artifacts` with `artifact_type = validation_report`
- additional Phase 1 raster artifact types:
  - `split_plan`
  - `derivative_manifest`
- `validation_reports`

Phase 1 PNG split extension:

- when `asset_format == png`, `Vlad` may now:
  - infer candidate symbol regions deterministically
  - build a `split_plan` artifact with padded bounding boxes
  - create proposed child crop PNGs and a `derivative_manifest`
  - escalate to human review instead of silently finalizing a split
- multi-symbol raster sheets should currently escalate into Workspace review with proposed child crops preserved as candidate derivatives
- ambiguous raster sheets should currently create review-first artifacts rather than child crops only
- derivative child crops can now be mirrored into `attachments` linked to the `validation_report`
- raster split escalations can now create `review_cases`
- `control_exceptions` are available for rule-based raster anomalies when the deterministic path cannot classify the sheet cleanly

`validation_reports` fields for the first slice:

- `id`
- `queue_item_id`
- `source_type`
- `source_id`
- `validation_status`
- `defect_count`
- `normalized_payload_json`
- `report_json`
- `created_at`

Validation decision mapping:

- `pass` -> queue item status `completed`
- `fail` -> queue item status `completed`
- `escalate` -> queue item status `escalated`

Initial `Vlad` rule-code families:

- `VLAD-INTEGRITY-*`
- `VLAD-SVG-*`
- `VLAD-A11Y-*`
- `VLAD-GEOM-*`
- `VLAD-DUPE-*`
- `VLAD-RASTER-*`
- `VLAD-TASK-*`

The first local implementation is intentionally queue-shaped and evidence-heavy, but still file-backed. This keeps the contract aligned with the future backend while letting the `Vlad` OpenClaw workspace execute real queue items immediately.

## `Scott` first concrete queue contract

`Scott` is the intake boundary for raw candidate material before technical validation or provenance review begins.

`Scott` uses `agent_queue_items.payload_json` as the executable intake payload.

Required payload fields:

- `submission_kind`
- `source_ref`
- `submitted_by`
- `raw_input_path`

Recommended payload fields:

- `declared_format`
- `candidate_symbol_id`
- `candidate_title`
- `contributor_name`
- `contributor_org`
- `contributor_declaration`
- `source_notes`
- `import_batch_id`

Allowed `submission_kind` values in the first slice:

- `single_upload`
- `contributor_submission`
- `imported_symbol_library`

Initial deterministic checks for `Scott`:

- required payload presence
- raw input path existence
- file extension and basic format recognition
- file size and empty-file detection
- raster intake recognition for PNG submissions
- simple metadata extraction from filename and payload
- eligibility screening for supported formats and minimum declaration completeness

`Scott` writes the following durable output families:

- `agent_runs`
- `agent_output_artifacts` with `artifact_type = intake_record`
- `intake_records`

`intake_records` fields for the first slice:

- `id`
- `queue_item_id`
- `source_type`
- `source_ref`
- `submitter`
- `submission_kind`
- `intake_status`
- `eligibility_status`
- `normalized_submission_json`
- `routing_recommendation_json`
- `report_json`
- `created_at`

Allowed `intake_status` values for the first slice:

- `accepted`
- `rejected`
- `escalated`

Allowed `eligibility_status` values for the first slice:

- `eligible`
- `ineligible`
- `needs_review`

`routing_recommendation_json` should include:

- `route_to_agents`
- `next_queue_families`
- `priority`
- `reason_codes`
- `human_follow_up_required`

Initial `Scott` routing rules:

- `accepted` plus `eligible` -> recommend `Vlad` and `Tracy`
- `accepted` plus `needs_review` -> escalate to `human_reviewer`
- `rejected` plus `ineligible` -> stop automated routing
- malformed or unsupported intake payloads -> `escalated` or `rejected` depending on whether the defect is recoverable

Queue status mapping:

- `accepted` -> queue item status `completed`
- `rejected` -> queue item status `completed`
- `escalated` -> queue item status `escalated`

Initial `Scott` rule-code families:

- `SCOTT-TASK-*`
- `SCOTT-INTEGRITY-*`
- `SCOTT-FORMAT-*`
- `SCOTT-META-*`
- `SCOTT-ELIG-*`
- `SCOTT-ROUTE-*`

The first local `Scott` output artifact should contain:

- `decision`
- `confidence`
- `escalation_target`
- `normalized_submission`
- `extracted_metadata`
- `eligibility_flags`
- `routing_recommendation`
- `defects`
- `evidence_trace`

`Scott` should not decide publication, rights clearance, or technical validity. Its job is to normalize intake, reject obviously bad inputs, and produce a stable accepted record for downstream queues.

## `Tracy` first concrete queue contract

`Tracy` consumes accepted intake outputs and evaluates source lineage, ownership confidence, and publication-rights risk.

`Tracy` uses `agent_queue_items.payload_json` as the executable provenance payload.

Required payload fields:

- `intake_record_id`
- `source_ref`
- `submitted_by`
- `contributor_declaration`

Recommended payload fields:

- `candidate_symbol_id`
- `candidate_title`
- `contributor_name`
- `contributor_org`
- `rights_documents`
- `standards_source_refs`
- `evidence_links`
- `prior_assessment_refs`

Initial deterministic checks for `Tracy`:

- required declaration presence
- source-reference presence
- evidence-link formatting and file existence where local paths are provided
- duplicate or contradictory declaration detection
- standards-reference capture for reviewer follow-up

`Tracy` writes the following durable output families:

- `agent_runs`
- `agent_output_artifacts` with `artifact_type = provenance_assessment`
- `provenance_assessments`

`provenance_assessments` fields for the first slice:

- `id`
- `queue_item_id`
- `intake_record_id`
- `rights_status`
- `risk_level`
- `confidence`
- `summary`
- `evidence_json`
- `report_json`
- `assessed_at`

Allowed `rights_status` values for the first slice:

- `cleared`
- `restricted`
- `unknown`
- `conflict`

Allowed `risk_level` values for the first slice:

- `low`
- `medium`
- `high`
- `critical`

The first local `Tracy` output artifact should contain:

- `decision`
- `confidence`
- `escalation_target`
- `rights_status`
- `risk_level`
- `reviewer_summary`
- `evidence`
- `recommended_actions`
- `defects`
- `evidence_trace`

Initial `Tracy` decision rules:

- `rights_status = cleared` and `risk_level` up to `medium` -> `decision = pass`
- `rights_status = restricted` or `conflict` -> `decision = fail`
- `rights_status = unknown`, low confidence, or policy ambiguity -> `decision = escalate`

Queue status mapping:

- `pass` -> queue item status `completed`
- `fail` -> queue item status `completed`
- `escalate` -> queue item status `escalated`

Initial `Tracy` rule-code families:

- `TRACY-TASK-*`
- `TRACY-DECL-*`
- `TRACY-SOURCE-*`
- `TRACY-RIGHTS-*`
- `TRACY-EVID-*`
- `TRACY-POLICY-*`

Escalation rules for the first slice:

- missing contributor declaration with otherwise plausible intake -> escalate to `human_reviewer`
- conflicting ownership evidence -> escalate to `human_reviewer`
- explicit licensing restriction incompatible with publication -> fail with reviewer-visible evidence
- policy-sensitive exceptions must never be auto-cleared

`Tracy` must not override licensing conflicts, invent rights evidence, or silently convert ambiguous ownership into a clear pass. Human review remains the authority for exceptions and unclear rights outcomes.

## Initial queue handoff rules across `Scott`, `Vlad`, and `Tracy`

For the first local implementation:

- raw submissions enter `Scott` first
- only `Scott` outputs with `intake_status = accepted` and `eligibility_status = eligible` can enqueue `Vlad` and `Tracy`
- `Vlad` and `Tracy` may run in parallel once `Scott` has produced a stable accepted intake record
- `Scott` outputs with `needs_review`, `rejected`, or malformed contracts do not auto-enqueue downstream agents
- `Tracy` escalations and `Vlad` escalations remain human-routed until `Daisy` exists

This preserves a clean operating boundary:

- `Scott` decides intake normalization and downstream eligibility
- `Vlad` decides technical validation outcome
- `Tracy` decides provenance and rights outcome
- `Daisy` is deferred until these outputs exist as stable records
