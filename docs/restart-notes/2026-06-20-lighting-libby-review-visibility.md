# 2026-06-20 — Lighting.zip Libby -> review visibility follow-up

## Status
- Reproduced the issue: Lighting-related Libby completions are not visible in `/api/v1/workspace/review-cases`.
- Root cause confirmed: Tracy non-blocking provenance outcomes (`review_required` / `pass`) could enqueue Libby without creating any `review_cases` row; review-cases API only lists DB-backed open review cases.
- Implemented a forward fix in `scripts/run_tracy_provenance.py`:
  - Added `review_case_recommendation_for_libby_handoff(artifact)`.
  - For non-blocking Tracy outcomes without a rights review recommendation, persistence now creates a review case gate at `current_stage = libby_disposition_review`.
  - Existing rights-review flow remains intact; Daisy coordination queue items are still created only for rights-blocking recommendations.

## Verification
- Tests run:
  - `pytest -q /data/symgov/tests/test_tracy_provenance_flow.py` → `8 passed`
  - `pytest -q /data/symgov/tests/test_duplicate_exception_workflow.py /data/symgov/tests/test_workspace_rights_review_api.py` → `12 passed`
- Added regression tests in `tests/test_tracy_provenance_flow.py`:
  - non-blocking recommendation helper behavior
  - process_queue_item persistence creates Libby gate review case and propagates it into Libby queue payload
- Live check (current pre-fix historical batch state):
  - `docker exec symgov-hermes-api ... /workspace/review-cases` showed `lighting_hits=0` before any backfill/replay.

## Files changed
- `scripts/run_tracy_provenance.py`
- `tests/test_tracy_provenance_flow.py`

## Uncommitted state (focused)
- `M scripts/run_tracy_provenance.py`
- `M tests/test_tracy_provenance_flow.py`

## Next actions
1. Run a new Tracy->Libby non-blocking submission (or replay one affected item) with DB persistence enabled, then confirm:
   - Tracy creates review case at `libby_disposition_review`.
   - Libby updates that same review case to `classification_review`.
   - `/api/v1/workspace/review-cases` surfaces the case/card.
2. Decide whether to backfill historical Lighting.zip rows that already completed Libby with `review_case_id = null`.

## Restart prompt
"Continue from docs/restart-notes/2026-06-20-lighting-libby-review-visibility.md. Verify the forward fix on a fresh/replayed non-blocking Tracy item and optionally backfill historical Lighting.zip Libby completions with missing review_case_id so they appear in review-cases."