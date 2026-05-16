# symgov Agent Architecture

Last updated: 2026-04-26

## Purpose

This document defines how `symgov` should introduce independent specialist agents into the product and operating model.

The design direction for this pass is:

- Agents should be independent as much as possible.
- Each agent should own its own queue and produce explicit outputs.
- The default low-cost model path should use the local Ollama provider with `ollama/gemma4:e4b`.
- Human review remains the final authority for high-risk governance and publication actions, but low-risk symbols may bypass human review when Libby explicitly records that review is not required and the validation, provenance, classification, and release evidence is complete.

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
- the current bootstrap and inspection entrypoint is `/data/.openclaw/workspace/symgov/backend/manage_symgov.py`

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
- `Ed` documentation and policy (also oversees UI changes to provide the best user experience)

Current implementation status is now mixed rather than empty:

- `Scott`, `Vlad`, and `Tracy` exist as live local/file-backed runners with PostgreSQL write-through support
- `Daisy` now exists as the first review-coordination scaffold and can be created automatically from persisted `review_cases`
- `Libby` now exists as the classification-and-research scaffold between `Tracy` and `Daisy`
- `Rupert` now exists as the first publishing and release-management scaffold after either human approval or an auditable Libby no-human-review-required handoff
- the remaining named agents still exist only as planning targets

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
- queue status should describe the agent's responsibility, not the whole downstream lifecycle. For example, Vlad completes a Libby-routed graphic-change queue item once the changed image result has been returned to Libby, and Daisy completes human-review escalation work once the review request has been created/escalated.
- agent runs should keep useful `tool_trace_json` because Workspace monitor cards can summarize Vlad's process/tool usage from that trace. Current UI labels include `Tess` for Tesseract/OCR, `Nano` for Nano Banana/Gemini image-edit fallback, and conversion/split labels such as `DXF to SVG`, `Format conversion`, `Raster split`, and `Raster candidate`.

### 2. Durable outputs

Each agent should write structured outputs, not just chat text.

Examples:

- `Scott` writes intake records, extracted metadata, routing flags
- `Tracy` writes provenance reports, rights status, evidence notes
- `Vlad` writes validation reports, defect lists, normalized technical metadata
- `Libby` writes taxonomy assignments, aliases, search terms, source classifications, and source-reference evidence
- `Daisy` writes reviewer assignment proposals, stage-transition proposals, and contributor evidence requests against existing `review_cases`
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

Low-risk publication handoff is allowed only when `Libby` has classified the single-symbol item, recorded Category and Discipline where ascertainable, found no unresolved validation/provenance/classification block, and explicitly marked human review as not required. That handoff is a publication-readiness recommendation, not authority to override Rupert release checks.

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
  - id, symbol_id or intake_record_id, classification_status, discipline, format, industry, symbol_family, process_category, parent_equipment_class, standards_source, library_provenance_class, source_classification, aliases_json, search_terms_json, source_refs_json, evidence_json, libby_approved, confidence, supersedes_classification_id, created_at

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
- split-processing queue for submitted symbol sheets
- file integrity checks
- format checks
- geometry and rule checks
- duplicate detection
- process/tool trace reporting for Workspace visibility, including OCR, image-edit fallback, raster analysis, and future format-conversion tools such as DXF-to-SVG conversion

Inputs:

- accepted intake records
- draft symbol revisions
- Libby-routed physical symbol graphic change requests

Outputs:

- `validation_reports`
- normalized technical metadata
- pass or fail recommendation
- for raster files in Phase 1:
  - `split_plan` artifacts
  - `single_symbol_raster_candidate` artifacts for one-symbol files
  - `derivative_manifest` artifacts
  - proposed child crop assets
- technical split output that creates single-symbol child candidates for Libby classification
- returned graphic-change results for Libby consolidation

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
- provenance evidence for Libby's classification and human-review-required decision

Why in wave 1:

- highly important before publication

### `Libby` - classification and research librarian

Owns:

- classification queue
- publication-readiness triage for classified single-symbol items
- non-approval review follow-up queue
- taxonomy assignment
- discoverability metadata
- alias and keyword generation
- source-reference repair when provenance/source metadata is missing or weak
- taxonomy term creation when an existing controlled term is insufficient
- consolidation of reviewer comments, child-symbol decisions, metadata updates, and Vlad graphic-change results before Daisy re-review
- flexible queue-item handling for either one case or a batch of child/case follow-up items

Inputs:

- accepted intake records
- validation reports, single-symbol raster candidates, and raster split child metadata
- provenance assessments
- active review cases when classification needs a retry or upgrade
- Daisy-organized human review decisions that are not approvals
- Vlad graphic-change results returning for consolidation
- multi-item queue payloads using `payload_json.items` or `payload_json.cases`

Outputs:

- `classification_records`
- `review_followup_reports`
- publication-readiness handoffs to Rupert when human review is not required
- parent `libby_batch_report` output artifacts for multi-item queue work
- Category, Discipline, format, industry, symbol family, process category, and parent equipment class
- standards source and library provenance class
- aliases and search terms
- source classification and supporting reference evidence
- `libby_approved` readiness signal
- explicit `human_review_required` and review rationale
- downstream Daisy queue items for first review or re-review when human review is required
- downstream Vlad queue items when a physical symbol graphic change is requested
- downstream Rupert queue items when publication-ready and human review is not required
- queue status summaries for read-only chat/status checks

Agreed operating rules:

- Libby runs after `Tracy` and after Vlad split/validation output is available, before Daisy is asked to coordinate any human review
- Libby classifies at both file and symbol level, but symbol-level cases are the main review unit
- every submitted single symbol and every child symbol extracted from a sheet must pass through Libby before Daisy or Rupert
- each symbol-level output must retain lineage back to the originating source file and batch
- Libby may browse externally whenever needed to resolve classification uncertainty
- Libby may create new taxonomy terms and use them immediately
- Libby may supersede existing classifications, but earlier records must remain durable and be marked obsolete
- Libby does not override `Tracy` rights decisions or `Vlad` technical findings
- Libby owns all non-approval outcomes from Daisy-organized review
- Libby must send physical symbol graphic changes to `Vlad`
- Libby must check Vlad graphic-change results and combine them with any metadata/source/classification updates before either Daisy re-review or Rupert handoff
- Libby may send items to `Rupert` only through a structured no-human-review-required publication-readiness handoff with validation, provenance, and classification evidence
- Libby may prepare audited metadata/source/classification/disposition instructions, but durable write/delete mutations must go through Symgov-controlled backend helpers
- explicit non-graphic follow-up types route back to Daisy even if the reviewer text mentions graphics in a negative or contextual phrase
- Telegram remains orchestrated by Alfi/main; Libby is not directly bound to `telegram:7643191699`. Any future direct Libby Telegram binding should start read-only and mutation commands would require explicit authorization and audit.

Review-flow implication:

- the intended case progression becomes:
  - technical validation / raster split
  - provenance review
  - classification review
  - Libby decision: human review required or not required
  - if review is required, Daisy-managed human review coordination
  - if review is not required, Rupert-managed publication staging from Libby's auditable publication-readiness handoff
  - if Daisy review is approved, Rupert-managed publication staging after explicit human approval
  - if not approved, Libby follow-up
  - optional Vlad graphic-change work
  - Libby consolidation
  - Daisy re-review when human review is still required, or Rupert handoff when no review is required
  - repeat until approval or explicit human disposition

### `Rupert` - publication and release management agent

Owns:

- publication queue
- release-area staging
- publication pack proposals
- published page proposals
- pack entry proposals
- Standards availability summaries

Inputs:

- explicit human approval handoff from a Daisy-coordinated review
- Libby no-human-review-required publication-readiness handoff
- approved symbol revision IDs
- release target
- effective date and Standards visibility
- optional publication pack metadata

Outputs:

- `publication_reports`
- release-area manifest records
- publication pack metadata
- published page proposals
- pack entry proposals
- Standards availability summary

Agreed operating rules:

- Rupert runs only after explicit human approval or an explicit Libby no-human-review-required handoff
- Rupert must not publish or stage items with missing human approval or Libby no-review evidence, missing symbol revisions, or unresolved release target ambiguity
- Rupert must not override rights, classification, technical, policy, or review blocks
- Rupert is reached from an explicit approved human review decision or from Libby's structured no-human-review-required handoff; Rupert is never reached from Vlad directly
- the first implementation stages release intent into a local release area and report artifact; durable database publication mutations can follow once final release authority is defined

## Current bootstrap and usage notes

- Seed baseline agent rows with:
  - `python /data/.openclaw/workspace/symgov/backend/manage_symgov.py seed-agent-definitions`
- Inspect the live database with:
  - `python /data/.openclaw/workspace/symgov/backend/manage_symgov.py check-db`
- Inspect local MinIO with:
  - `python /data/.openclaw/workspace/symgov/backend/manage_symgov.py check-storage`
- Mirror a local runner execution into PostgreSQL with:
  - `python /data/.openclaw/workspaces/scott/run_scott_intake.py --queue-item ... --runtime-root ... --persist-db`
  - `python /data/.openclaw/workspaces/vlad/run_vlad_validation.py --queue-item ... --runtime-root ... --persist-db`
  - `python /data/.openclaw/workspaces/tracy/run_tracy_provenance.py --queue-item ... --runtime-root ... --persist-db`
- Run local scaffold-only agents with:
  - `python /data/.openclaw/workspaces/daisy/run_daisy_coordination.py --queue-item ... --runtime-root ...`
  - `python /data/.openclaw/workspaces/rupert/run_rupert_publication.py --queue-item ... --runtime-root ...`

## Current verified live state

- `agent_definitions` contains seed definitions for `scott`, `vlad`, `tracy`, `daisy`, `libby`, and `rupert`
- `Scott` has been verified writing `agent_queue_items`, `agent_runs`, `agent_output_artifacts`, and `intake_records`
- `Vlad` has been verified writing `agent_queue_items`, `agent_runs`, `agent_output_artifacts`, and `validation_reports`
- `Vlad` Phase 1 raster split persistence has now also been verified for:
  - additional `agent_output_artifacts`
  - derivative child `attachments`
  - `review_cases`
- `Tracy` is wired for the same bridge path and now creates persisted `review_cases` when provenance follow-up is required
- `Daisy` now:
  - accepts `review_case` coordination queue items
  - writes local `review_coordination_reports`
  - can be auto-created from `Vlad` or `Tracy` review-case outputs on the live external submission path
  - is queryable through the backend Workspace API and visible in the Workspace UI as a read-only coordination lane
  - marks Libby follow-up/human-review escalation queue items `completed` once the human review request has been created/escalated, with related Daisy queue completions mirrored into PostgreSQL by the backend queue bridge
- `Libby` now:
  - accepts DB-backed and local runtime queue items for non-approval human review follow-up
  - is the required classification and Category/Discipline pass for submitted single symbols and child symbols extracted from sheets
  - decides whether human review is required before routing to Daisy or Rupert
  - writes local `review_followup_reports`
  - routes non-graphic follow-up back to Daisy for re-review
  - routes graphic-change follow-up to Vlad
- `Vlad` now:
  - accepts Libby-routed `symbol_graphic_change_request` queue items
  - writes graphic-change result artifacts
  - returns graphic-change results to Libby for consolidation before Daisy re-review
  - marks `symbol_graphic_change_request` queue items `completed` after returning the graphic-change result to Libby
- `Rupert` now:
  - accepts publication handoff queue items after human approval or a Libby no-human-review-required readiness handoff
  - writes local `publication_reports`
  - writes release-area manifests for staged symbol revisions
  - can persist durable publication records with `--persist-db`, writing publication jobs, packs, published pages, pack entries, audit events, and published symbol lifecycle updates
  - causes Workspace Rupert cards to show `PUBLISHED` and link to the Standards record only after the queued symbol revision has a public published page
  - is registered in the SymGov-owned OpenClaw manifest and backend agent seed list
- independent output contract
- manageable even before advanced orchestration exists

### `Ed` - visual experience and help agent (also oversees UI changes to provide the best user experience)

Owns:

- Standards View visual experience
- admin Workspace visual experience for processing visibility
- SME Reviews visual experience for Daisy-coordinated review cases
- public help and in-product guidance
- empty, loading, error, and not-found states
- user-facing copy, layout, navigation, and frontend verification
- API response-shape feedback from a visual/user-experience perspective
- overseeing UI changes to provide the best user experience
- collecting and summarizing interface feedback sent to the existing SymGov Ops Telegram group

Does not own:

- publication table writes
- human approval authority
- provenance, rights, classification, or publication policy decisions
- Rupert release-management actions
- active feedback prompting; Ed currently only summarizes feedback that users send

Current implementation status:

- Ed has a local OpenClaw workspace at `/data/.openclaw/workspaces/ed`
- Ed is registered in the SymGov-owned OpenClaw manifest and backend agent seed list with queue family `ux_feedback`
- Ed writes local `ux_feedback_reports`, `interface_review_reports`, and `design_recommendations` artifacts
- Ed feedback remains artifact-backed in phase 1; no durable Symgov feedback table is required yet

Rupert should produce structured publication summaries, page metadata, and pack context that Ed can render. Ed (also oversees UI changes to provide the best user experience) may propose UI grouping such as source-file pack grouping, discipline grouping, or search facets, but those should consume published APIs rather than mutate publication data.

### `Daisy` - review coordination agent

Owns:

- case coordination queue
- assignment suggestions
- stage movement proposals
- exception routing

Inputs:

- Libby-routed review-required cases with validation, provenance, classification, Category, and Discipline context
- clarification links
- change request state

Outputs:

- reviewer assignment proposals
- stage movement recommendations
- contributor evidence requests
- human-review queue state for cases Libby or another upstream control requires

Why after wave 1:

- depends on upstream Libby review-required decisions and supporting outputs
- better once intake, validation, and provenance contracts exist

## Wave 2 agents

- `Libby` for classification and discoverability
- `Rupert` for publication jobs and release control
- `Reggie` for compliance monitoring and control rules

## Wave 3 agents

- `David` for catalogue curation
- `Whitney` for market intelligence
- `Ed` for documentation and policy maintenance (also oversees UI changes to provide the best user experience)

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
6. Add `Rupert` to stage approved symbols for release and Standards publication.

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
- the current SymGov agent workspace set also includes `/data/.openclaw/workspaces/libby` and `/data/.openclaw/workspaces/rupert`
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
- `Libby` as the required classification and review-required decision point for all single-symbol candidates
- `Rupert` as the first publication staging agent after Daisy-managed human approval or Libby no-human-review-required handoff

## Deferred decisions

- whether agent queues live inside the main Symgov database only or also map to OpenClaw session state
- whether `Daisy` should be a true orchestrator agent or a workflow service plus human UI affordance
- whether `Rupert` should perform final durable publication automatically after human approval or Libby no-review handoff, or only stage for operator release
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
- `candidate_symbol_id`
- `candidate_title`
- `origin_file_name`
- `source_notes`
- `file_note`
- `submission_batch_summary`
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
  - `single_symbol_raster_candidate`
  - `derivative_manifest`
- `validation_reports`

Phase 1 raster analysis extension:

- when `asset_format == png`, or `asset_format == jpeg` with Pillow available for normalization, `Vlad` may now:
  - infer candidate symbol regions deterministically
  - build a `split_plan` artifact with padded bounding boxes
  - create a `single_symbol_raster_candidate` artifact for one-symbol raster files
  - create proposed child crop PNGs and a `derivative_manifest` for multi-symbol raster sheets
  - escalate to human review instead of silently finalizing a split
- single-symbol raster candidates preserve original filename, candidate title, aliases, keywords, description hints from file notes/batch notes, attachment IDs, object-storage key, image dimensions, candidate region, and recommended next agents
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
- `Vlad` split output and accepted single-symbol output flow to `Libby` as symbol-level candidates
- `Tracy` provenance output flows to `Libby` as source/right evidence for classification and review-required triage
- `Tracy` escalations and `Vlad` escalations can create a `Daisy` coordination queue item only when human review is required and a persisted `review_case` exists
- `Libby` routes no-human-review-required, publication-ready symbols to `Rupert`
- `Libby` routes human-review-required symbols to `Daisy`
- Daisy-coordinated human approvals can create a `Rupert` publication queue item for release staging
- every Daisy-coordinated non-approval outcome routes to `Libby`
- Libby may route graphic changes to `Vlad`, but Vlad returns results to Libby rather than Daisy or Rupert
- Libby sends revised items back to `Daisy` for another review pass when human review remains required, or to `Rupert` when the item is publication-ready without further review

This preserves a clean operating boundary:

- `Scott` decides intake normalization and downstream eligibility
- `Vlad` decides technical validation outcome
- `Tracy` decides provenance and rights outcome
- `Libby` decides classification, Category, Discipline, and whether human review is required
- `Libby` consumes non-approval review decisions, performs or coordinates follow-up, and returns revised items to Daisy only when human review remains required
- `Daisy` consumes stable human-review-required `review_cases` and proposes coordination actions without taking final approval authority
- `Rupert` stages/publishes only from explicit human approval or Libby's auditable no-human-review-required handoff
- `Vlad` consumes Libby-routed graphic-change requests and returns results to Libby
- `Rupert` consumes explicit human approval or Libby no-review handoffs and stages approved or cleared symbol revisions for publication without overriding release authority
