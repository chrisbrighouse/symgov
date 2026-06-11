# Restart notes: build stamp auto-date fix

## Status
- Fixed the frontend build/version stamp so Vite rewrites `meta[name="symgov-build"]` at build time instead of preserving the stale hard-coded date from `frontend/index.html`.
- Current generated stamp format is `YYYY-MM-DD.01`, using the UTC build date.
- `SYMGOV_BUILD_STAMP` can override the value for controlled deployments.
- Updated `scripts/publish-static.sh` so publishing writes both the repo/static root (`/data/symgov`) and the currently served compatibility root (`/data/.openclaw/workspace/symgov`) when they are different paths.
- Synced `package-lock.json` to the existing `package.json` version `0.1.6`.

## Verification
- `node --test tests/test_build_stamp.mjs frontend/src/timerControls.test.js` passed: 8/8 tests.
- `npm run build` passed with Vite 7.3.2.
- `npm run publish:static` published to both static roots.
- Live check with cache-busting confirmed the public app serves:
  - `v0.1.6 · 2026-06-11.01`
  - `meta[name="symgov-build"]` content `2026-06-11.01`

## Uncommitted state to be aware of
- This fix intentionally changed:
  - `vite.config.js`
  - `tests/test_build_stamp.mjs`
  - `package-lock.json`
  - `scripts/publish-static.sh`
  - built/published static files under `dist/`, `/data/symgov`, and `/data/.openclaw/workspace/symgov`
- The repository already had substantial unrelated uncommitted Symgov changes before this fix, including backend routes, workspace UI/API files, Hannah scripts/tests, timer controls, and published-symbol workflow files. Do not treat those unrelated changes as part of this build-stamp fix without reviewing separately.

## Next actions
- If you want this saved in git, commit only the build-stamp-related source/test/script/lockfile changes plus any intended generated static assets.
- Consider retiring the compatibility public root once nginx/Traefik definitively serves `/data/symgov`, then simplify `publish-static.sh`.

## Restart prompt
Continue Symgov build-stamp work from `/data/symgov`. Review `docs/plans/2026-06-11-build-stamp-auto-date-restart.md`, verify the live app still shows the current build stamp, then decide whether to commit the build-stamp fix separately from the pre-existing unrelated working-tree changes.
