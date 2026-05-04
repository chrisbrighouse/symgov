# symgov Review Decision and Orchestration

Last updated: 2026-05-03

## Purpose

This document defines the implemented review-outcome loop for Daisy-coordinated human review in SymGov.

The problem it solves is simple:

- human reviewers need to express explicit decisions in a durable, auditable way
- Daisy needs to coordinate review without becoming the final authority
- Libby needs to own every non-approval follow-up outcome
- Vlad needs to own physical symbol graphic changes requested by review
- the system needs a reliable loop from review to rework and back to review, ending only when an item is approved and sent to Rupert

This document is intended as the pickup point if the current session is lost.

## Core rule

Keep two layers separate:

- layer 1: human decision codes
- layer 2: system/agent action codes

Humans decide what should happen.
Daisy coordinates review. The backend records the decision and routes the follow-up.

Daisy must not approve, publish, withdraw, reject by itself, or replace human judgment.

Current routing rule:

- `approve` routes to Rupert for publication staging.
- every non-approval outcome routes to Libby.
- Libby either handles metadata/source/classification/disposition follow-up and sends the item back to Daisy for review, or sends physical symbol graphic changes to Vlad.
- Vlad returns graphic-change results to Libby.
- Libby combines Vlad results with any other updates and sends the item back to Daisy for another review pass.
- items may cycle between Daisy review and Libby follow-up multiple times before approval.

## Current implementation baseline

Current grounded review stages in the active implementation:

- `raster_split_review`
- `provenance_review`
- `classification_review`
- `review_pending_assignment`
- `ready_for_human_decision`

Current Daisy behavior in the scaffold:

- proposes reviewer assignments
- proposes stage transitions
- proposes contributor evidence requests
- does not write final human decisions

Current backend status:

- `review_cases` currently holds only source linkage, current stage, owner, escalation level, and open/closed timestamps
- migration `20260426_0004_human_review_decisions.py` adds durable `human_review_decisions`
- the same migration adds durable `review_case_actions`
- migration `20260503_0005_review_split_items.py` adds durable `review_split_items` for raster split child-symbol review state
- `POST /api/v1/workspace/review-cases/{id}/decisions` records SME decisions, creates deterministic follow-on actions, updates the case stage, and writes an audit event
- `POST /api/v1/workspace/review-cases/{id}/split-items/process-decisions` records child-level decisions for raster split cases, processes only non-pending children, and leaves undecided children open
- approval decisions create the Rupert publication handoff through `publication_handoff.py`
- non-approval decisions create a durable Libby follow-up action through `review_followup_handoff.py`
- Libby follow-up queue payloads include decision code, decision note, case comment, reviewer identity, source lineage, current classification context, and child decisions
- `GET /api/v1/workspace/review-cases` now includes the latest unsuperseded decision summary
- the live public API process still needs a restart or redeploy before the latest routing changes are active at `https://apps.chrisbrighouse.com/api/v1`

## Design goals

- preserve human authority at governance edges
- make review outcomes machine-readable
- keep audit trails clear about who decided and who coordinated
- support rework, evidence requests, and publication blocking without losing lineage
- support symbol-level review where cases fan out from source files or split sheets

## Implemented review loop

The current implementation uses the Reviews UI decision codes:

- `approve`
- `reject`
- `request_changes`
- `more_evidence`
- `rename_classify`
- `duplicate`
- `deleted`
- `defer`
- `child_actions_submitted`

Routing:

- `approve`
  - creates `prepare_publication_handoff`
  - targets `rupert`
  - immediately executes the Rupert handoff path in the backend
- all other decision codes
  - create `route_review_follow_up_to_libby`
  - target `libby`
  - create a DB-backed Libby queue item and matching local Libby runtime queue JSON
  - keep the review case open for follow-up rather than closing it immediately

Child decisions:

- non-split cases may still submit child decision details as part of one whole-case decision
- child action aliases `approved` and `rejected` are normalized to `approve` and `reject`
- when the whole-case decision is not `approve`, child-level actions are forced to Libby follow-up even if an individual child action is approved
- this prevents a non-approval whole-case decision from accidentally producing a Rupert-targeted child action

Raster split child-item decisions:

- raster split cases materialize Vlad `derivative_manifest.children` into `review_split_items`
- `GET /api/v1/workspace/review-cases` exposes only split items with `awaiting_decision` or `returned_for_review`
- the Reviews UI replaces the whole-case action panel with `Process Symbols` / `Process Selected Symbols` for raster split cases that have open children
- split review processing submits only the selected child-symbol decisions from the UI; case-level action, reviewer, whole-file comment, and decision-note controls are reserved for non-split reviews
- the split processing endpoint ignores pending children and rejects calls where no non-pending child decisions were provided
- each processed child gets its own `human_review_decision`, `review_case_action`, audit event, latest action/note/details, downstream agent, and downstream queue item reference
- approved children route to Rupert through `prepare_publication_handoff`
- every non-approval child action routes to Libby through `route_review_follow_up_to_libby`
- processed children move to `queued_rupert` or `queued_libby` and disappear from the open split workbench
- the parent raster split case moves to `split_children_processed` and closes only when no split items remain open
- current caveat: failed downstream child handoffs are not yet marked as `processing_failed`; after the handoff attempt the child is marked routed, so this needs a follow-up hardening pass

Libby follow-up types:

- `deletion_or_rejection`
- `duplicate_resolution`
- `metadata_or_classification_update`
- `evidence_request`
- `deferral`
- `graphic_change_triage`
- `review_follow_up`

Libby downstream routing:

- non-graphic follow-up writes `review_followup_reports` and queues Daisy for re-review
- graphic-change follow-up writes `review_followup_reports` and queues Vlad with `task_type: symbol_graphic_change_request`

Vlad downstream routing:

- Vlad processes `symbol_graphic_change_request`
- Vlad writes a graphic-change result artifact
- Vlad queues Libby with `task_type: vlad_graphic_update_completed`
- Vlad does not send modified graphics directly to Daisy or Rupert

Libby after Vlad:

- Libby checks the Vlad result
- Libby combines the graphic result with any metadata, classification, evidence, disposition, or child-symbol updates
- Libby queues Daisy for the next review pass

## Historical decision vocabulary

The earlier planning vocabulary below remains useful for conceptual governance design, but the live Reviews endpoint currently uses the implemented UI decision codes listed above.

## Layer 1: Human decision codes

Use these canonical decision codes:

- `approve`
- `approve_with_follow_up`
- `request_contributor_evidence`
- `request_internal_remediation`
- `split_case`
- `reject_current_submission`
- `block_publish_rights`
- `defer`

### Meaning of each code

`approve`
- the reviewed item is accepted for the next lifecycle step

`approve_with_follow_up`
- the reviewer accepts the direction, but small edits, cleanups, or metadata repairs still need to happen before the next irreversible step

`request_contributor_evidence`
- the reviewer needs more evidence from the submitter or source owner before the case can advance

`request_internal_remediation`
- the reviewer wants internal repair or rework from SymGov-side processing rather than more contributor input

`split_case`
- the reviewer concludes the case is too coarse and must be broken into narrower child review units

`reject_current_submission`
- the reviewed material should not advance in its current form

`block_publish_rights`
- the case is specifically blocked from publication because of rights, licensing, policy, or source restrictions

`defer`
- the case remains unresolved because some external dependency or workflow precondition is not yet satisfied

## Layer 2: Daisy action codes

Use these canonical orchestration action codes:

- `advance_stage`
- `assign_reviewers`
- `request_contributor_evidence`
- `requeue_agent`
- `create_child_cases`
- `hold_case`
- `close_as_rejected`
- `mark_publish_blocked`
- `park_case`
- `prepare_publication_handoff`

### Important rule

One human decision may produce several Daisy actions.

Examples:

- `request_internal_remediation`
  - `requeue_agent`
  - `hold_case`

- `approve_with_follow_up`
  - `hold_case`
  - `assign_reviewers` or assign owner
  - optional `requeue_agent`

- `approve`
  - `advance_stage`
  - optional `prepare_publication_handoff`

## Recommended review stages

Keep the current implementation stages and add the missing operational states:

- `raster_split_review`
- `provenance_review`
- `classification_review`
- `publish_readiness_review`
- `review_pending_assignment`
- `ready_for_human_decision`
- `awaiting_contributor_evidence`
- `awaiting_internal_remediation`
- `ready_for_publication_handoff`
- `rejected`
- `publish_blocked`
- `deferred`

## Stage transition baseline

### `raster_split_review`

Allowed human decisions:

- `approve`
- `approve_with_follow_up`
- `request_internal_remediation`
- `split_case`
- `reject_current_submission`
- `defer`

Recommended transitions:

- `approve`
  - usually to `classification_review`
  - optionally to `provenance_review` if the split result is already normalized enough for rights review
- `approve_with_follow_up`
  - to `awaiting_internal_remediation`
- `request_internal_remediation`
  - to `awaiting_internal_remediation`
- `split_case`
  - create symbol-level child cases
  - parent case becomes a coordination shell or closes as superseded by child cases
- `reject_current_submission`
  - to `rejected`
- `defer`
  - to `deferred`

### `provenance_review`

Allowed human decisions:

- `approve`
- `approve_with_follow_up`
- `request_contributor_evidence`
- `request_internal_remediation`
- `reject_current_submission`
- `block_publish_rights`
- `defer`

Recommended transitions:

- `approve`
  - to `classification_review`
  - or directly to `publish_readiness_review` if classification is already current and sufficient
- `approve_with_follow_up`
  - to `awaiting_internal_remediation`
- `request_contributor_evidence`
  - to `awaiting_contributor_evidence`
- `request_internal_remediation`
  - to `awaiting_internal_remediation`
- `reject_current_submission`
  - to `rejected`
- `block_publish_rights`
  - to `publish_blocked`
- `defer`
  - to `deferred`

### `classification_review`

Allowed human decisions:

- `approve`
- `approve_with_follow_up`
- `request_contributor_evidence`
- `request_internal_remediation`
- `split_case`
- `reject_current_submission`
- `block_publish_rights`
- `defer`

Recommended transitions:

- `approve`
  - to `publish_readiness_review`
- `approve_with_follow_up`
  - to `awaiting_internal_remediation`
- `request_contributor_evidence`
  - to `awaiting_contributor_evidence`
- `request_internal_remediation`
  - to `awaiting_internal_remediation`
- `split_case`
  - create narrower symbol-level child review units where file-level review is still too coarse
- `reject_current_submission`
  - to `rejected`
- `block_publish_rights`
  - to `publish_blocked`
- `defer`
  - to `deferred`

### `publish_readiness_review`

Allowed human decisions:

- `approve`
- `approve_with_follow_up`
- `request_internal_remediation`
- `reject_current_submission`
- `block_publish_rights`
- `defer`

Recommended transitions:

- `approve`
  - to `ready_for_publication_handoff`
- `approve_with_follow_up`
  - to `awaiting_internal_remediation`
- `request_internal_remediation`
  - to `awaiting_internal_remediation`
- `reject_current_submission`
  - to `rejected`
- `block_publish_rights`
  - to `publish_blocked`
- `defer`
  - to `deferred`

## Human decision to Daisy action mapping

Implementation note as of 2026-05-03:

- this section is the conceptual Daisy orchestration vocabulary
- the live backend now stores review follow-up as `review_case_actions`
- `approve` is the only Rupert-targeting path
- every non-approval path is routed through Libby before it returns to Daisy for another review pass
- physical symbol graphic changes are routed Libby -> Vlad -> Libby -> Daisy

### `approve`

Expected Daisy actions:

- `advance_stage`
- optionally `prepare_publication_handoff`

### `approve_with_follow_up`

Expected Daisy actions:

- `hold_case`
- assign owner or reviewers
- optional `requeue_agent`

### `request_contributor_evidence`

Expected Daisy actions:

- `request_contributor_evidence`
- `hold_case`

### `request_internal_remediation`

Expected Daisy actions:

- `requeue_agent`
- `hold_case`
- optional `assign_reviewers`

### `split_case`

Expected Daisy actions:

- `create_child_cases`
- `hold_case`

### `reject_current_submission`

Expected Daisy actions:

- `close_as_rejected`

### `block_publish_rights`

Expected Daisy actions:

- `mark_publish_blocked`

### `defer`

Expected Daisy actions:

- `park_case`

## Persistence model

### Keep `review_cases` as the workflow anchor

Recommended additions to `review_cases`:

- `status text not null`
  - `open`
  - `waiting`
  - `ready`
  - `closed`
- `resolution_code text null`
  - `ready_for_publication_handoff`
  - `rejected`
  - `publish_blocked`
  - `superseded`
- `waiting_reason_code text null`
  - `contributor_evidence`
  - `internal_remediation`
  - `review_assignment`
  - `external_dependency`
  - `policy_hold`
- `parent_review_case_id uuid null`

Optional denormalized links if lookup speed becomes important:

- `related_intake_record_id`
- `related_validation_report_id`
- `related_provenance_assessment_id`
- `related_classification_record_id`

### Add `human_review_decisions`

Recommended shape:

- `id uuid primary key`
- `review_case_id uuid not null`
- `decision_code text not null`
- `decision_summary text null`
- `decision_note text null`
- `decided_by uuid not null`
- `decider_role text not null`
- `from_stage text not null`
- `to_stage text null`
- `decision_payload_json jsonb not null default '{}'`
- `created_at timestamptz not null`
- `superseded_at timestamptz null`

Recommended `decision_payload_json` uses:

- approved child ids
- rejected child ids
- contributor evidence request details
- requested agent remediation details
- requested metadata changes
- publication notes
- rights override reason
- defer reason

### Add `review_case_actions`

Recommended shape:

- `id uuid primary key`
- `review_case_id uuid not null`
- `decision_id uuid null`
- `action_code text not null`
- `action_status text not null`
- `assigned_to uuid null`
- `target_agent_slug text null`
- `target_stage text null`
- `action_payload_json jsonb not null default '{}'`
- `created_by_type text not null`
  - `daisy`
  - `system`
  - `human`
- `created_by_id uuid null`
- `created_at timestamptz not null`
- `started_at timestamptz null`
- `completed_at timestamptz null`

Recommended `action_status` values:

- `pending`
- `in_progress`
- `completed`
- `cancelled`
- `blocked`

## API proposal

### First write endpoint

`POST /api/v1/workspace/review-cases/{id}/decisions`

Request:

- `decisionCode`
- `decisionSummary`
- `decisionNote`
- `decisionPayload`

Response:

- created decision record
- updated review case stage and status
- generated `review_case_actions`

### Read-model additions

Extend `GET /api/v1/workspace/review-cases` to include:

- `caseStatus`
- `waitingReason`
- `resolutionCode`
- latest human decision summary
- pending action counts

Add `GET /api/v1/workspace/review-cases/{id}` to include:

- decision history
- Daisy/system action history
- split parent/child relationships
- publication readiness checklist state

### Daisy read-model additions

Extend `GET /api/v1/workspace/daisy/reports` to optionally include:

- `recommendedActionCodes`
- `recommendedWaitingReason`
- `recommendedResolutionCode`

Important rule:

- Daisy may create or update orchestration actions
- Daisy may not write final human decision records

## First implementation cut

Status as of 2026-05-03:

- database schemas for `human_review_decisions` and `review_case_actions` are implemented and migrated
- ORM models and Pydantic schemas are implemented
- `POST /api/v1/workspace/review-cases/{id}/decisions` is implemented in source
- decision actions currently cover the SME actions agreed for the first Reviews UI: approve, reject, request changes, request more evidence, rename/classify, mark duplicate, delete proposed child, defer, and child-action submission
- `approve` creates and executes the Rupert publication handoff
- every non-approval decision creates a Libby follow-up handoff with the full review response and child-decision details
- Libby review follow-up can queue Daisy directly for re-review or queue Vlad for graphic changes
- Vlad graphic-change results return to Libby, and Libby then queues Daisy for re-review
- Reviews frontend filters and the decision panel are implemented and published to the static route
- live API reload remains the deployment boundary before the latest routing behavior can be verified from the public browser UI

Do not implement the whole state machine at once.

Start with:

- review surfaces:
  - `provenance_review`
  - `classification_review`
- human decision codes:
  - `approve`
  - `request_contributor_evidence`
  - `request_internal_remediation`
  - `reject_current_submission`
  - `block_publish_rights`
- action codes:
  - `advance_stage`
  - `request_contributor_evidence`
  - `requeue_agent`
  - `close_as_rejected`
  - `mark_publish_blocked`

This is enough to validate the human decision pipeline without introducing publication handoff or split-case fan-out yet.

## Recommended implementation sequence

1. Add database schema for `human_review_decisions`. Done in migration `20260426_0004_human_review_decisions.py`.
2. Add database schema for `review_case_actions`. Done in migration `20260426_0004_human_review_decisions.py`.
3. Add review-case status, waiting, and resolution fields. Deferred; current first cut uses `review_cases.current_stage`, `closed_at`, latest decision summary, and action rows.
4. Implement `POST /api/v1/workspace/review-cases/{id}/decisions`. Done in source, pending live API process reload.
5. Generate deterministic follow-on action rows from the decision code. Done for the first SME decision set.
6. Extend Workspace review APIs to show decision and action state. Partly done through `latestDecision` on review-case list responses; full detail/history remains pending.
7. Extend Daisy reports to summarize pending action state instead of only pre-decision suggestions. Pending.
8. Add `publish_readiness_review` and publication-handoff preparation later. Partly done through direct Rupert handoff on approval.
9. Add Libby non-approval follow-up handoff. Done in `review_followup_handoff.py`.
10. Add Libby -> Vlad -> Libby -> Daisy graphic-change loop. First file-backed runner slice done.

## Audit rule

Always store both:

- the explicit human decision code
- the follow-on system action bundle

That separation is the key audit requirement.

Without it, the system will blur human judgment and automation, which is exactly what SymGov should avoid.
