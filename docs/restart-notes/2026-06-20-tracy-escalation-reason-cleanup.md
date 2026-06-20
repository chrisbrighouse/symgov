# Tracy escalation reason cleanup restart note

Goal
- Finish the Tracy rights-state separation by ensuring non-blocking `unknown_warning` provenance no longer leaves an operator-facing escalation reason on the Tracy queue item.

Change
- Added `queue_escalation_reason_for_artifact()` in `scripts/run_tracy_provenance.py`.
- `process_queue_item()` now derives `queue_item["escalation_reason"]` from canonical `rights_disposition` / `processing_outcome`, not legacy `decision`.
- `unknown_warning` + `review_required` now has queue status `completed` and `escalation_reason=None`.
- `restricted`, `conflict`, and `failed` still produce a blocking operator-facing escalation reason: `provenance_rights_review_required`.
- Added regression assertions in `tests/test_tracy_provenance_flow.py`.

Verification
- RED check first failed as expected because `queue_escalation_reason_for_artifact` did not exist.
- `PYTHONPATH=backend pytest tests/test_tracy_provenance_flow.py -v` passed: 6 passed.
- `PYTHONPATH=backend pytest tests/test_workspace_asset_preview.py tests/test_vlad_hardening.py -q` passed: 15 passed.

Uncommitted state
- Modified: `scripts/run_tracy_provenance.py`
- Modified: `tests/test_tracy_provenance_flow.py`
- Added: `docs/restart-notes/2026-06-20-tracy-escalation-reason-cleanup.md`

Next actions
- Commit these changes if the behavior looks right.
- Optionally run a live Tracy submission to confirm the monitor lane shows unknown provenance as completed/warning rather than escalated.

Restart prompt
"Continue Symgov development. Tracy rights-state separation is implemented and committed at 0c589cf, with an additional uncommitted cleanup ensuring unknown_warning no longer leaves an escalation_reason. Verify/commit the changes in scripts/run_tracy_provenance.py and tests/test_tracy_provenance_flow.py."
