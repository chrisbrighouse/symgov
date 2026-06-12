# Restart notes: Scott auth-wall rollout Phase 1

## Status
- Verified queue item `74a7ebdd-e84d-446d-85f2-9b8b50c1c174` and successfully persisted `requiresAuth` and `authStatus` into `scott_source_discovery_sites`.
- Added Scott Sources UI columns and filters for auth fields (`requiresAuth`, `authStatus`, `authSecretKey`) in the frontend.
- Committed and pushed all Phase 1 backend, worker, and frontend changes.
- Tracked and committed the untracked Alembic template `backend/alembic/script.py.mako` to keep the working tree fully clean.

## Verification
- Automated tests passed:
  `uv run --with pytest --with-requirements backend/requirements.txt pytest tests/test_scott_source_recommendations.py -q` passed with 5/5.
- API check on persisted auth fields returned correct results:
  `docker exec symgov-hermes-api curl -s "http://127.0.0.1:8010/api/v1/workspace/scott/source-sites?limit=5&requiresAuth=true&sort=authStatus&direction=asc"` returned `webstore.iec.ch` and `necanet.org` as `requiresAuth: true` and `authStatus: gated_detected`.
- Frontend successfully compiled: `npm run build` completed with no errors.

## Uncommitted state
- Working tree is 100% clean.
- Branches are pushed and up-to-date with `origin/main`.

## Next actions
- Continue to Phase 2 of the Scott auth-wall rollout (implementing auth secret configurations, auth session-validation on worker downloads, or targeted login testing).
- Perform a live verification of the Scott Sources UI filters in the browser to ensure the newly added auth search selects and columns display correctly.

## Restart prompt
Continue from `/data/symgov` / `/docker/openclaw-hz0t/data/symgov`. Read `docs/plans/2026-06-12-scott-auth-wall-rollout-restart.md`, verify the active API filters by running `docker exec symgov-hermes-api curl -s "http://127.0.0.1:8010/api/v1/workspace/scott/source-sites?requiresAuth=true"`, and prepare the implementation plan for Phase 2.
