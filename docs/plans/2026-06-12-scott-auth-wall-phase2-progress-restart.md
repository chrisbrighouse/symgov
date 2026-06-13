# Restart notes: Scott auth-wall rollout Phase 2

## Status
- Implemented Scott auth-aware fetch logic in `/data/.openclaw/workspaces/scott/run_scott_intake.py`.
  - `fetch_text_url(...)` now accepts `auth_secret_key` and resolves secrets from runtime environment or env file.
  - Header injection supports `Authorization` (`Bearer`/`Basic`), synthesized Basic auth from `user:pass`, `Cookie`, and `X-Api-Key` fallback.
  - `inspect_candidate_site(...)` now accepts auth context and sets auth outcomes (`auth_verified`, `auth_failed`, fallback `gated_detected`/`no_auth`).
  - Prior-site inspection in `run_source_discovery_task(...)` now passes `auth_secret_key` and `db_env_file` through for session validation.
  - Follow-up fix on 2026-06-13: `detect_auth_wall(...)` no longer treats an unchanged authenticated URL containing words such as `/basic-auth/...` as an auth redirect. Auth-looking URL markers are now considered redirect evidence only when the fetch actually redirects to that target.
- Added frontend API client method `updateScottSourceSiteAuth(...)` in `frontend/src/api.js` with wrapped-request fallback handling.
- Added Scott Sources auth editors in `frontend/src/App.jsx`:
  - inline `requiresAuth` toggle,
  - inline `authStatus` selector,
  - inline `authSecretKey` draft/save input.
- Added targeted test module `tests/test_scott_auth_verification.py` covering auth header resolution, auth redirect detection, and auth status transitions.

## Verification
- Worker script syntax check passed:
  - `python3 -m py_compile /data/.openclaw/workspaces/scott/run_scott_intake.py`
- Backend tests passed:
  - `uv run --with pytest --with-requirements backend/requirements.txt pytest tests/test_scott_auth_verification.py -q` → `6 passed`
  - `uv run --with pytest --with-requirements backend/requirements.txt pytest tests/test_scott_source_recommendations.py tests/test_scott_auth_verification.py -q` → `11 passed`
- Frontend build passed:
  - `npm run build` → Vite build succeeded, producing `dist/assets/index-DCVUZqt0.css` and `dist/assets/index-BelwM6KO.js`.
- Frontend static publish completed on 2026-06-13:
  - `npm run publish:static` → published from `/data/symgov/dist` to `/data/symgov` and `/data/.openclaw/workspace/symgov`.
- Live API health passed:
  - `docker exec symgov-hermes-api sh -lc 'curl -fsS http://127.0.0.1:8010/api/v1/health'` → `{"ok":true,"service":"symgov-api",...}`
- Live API filter re-check correct after cleanup:
  - `GET /api/v1/workspace/scott/source-sites?authStatus=auth_verified` → `auth_verified_count: 0` after temporary fixture removal.
  - DB gated rows remain the expected real sources only: `webstore.iec.ch` and `necanet.org`, both `gated_detected` with no secret key.
- Auth patch route previously validated with wrapped payload:
  - `PATCH /api/v1/workspace/scott/source-sites/fac08302-865d-5c9b-adfd-b53f36fb86f4/auth` with `{"request":{"requiresAuth":true}}` returned updated source-site JSON successfully.

## Live Scott auth verification run (2026-06-13)
- Added a temporary controlled source-site fixture for `httpbin.org` using `https://httpbin.org/basic-auth/user/pass` and secret reference `SCOTT_E2E_BASIC`.
- Negative credential run:
  - queue item `2f6d6c68-5939-4a47-9bee-9629f4f8af36`
  - env `SCOTT_E2E_BASIC=wrong:creds`
  - persisted source-site row moved to `auth_failed` with HTTP 401 auth evidence.
- Positive credential run:
  - queue item `751185fa-5134-459d-98fd-a4388aae6696`
  - env `SCOTT_E2E_BASIC=user:pass`
  - first attempt exposed a bug: the unchanged URL path `/basic-auth/user/pass` was being interpreted as an auth redirect because `detect_auth_wall(...)` scanned the original URL for `auth`.
  - fixed the redirect heuristic in `/data/.openclaw/workspaces/scott/run_scott_intake.py`, added regression tests, and reran.
  - resulting artifact and DB replay showed `auth_status='auth_verified'`, HTTP 200, and `auth_detection.reason='none'` for `httpbin.org`.
- Cleanup completed:
  - deleted temporary `httpbin.org` row from `scott_source_discovery_sites`.
  - removed temporary runtime queue files for both verification queue items.
  - DB queue rows remain as historical `progress_saved` records, but no active runtime files remain and `httpbin.org` is no longer in source-site memory.

## Uncommitted state
- `/data/symgov` has uncommitted changes:
  - modified: `frontend/src/App.jsx`
  - modified: `frontend/src/api.js`
  - new: `tests/test_scott_auth_verification.py`
  - new/updated: `docs/plans/2026-06-12-scott-auth-wall-phase2-progress-restart.md`
- Additional non-repo runtime change:
  - modified: `/data/.openclaw/workspaces/scott/run_scott_intake.py`

## Next actions
1. Decide whether to publish the latest frontend build with `npm run publish:static` (the build succeeded; publishing was not repeated after the 2026-06-13 test-only/runner fix).
2. If the auth editor UX feels sufficient, commit `/data/symgov` changes.
3. Separately snapshot/deploy the updated Scott runner script from `/data/.openclaw/workspaces/scott/run_scott_intake.py`; this file is outside the Git repo but is part of the live direct-worker path.
4. Optional small UX polish: add an explicit clear-secret affordance and success indicator for auth saves.

## Restart prompt
Continue from `/data/symgov` and `/data/.openclaw/workspaces/scott`. Read `docs/plans/2026-06-12-scott-auth-wall-phase2-progress-restart.md`, run `git status --short --branch` in `/data/symgov`, confirm `/data/.openclaw/workspaces/scott/run_scott_intake.py` still contains the redirect-only `auth_redirect` heuristic in `detect_auth_wall(...)`, then either publish/commit the Phase 2 frontend+test changes or snapshot the external Scott runner update.
