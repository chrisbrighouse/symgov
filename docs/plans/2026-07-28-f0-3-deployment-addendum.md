# F0.3 Deployment Addendum

Date: 2026-07-28

## Purpose

This addendum records the current release state discovered after the historical F0.3 specification, restart handoff and post-checkpoint note were written. Those documents correctly recorded that F0.3 was not deployed at their respective point in time. Preserve them unchanged as historical evidence; this newer note supersedes only their current-state deployment conclusion.

## Verified Git identity

Read-only checks from `/data/symgov` on 2026-07-28 established:

- branch: `main`;
- worktree: clean;
- local HEAD: `45fc6e00b1372fce1e092ebe282f264ccd401cb3` (`[verified] fix Rupert durable queue flush ordering`);
- local `origin/main`: `45fc6e00b1372fce1e092ebe282f264ccd401cb3`;
- remote `refs/heads/main`: `45fc6e00b1372fce1e092ebe282f264ccd401cb3`;
- relation `origin/main...HEAD`: zero behind, zero ahead;
- F0.3 implementation commit: `c7833c8ba19c0c19c1cc7c5267303d324964d39b`;
- post-checkpoint documentation commit: `7bdd9c077109f555fe2a7ea65983726dd0ab5a4c`;
- reviewed release follow-up: `45fc6e00b1372fce1e092ebe282f264ccd401cb3`, which changes `backend/symgov_backend/runtime.py` and `tests/test_f0_3_session_attribution.py` to correct Rupert durable-queue flush ordering.

The immutable production release worktree `/data/symgov-releases/f0.3-45fc6e0` is detached at the same `45fc6e00b1372fce1e092ebe282f264ccd401cb3` and was clean when checked.

## Verified runtime activation

Read-only container and public-boundary evidence established:

- API container: `symgov-hermes-api`;
- API configured working directory: `/data/symgov-releases/f0.3-45fc6e0/backend`;
- API PID 1 command: `python manage_symgov.py serve-api --host 0.0.0.0 --port 8010` (under `docker-init`);
- API process working directory: `/data/symgov-releases/f0.3-45fc6e0/backend`;
- API start time: `2026-07-28T01:52:06.745691151Z`;
- API health: healthy, restart count 0;
- frontend container: `applications-web`;
- frontend mount: `/data/symgov-releases/f0.3-45fc6e0/dist` read-only at `/usr/share/nginx/html`;
- frontend start time: `2026-07-28T01:52:06.985754522Z`;
- frontend health: healthy, restart count 0;
- public `https://apps.chrisbrighouse.com/api/health`: HTTP 200 with `service="symgov-api"`;
- public root: HTTP 200.

This proves the backend and frontend are configured and running from the immutable `f0.3-45fc6e0` release and that the public boundary is healthy. The release worktree contains the repository-owned Rupert runner at the same release identity. No mutating live smoke test was performed in this planning session.

## Current conclusion

F0.3 is **COMPLETE, PUSHED AND DEPLOYED** at production release commit `45fc6e00b1372fce1e092ebe282f264ccd401cb3`, containing implementation commit `c7833c8b`, post-checkpoint record `7bdd9c0`, and the verified Rupert flush-order correction.

This addendum does not claim that F0.4 is implemented. The live F0.4 defect remains: published feedback/review requests can change a published revision to `review`, and browser requests are attributed to Ed rather than the authenticated requester.

## Actions not taken

This verification was read-only. It did not push, deploy, rebuild assets, migrate or mutate the database, restart services/gateways, publish or withdraw symbols, send external messages, or clean/reset/stash work.
