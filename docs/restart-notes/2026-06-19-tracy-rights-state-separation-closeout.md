# Tracy rights-state separation closeout note

Goal
- Separated overloaded "escalate" concept into `processing_outcome` (pipeline progression) and `rights_disposition` (publication risk).
- Fixed issues where unknown provenance would stall in Tracy as "escalated" instead of proceeding to Libby.

Changes
- **Backend Schema**: Added `rights_disposition` and `processing_outcome` columns to `provenance_assessments` table (Alembic revision `20260619_0016`).
- **Tracy Runner**: 
  - `unknown_warning` now results in `completed` status and `review_required` outcome, allowing it to flow automatically to Libby classification.
  - `cleared` results in `pass` outcome.
  - `restricted / conflict` results in `failed` outcome and creates a blocking rights review case.
- **Libby Runner**: Now prefers the canonical `rights_disposition` field for its classification heuristics.
- **Automation Policy**: The publication gate now explicitly checks `rights_disposition` (only `cleared` allowed for auto-publication initially).
- **Frontend UI**:
  - Added `RightsBadge` component for visual distinction of provenance states.
  - Workspace and Curation views now show both the pipeline progress and the explicit rights disposition.
  - CSS added for `cleared` (mint), `unknown_warning` (gold), and `restricted/conflict/failed` (rose).
- **Tests**: Added regression coverage in `tests/test_tracy_provenance_flow.py` for canonical state transitions.

Verification
- `PYTHONPATH=backend pytest tests/test_tracy_provenance_flow.py -v` (PASSED)
- `PYTHONPATH=backend pytest tests/test_workspace_asset_preview.py -v` (PASSED)
- `PYTHONPATH=backend pytest tests/test_vlad_hardening.py -v` (PASSED)

Next Actions
- Monitor the Tracy monitor lane for "completed" vs "failed" distributions.
- Adjust `LOW_RISK_RIGHTS_STATUSES` in `automation_policy.py` if `unknown_warning` items are eventually deemed safe for auto-publication.
- Remove legacy `rights_status` usage in a future cleanup cycle.

Restart prompt:
"Continue Symgov development. Tracy rights separation is complete. Schema updated, runners updated, and UI badge added. Verify the RightsReview lane behavior with a live submission."
