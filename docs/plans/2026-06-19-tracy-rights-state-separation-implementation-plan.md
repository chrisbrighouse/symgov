# Tracy rights-state separation implementation plan

> For Hermes: execute in small, test-first slices. Do not add more Tracy intelligence until this state model is in place.

Goal
- Remove ambiguous overloaded "escalate" semantics in Tracy by separating:
  1) processing progression (queue status / agent decision), and
  2) rights/provenance disposition (publication gating signal).

Why now
- Current Tracy uses `decision = escalate` for both:
  - non-blocking unknown provenance (should proceed to Libby/review visibility), and
  - true rights blockers (must stop publication and require explicit rights review).
- This makes operator interpretation, queue grouping, and publication policy harder than necessary.

Current state (verified in repo)
- Tracy maps `decision == escalate` -> queue status `escalated` in `scripts/run_tracy_provenance.py` (`queue_status_for_decision`).
- Provenance persistence already stores `rights_status` and `risk_level` in `provenance_assessments`.
- Libby already treats rights statuses differently (`cleared`, `restricted/conflict`, and other).
- Publication automation gate already checks `provenance.rights_status` and `provenance.risk_level`.

This means most data needed for separation exists; the gap is contract clarity and consistent usage.

---

## 1) Canonical state model

Define two independent fields in Tracy artifact contract:

1. `processing_outcome` (agent pipeline progression)
- allowed values (initial):
  - `pass`
  - `review_required`
  - `failed`

2. `rights_disposition` (publication gate semantics)
- allowed values:
  - `cleared`
  - `unknown_warning`
  - `restricted`
  - `conflict`
  - `failed`

Interpretation table:
- `cleared`: proceed normally.
- `unknown_warning`: proceed to classification/review visibility with warning retained; not a hard rights block by itself.
- `restricted`: explicit rights block; rights review required before publication.
- `conflict`: explicit rights block; rights review required before publication.
- `failed`: provenance evaluation failed; block publication until resolved/reprocessed.

Compatibility note
- Keep legacy `decision` and legacy `rights_status` temporarily as derived/aliased fields during migration.

---

## 2) Data/schema changes

Primary DB table: `provenance_assessments`

Current columns include:
- `rights_status` (text)
- `risk_level` (text)
- `report_json` (jsonb)

### Migration A (add new canonical columns)
Add nullable columns first, then backfill, then enforce not-null:
- `rights_disposition` text
- `processing_outcome` text

Suggested Alembic file
- `backend/alembic/versions/20260619_00xx_tracy_rights_state_separation.py`

Suggested SQL backfill mapping
- `rights_disposition`:
  - `rights_status='cleared'` -> `cleared`
  - `rights_status='unknown'` -> `unknown_warning`
  - `rights_status='restricted'` -> `restricted`
  - `rights_status='conflict'` -> `conflict`
  - else -> `failed`
- `processing_outcome`:
  - if `report_json->>'decision'='pass'` -> `pass`
  - if `rights_status in ('restricted','conflict')` -> `failed`
  - if `report_json->>'decision'='fail'` -> `failed`
  - else -> `review_required`

Then:
- set both columns `NOT NULL`
- add check constraints:
  - `rights_disposition in ('cleared','unknown_warning','restricted','conflict','failed')`
  - `processing_outcome in ('pass','review_required','failed')`

Future cleanup (separate migration)
- deprecate `rights_status` reads/writes after all consumers moved.

Model update
- file: `backend/symgov_backend/models/schema.py`
- class: `ProvenanceAssessment`
- add mapped fields for `rights_disposition`, `processing_outcome`.

---

## 3) Tracy artifact contract changes

File: `scripts/run_tracy_provenance.py`

### Replace overloaded semantics in `run_provenance_task`
Current behavior:
- ambiguous unknown often emits `decision='escalate'`, `rights_status='unknown'`.

Target behavior:
- always compute `rights_disposition` and `processing_outcome` directly.
- keep legacy fields during compatibility phase:
  - `decision` derived from `processing_outcome`
  - `rights_status` derived from `rights_disposition`

Decision derivation during compatibility:
- `processing_outcome='pass'` -> `decision='pass'`
- `processing_outcome='review_required'` -> `decision='escalate'`
- `processing_outcome='failed'` -> `decision='fail'`

Rights derivation during compatibility:
- `unknown_warning` -> legacy `rights_status='unknown'`
- others map 1:1 except `failed` -> `unknown` (or keep `failed` if downstream accepts it).

### Queue status mapping update
Function: `queue_status_for_decision`
- stop using legacy decision as the primary truth.
- new mapping:
  - `processing_outcome in ('pass','review_required')` -> queue status `completed`
  - `processing_outcome='failed'` -> queue status `failed`

Rationale:
- unknown warning should no longer produce operator-waiting `escalated` queue rows.
- rights blockers still surface as explicit review workflow via Daisy/review_case, not via ambiguous queue status.

### Review recommendation gating
Current:
- review recommendation for restricted/conflict/fail patterns.

Target:
- create rights review recommendation only when `rights_disposition in ('restricted','conflict','failed')`.
- do not create rights review recommendation for `unknown_warning`.

### Persist canonical fields
When writing `provenance_assessment` durable record, include:
- `rights_disposition`
- `processing_outcome`

And include both in artifact payload for API/UI consumers.

---

## 4) Runtime persistence bridge updates

File: `backend/symgov_backend/runtime.py`

In provenance upsert path (`durable_kind == 'provenance_assessment'`):
- write/read new durable keys into DB columns:
  - `rights_disposition`
  - `processing_outcome`
- keep legacy fallback logic while mixed-version workers may still send old keys.

Compatibility rule:
- if new fields missing in incoming payload, derive from legacy fields.

---

## 5) API and response contract updates

Primary surface: `backend/symgov_backend/routes/workspace.py`

Update provenance-related response payloads (workspace review items and queue items) to expose both:
- `rightsDisposition`
- `processingOutcome`

Keep compatibility keys for one release window:
- existing `rightsStatus`
- any legacy decision/status display fields

Schema updates
- `backend/symgov_backend/schemas.py`
- add new optional/required response fields where provenance is represented.

Operator-facing note strings (currently via `build_provenance_notes`) should reference `rights_disposition` labels and avoid the word "escalated" unless it truly means queue escalation.

---

## 6) Frontend behavior changes

File: `frontend/src/App.jsx`

Current risk:
- status badges and wording can still conflate "escalated" with rights blocking.

Target UI behavior:
- show two separate chips/fields on provenance/review cards:
  - Progress: from queue/review stage (`completed`, `running`, etc.)
  - Rights: `cleared | unknown_warning | restricted | conflict | failed`

Rules:
- `unknown_warning`: warning visual only; allow downstream card flow.
- `restricted/conflict/failed`: blocked visual; ensure routed to rights review lane.

Filter/queue affordances:
- add rights filter buckets:
  - warnings only (`unknown_warning`)
  - blockers (`restricted`,`conflict`,`failed`)

CSS/state naming
- keep `lane-escalated` only for true queue escalation semantics, not rights disposition rendering.

---

## 7) Agent handoff contract updates

### Tracy -> Libby
File: `scripts/run_tracy_provenance.py` (`build_libby_queue_item`)
- include canonical keys in payload:
  - `rights_disposition`
  - `processing_outcome`
- keep `rights_status` compatibility key temporarily.

### Libby behavior
File: `scripts/run_libby_classification.py`
- read canonical `rights_disposition` first, fallback to legacy `rights_status`.
- semantics:
  - `cleared`: normal confidence path
  - `unknown_warning`: proceed but preserve warning context/evidence
  - `restricted/conflict/failed`: classification can run for context, but publication remains blocked by gate/review

### Daisy coordination
- rights review coordination creation should key off `rights_disposition` blocker set.

---

## 8) Publication gate policy update

File: `backend/symgov_backend/automation_policy.py`

Current logic uses `provenance.rights_status` and `provenance.risk_level`.

Target:
- prefer `rights_disposition`:
  - allow only `cleared` and optionally `unknown_warning` (policy choice)
- recommended initial strictness:
  - automated publication allowlist = `{'cleared'}`
  - `unknown_warning` remains visible and routable but not auto-published unless policy explicitly changed.

Reason:
- respects your review comment: unknown should continue downstream, but publication should remain governed.

---

## 9) Queue reconciliation + status grouping

File: `backend/symgov_backend/agent_queue_reconciliation.py`

No large structural change needed if Tracy unknown warnings now end as `completed`.
But verify no logic assumes Tracy escalated == rights blocker.

Tests to adjust:
- `tests/test_agent_queue_state_machine.py`
- any tests asserting Tracy unknown outcomes are `escalated` queue status.

---

## 10) Test plan (required)

1) Tracy unit tests
- file: `tests/test_tracy_provenance_flow.py`
- add assertions:
  - ambiguous declaration -> `rights_disposition='unknown_warning'`, `processing_outcome='review_required'`, no rights review case
  - restricted/conflict -> blocker dispositions, rights review recommendation present
  - malformed/missing required fields -> `rights_disposition='failed'`, `processing_outcome='failed'`

2) Persistence tests
- verify DB writes/reads of new columns via runtime bridge.

3) Workspace API tests
- ensure response includes new keys and keeps compatibility keys.

4) Automation policy tests
- ensure gate blocks non-`cleared` dispositions (or whichever policy is configured).

5) Frontend tests
- add/adjust tests for rights lane/status display and filters.

---

## 11) Rollout plan (safe sequence)

Phase 1: additive backend
1. Add DB columns + backfill + model fields.
2. Update runtime persistence bridge to support both old/new payloads.
3. Ship API that returns both old and new fields.

Phase 2: Tracy/agent contract
4. Update Tracy to emit canonical fields (and compatibility fields).
5. Update Libby/Daisy to read canonical first.

Phase 3: UI/policy
6. Update frontend display/filter semantics.
7. Update automation gate to canonical field.

Phase 4: cleanup
8. Remove legacy field dependencies (`decision` overload, `rights_status` reads) after one stable release cycle.

---

## 12) Explicit mapping for operator guidance

Use this exact operator-facing matrix in docs/UI help:

- cleared
  - meaning: rights/provenance acceptable
  - flow: proceed normally
  - publication: allowed subject to other gates

- unknown_warning
  - meaning: provenance unclear, no explicit conflict/restriction
  - flow: proceed to Libby/review visibility with warning retained
  - publication: blocked from automatic release unless policy exception

- restricted
  - meaning: explicit rights restriction
  - flow: must go to provenance/rights review
  - publication: blocked

- conflict
  - meaning: contradictory rights signals
  - flow: must go to provenance/rights review
  - publication: blocked

- failed
  - meaning: provenance assessment failed (missing required data/system failure)
  - flow: requires remediation/reprocess
  - publication: blocked

---

## 13) Files expected to change

Backend
- `backend/alembic/versions/20260619_00xx_tracy_rights_state_separation.py` (new)
- `backend/symgov_backend/models/schema.py`
- `backend/symgov_backend/runtime.py`
- `backend/symgov_backend/routes/workspace.py`
- `backend/symgov_backend/schemas.py`
- `backend/symgov_backend/automation_policy.py`
- `backend/symgov_backend/agent_queue_reconciliation.py` (if needed)

Runners
- `scripts/run_tracy_provenance.py`
- `scripts/run_libby_classification.py`
- external mirrored workers if still active (`/data/.openclaw/workspaces/...`)

Frontend
- `frontend/src/App.jsx`
- `frontend/src/styles.css`

Tests
- `tests/test_tracy_provenance_flow.py`
- `tests/test_agent_queue_state_machine.py`
- any workspace/API/policy tests affected by response shape and gating semantics

---

## 14) Non-goals for this slice

- No expansion of NLP rights intelligence.
- No change to Daisy/Human reviewer authority model.
- No policy relaxation for auto-publication beyond explicit gate config.

This slice is purely semantic correctness + operator clarity + safer routing behavior.