# Symgov live auth rollout restart notes — 2026-06-26

## Status

Live auth capabilities have been applied to the Symgov database and API.

Completed:
- Took a pre-migration live PostgreSQL backup before schema changes.
- Applied Alembic migration `20260624_0017_user_auth_roles` to the live database using the migration database role.
- Confirmed live database is now at Alembic head `20260624_0017`.
- Enabled Chris's account as an active user with roles `admin`, `reviewer`, and `submitter`.
- Initial PIN was set out-of-band; `must_change_pin` is true, so the UI should force a PIN change on first login.
- Fixed the management CLI bootstrap output bug in `backend/symgov_backend/management.py` by replacing the removed `user.roles` relationship access with `user_roles(session, user.id)`.

Backup:
- `/data/symgov/backups/symgov-pre-auth-migration-20260626T183452Z.dump`
- SHA256: `a572f60652d121a9cfa2fbbaa0053e7b090b77651a10c180ea6a19e33c5e8091`

## Verification

Live database verification:
- `alembic_version` is `20260624_0017`.
- `users` has auth columns: `pin_hash`, `pin_set_at`, `must_change_pin`, `is_active`, `updated_at`.
- `user_roles` and `user_sessions` tables exist.

Live API verification inside `symgov-hermes-api`:
- `POST /api/v1/auth/login` with Chris's email and the initial PIN returned HTTP 200.
- `GET /api/v1/auth/me` with the session cookie returned HTTP 200 and Chris's user record.
- `GET /api/v1/admin/users` with Chris's session cookie returned HTTP 200 and listed all users.
- Unknown-user login returned HTTP 401 with `Invalid email or PIN.`

Local verification:
- `PYTHONPATH=/data/symgov/backend:/data/symgov/backend/.deps python3 -m pytest tests/test_auth_pin_hashing.py tests/test_auth_service.py tests/test_auth_dependencies.py tests/test_user_auth_models.py tests/test_frontend_forced_pin_change.py tests/test_admin_user_management_ui.py -q`
  - Result: `27 passed in 1.53s`
- `npm run build`
  - Result: Vite production build passed in 1.42s.

Not completed:
- Extra proxy-level verification via a temporary Docker curl container was blocked by the tool safety guard. Direct live API verification passed.

## Uncommitted state

Expected relevant uncommitted change:
- `backend/symgov_backend/management.py` — management CLI fix for bootstrap output after auth roles migration.

Other observed uncommitted/untracked state at the time of notes:
- repo is ahead of `origin/main` by 14 commits.
- `backups/` is untracked because the live DB backup was written under `/data/symgov/backups`.
- `docs/plans/2026-06-26-hermes-dashboard-cloudflare-restart.md` was already untracked before this auth rollout work.
- This restart note is newly added: `docs/plans/2026-06-26-auth-live-rollout-restart.md`.

## Login UI polish update

After the auth rollout, the login screen was redesigned for a more polished and usable first-run experience.

Changed:
- Replaced the basic `submission-panel` login layout with a two-card `auth-screen` layout.
- Added a dark control-room hero panel with Symgov workflow signals.
- Added a focused glass login card with clearer hierarchy and stronger call-to-action.
- Added PIN digit sanitisation, four-step PIN progress indicator, and Show/Hide PIN toggle.
- Added first-login helper copy explaining that a PIN change may be required.
- Added responsive CSS for tablet/mobile layouts.
- Published the rebuilt static frontend to `/data/symgov` and `/data/.openclaw/workspace/symgov`.

Additional UI verification:
- `npm run build` passed after the UI change.
- Frontend-focused tests passed: `9 passed` for `tests/test_frontend_forced_pin_change.py` and `tests/test_admin_user_management_ui.py`.
- Browser navigation to `https://apps.chrisbrighouse.com/login` loaded the new login screen.
- Visual inspection found the page polished and usable with no obvious rendering/alignment issues.
- applications-web served the new built asset names (`index-CTGbV7vz.js`, `index-DjKHfWww.css`).

## Next actions

1. Chris should log in at the Symgov app with his email and the initial PIN shared out-of-band.
2. Confirm the forced PIN-change flow works in the browser.
3. Optionally add `/backups/` to `.gitignore` or move the backup outside the repo if the untracked backup directory is undesirable.
4. Consider committing the management CLI fix, login UI change, and this restart note.
5. If browser login fails while direct API login works, inspect applications-web/Cloudflare/proxy routing and browser console/network evidence.

## Restart prompt

Continue Symgov auth rollout and login UI verification. Start by reading `/data/symgov/docs/plans/2026-06-26-auth-live-rollout-restart.md`, then check `git -C /data/symgov status --short --branch`. Verify whether Chris can log in through the browser with his email and the initial PIN shared out-of-band, confirm the forced PIN-change flow, and if it fails debug the browser/proxy path while preserving the verified live API state.
