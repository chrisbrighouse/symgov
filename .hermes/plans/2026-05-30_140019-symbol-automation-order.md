# Symgov symbol automation order

## Goal

Move Symgov toward automatic symbol processing through intake, validation, provenance, classification, publication, and post-publication curation without bypassing governance controls.

## Current verified state

- Live API container: `symgov-hermes-api` is healthy.
- Agent workers are enabled for `scott,vlad,tracy,libby,daisy,rupert,ed`.
- Hannah curation exists but is not in the always-on worker list.
- Database health check succeeds.
- Current queue truth has been reconciled once using `manage_symgov.py reconcile-agent-queue --apply`.
- Current automation gate evaluation finds no publication candidates eligible for unattended Rupert handoff.

## Recommended order

### 1. Stabilise queue truth

Status: implemented first slice.

Changes:
- Added `symgov_backend.agent_queue_reconciliation`.
- Added CLI command:
  - `python /data/symgov/backend/manage_symgov.py reconcile-agent-queue`
  - `python /data/symgov/backend/manage_symgov.py reconcile-agent-queue --apply`

Rules:
- Default is dry run.
- Only active/stuck DB rows are inspected by default.
- Runtime JSON may update DB only when queue id, agent, source type, source id all match and runtime status is terminal.

Validation run:
- Dry run found one stale Scott DB row marked queued while runtime JSON was completed.
- Apply updated that one verified row.
- Follow-up dry run reported no changes remaining and one genuinely queued Libby review-follow-up item.

### 2. Add explicit automation policy gates

Status: implemented first read-only evaluator.

Changes:
- Added `symgov_backend.automation_policy`.
- Added CLI command:
  - `python /data/symgov/backend/manage_symgov.py evaluate-automation-gates --limit 50`
  - `python /data/symgov/backend/manage_symgov.py evaluate-automation-gates --classification-id <id>`

Current conservative gates require:
- Classification record is current.
- `classification_status == classified`.
- `libby_approved == true`.
- Category and Discipline are non-placeholder.
- Item is not a sheet/unclassified-symbol placeholder.
- Validation report exists and passes with zero defects.
- Provenance assessment exists with low-risk rights and low risk level.
- No blocking human-review stage remains open.

Current outcome:
- 33 current classification records evaluated.
- 0 allowed for unattended Rupert handoff.
- Main blockers: rights/provenance are still unknown/medium, many items are provisional symbol sheets, and several human-review cases are still open.

### 3. Enable low-risk publication path

Status: implemented second read-only/operator slice; unattended publication remains disabled.

Changes:
- Extended `symgov_backend.automation_policy` with `evaluate_symbol_metadata_gate`.
- Added unit tests under `tests/test_automation_policy.py`.
- Added CLI check:
  - `python /data/symgov/backend/manage_symgov.py evaluate-automation-gates --review-split-metadata --limit 50`

Policy decision for Libby/Vlad split symbols:
- Libby may only send a split symbol toward unattended Rupert handoff if the symbol has a specific name, category, and discipline.
- Generic Vlad/file-split fallback names such as `01-CommonValves Region 15` are blocked even if category and discipline are filled in.
- Specific OCR/history/Libby-inferred names such as `Globe Valves`, `Float Operated`, or `Balance Diaphragm` can pass the metadata gate if category and discipline are also specific.
- Libby-inferred values are acceptable; the gate does not require a human source. It checks the specificity and appropriateness of the values, not just who typed them.

Current live metadata gate result:
- 50 split-symbol property rows evaluated.
- 3 pass the metadata-only gate.
- 47 are blocked.
- Main blockers are missing category/discipline and generic region-style split names.

Still not enabled:
- No code currently auto-runs Rupert from this metadata check.
- The broader publication gate still returns 0 fully eligible unattended publication candidates because provenance/rights, validation, and open-review conditions are not yet satisfied.

Next implementation target:
- Wire policy evidence into any future Libby -> Rupert queue creation so blocked items are routed to Daisy/review instead of Rupert.
- Keep Rupert unattended handoff disabled until a candidate passes both metadata and full publication gates.

### 4. Enable Hannah curation safely

Next after publication gates.

Design:
- Add explicit env toggle, e.g. `SYMGOV_ENABLE_HANNAH_AUTO_CURATION=1`.
- Add cooldown/interval and max duration.
- Enforce single active run.
- Reuse existing Hannah runner and DB persistence.
- Never auto-attach `needs_review` candidates.

Likely files:
- `backend/symgov_backend/settings.py`
- `backend/symgov_backend/app.py`
- `backend/symgov_backend/routes/workspace.py`
- `scripts/run_hannah_curation.py`

### 5. Add operations health view

Design:
- Add a machine-readable endpoint or command summarising:
  - per-agent queue status counts
  - active/stuck queue rows
  - DB/runtime reconciliation drift count
  - recent agent errors
  - review backlog
  - publication counts
  - Hannah last run / candidates / attached count

Likely files:
- `backend/symgov_backend/routes/admin.py` or `routes/workspace.py`
- `backend/symgov_backend/schemas.py`
- `backend/manage_symgov.py`

## Immediate next decision

Before deployment, review the current code diff and decide whether to:

1. keep these first two CLI tools as local operator tools only for now, or
2. expose their summaries through an API endpoint for the workspace operations view.
