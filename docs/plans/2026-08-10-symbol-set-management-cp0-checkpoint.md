# Symbol Set Management — CP0 Foundation Checkpoint

- Checkpoint: CP0 Foundation gate
- Checkpoint date: 2026-08-10
- Repository: `/data/symgov`
- Immutable-note status: this file is the only new repository artifact created by this CP0 preparation.
- Gate status at note creation: **CP0 remains blocked pending fresh independent review and an authorized local checkpoint commit.**

## 1. Purpose and boundary

CP0 is a gate only. It records the current repository and Kanban evidence needed before Symbol Set Management Stage 1. **Stage 1 has not started.** No organization schema, invariant service, Stage 1 migration, F1 reviewer-scope work, F2 work, or implementation card was created or dispatched by this checkpoint preparation.

This note is immutable checkpoint evidence. It must not be rewritten to add its own final SHA-256 or a later commit SHA. The note path/hash and any checkpoint-commit result belong in the CP0 card and a separate post-checkpoint audit.

## 2. Frozen controlling artifacts

The four controlling artifacts were rehashed before relying on them; all matched the required values:

- `docs/Symbol Set Management Spec v0.3.md` — SHA-256 `b2d6cb681a7bb3e6c9495d39963f9114aadec13b936409573305489784db570a`; 106540 bytes; 941 lines
- `docs/plans/2026-08-08-symbol-set-management-implementation-plan.md` — SHA-256 `e69682310400c56af8b0633d01e57cbc3fa913b08a37485665ea0d5448dba283`; 92846 bytes; 968 lines
- `docs/plans/2026-08-08-symbol-set-management-decision-addendum.md` — SHA-256 `fa56ee7685e14103423baf1ed347e2277a0b9e50e9efe89fa892f058cb0af071`; 15903 bytes; 99 lines
- `docs/plans/2026-08-08-symbol-set-management-luna-resume.md` — SHA-256 `c3863105d9de5460af702871d134c4adb776af9ff3422ae9864085f7249aa681`; 14500 bytes; 167 lines

The historical resume contains embedded status/source prose that is stale for current-state purposes, including the older product-spec hash beginning `42c240...`. That historical prose is preserved. The verified live hashes above and the live repository/board evidence below control this checkpoint.

## 3. Repository identity and preserved worktree

The opening/pre-note repository capture was `2026-08-10T19:55:45Z`. The closing source snapshot for this note was captured immediately before note creation from the same live worktree; its branch, commits, tree, patch hashes, status manifest and migration result matched the opening capture. Creation of this note occurs after that source snapshot and is the sole new path; its post-creation hash is intentionally recorded outside this immutable note.

- Branch: `main`; `origin/main`: `a18d5b3587ebb11c95f45ca16643efe94b322c61`; branch is ahead by one commit.
- `HEAD`: `33d49b38ac7784b31357385b35b4800ed0824b5c`; `HEAD^`: `a18d5b3587ebb11c95f45ca16643efe94b322c61`; `HEAD^ == origin/main`: `True`.
- `HEAD` tree: `6fe8672434ebb0000a6e8982655674b1293fe3bc`.
- Staged patch SHA-256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (empty staged diff at the source snapshot).
- `HEAD`-to-worktree patch SHA-256: `8584f91fc93438798cbc46ec159d90ef82610bd343c28ccaa8f7a720a06921c0`.
- Untracked-path manifest SHA-256 from `git ls-files --others --exclude-standard`: `f526a7fa7dd5b3d449997369a213702657593eca1944904effa6590cf11f67b6`.
- `git status --short --branch` output SHA-256: `f68d5f643b24ca3b46434e994ad109b018282d46902aa2c8e7da1aba6b380fb0`.
- Source manifest counts: 23 modified tracked, 3 deleted tracked, 13 untracked; exact paths and content hashes follow.
- Existing dirty paths were preserved byte-for-byte; no existing path was reset, cleaned, stashed, deleted, overwritten, or reformatted.

### Modified tracked paths (23)
- `.gitignore` — worktree SHA-256 `29dcdf37c8aedb7d2b491e540da6de4c86d31c1d699fe3a93228954021eadc91`
- `README.md` — worktree SHA-256 `c82b785b72319eaa3cc5a1ff8cd219dcae215d4d7e58c77ff8b7c32ae54208f6`
- `backend/README.md` — worktree SHA-256 `fc9535044ac4918bea9151432ed7caece1d07843912876e029e5d34d41752a5e`
- `backend/manage_symgov.py` — worktree SHA-256 `10cf55c776fb9091cd5a327f87d5532b83384b6ce38eb19bf8c628fef1e25ddf`
- `backend/symgov_backend/__init__.py` — worktree SHA-256 `a5b9f8dad809547ba1ebb31422edfafbc2023b790bad63d2e3a2a6db93c5df4c`
- `backend/symgov_backend/app.py` — worktree SHA-256 `3a392c5f7d4ff1b5551350160e742f126358dacc0c7a192910e0b5784fb62731`
- `backend/symgov_backend/auth.py` — worktree SHA-256 `e7807d11d614658d9777e30c25f36996466caad62d5e20fca46a22cf8f1c6ac4`
- `backend/symgov_backend/dependencies.py` — worktree SHA-256 `6182847c7e8ef9ec7dacbd002039c03af4ebdc89cd5fb578e6cf78102602e4b5`
- `backend/symgov_backend/models/__init__.py` — worktree SHA-256 `8b6481fb59c6c266a2318cad2ff402847f8a6373cf2e174ff299ac137c2c562a`
- `backend/symgov_backend/models/schema.py` — worktree SHA-256 `c5f38c9ce83e3e7528dc18170a579e6e210d6d233cef418bca75b8300bc3403d`
- `backend/symgov_backend/routes/admin.py` — worktree SHA-256 `9c255196f66e85e1bc80cbd0a8665881592e3572146ada0c5f34743e51225bae`
- `backend/symgov_backend/routes/auth.py` — worktree SHA-256 `2f03031b1e4f50d3ba5275de232f4fcac0525292522b2c85a83e01afdc5b28eb`
- `backend/symgov_backend/routes/catalog.py` — worktree SHA-256 `c79df4eb3aace16791e14bbea58cc48bd0db7e3707f610ecb963d6625bcef0f0`
- `backend/symgov_backend/routes/public.py` — worktree SHA-256 `bffb916086a85d3db037c65c4dc818b9f14b653c6675c36139e5038814ec154d`
- `backend/symgov_backend/schemas.py` — worktree SHA-256 `3f90d83af8f2333a862cf2a512412447b759d64e812eac499d76ef24a7fb15e1`
- `backend/symgov_backend/settings.py` — worktree SHA-256 `4fd810f9276b119f27f58a9011a6c68df93b966a28656dfbf06595fcd6b268aa`
- `symgov-agent-architecture.md` — worktree SHA-256 `2e0bc40ede5307072c0fdefe32c026e58cabd430ab39308b3e66cefb0b45261f`
- `tests/test_admin_llm_management_routes.py` — worktree SHA-256 `db97901c4bb093bf2b03234052dbcdbc7ddba599a1ea2ae0c4dbee99feb08b84`
- `tests/test_admin_user_management_routes.py` — worktree SHA-256 `4f0a8a8fa51165b51ceca806217c77aeaf98037fda2828c8998c62f47585bbba`
- `tests/test_auth_routes.py` — worktree SHA-256 `7cb2f2703c51529dab270df596361e77f8c7ca2f6b9eb627ea5f21644a6f3495`
- `tests/test_catalog_symbol_download.py` — worktree SHA-256 `a81c49c948791b51153358a212aff0b404d9f2cfdc8f2e6542d7fd1b92f3158e`
- `tests/test_llm_usage_migration.py` — worktree SHA-256 `521cd302f71d359cbd653d24884a44671c997a84d6e9f1ba5710cafd8eccd6f5`
- `tests/test_profile_subscriptions.py` — worktree SHA-256 `e3b4abc392c284b165ec18ed4c7905723a25aa7279ccd52d6d870d084cc62f36`

### Deleted tracked paths (3)
- `backend/symgov_backend/openclaw_sync.py` — HEAD content SHA-256 `059a0f64a0d190cc5ce0e53ae080c169b2d276355a986bce659ebf82bdb410c8` (deleted in worktree)
- `openclaw-agents.manifest.json` — HEAD content SHA-256 `628ae0ca59820cbe6f005ef90d06171e075cb7e32122c9110ec5bcf654565446` (deleted in worktree)
- `tests/test_openclaw_sync.py` — HEAD content SHA-256 `5d4186c8a5685df395cf9397a70a7eed5c3225295a43b189eed9f7b04d3da7b9` (deleted in worktree)

### Untracked paths present before this note (13)
- `CLAUDE.md` — worktree SHA-256 `c1d837e72a751e8f3037435ad8d0f765f618d081a38807d91426b44b7751d516`
- `backend/alembic/versions/20260808_0027_account_security_invariants.py` — worktree SHA-256 `bf54d28e3cec706a5bda1a346fd8bc53fded0a2a448d962547dfc32126d7fc09`
- `backend/symgov_backend/auth_security.py` — worktree SHA-256 `3b14fe9f6fe675561ca7f93c63b26861b72924bcd2de55a65926abbfbfd006b2`
- `docs/Symbol Set Management Spec v0.3.md` — worktree SHA-256 `b2d6cb681a7bb3e6c9495d39963f9114aadec13b936409573305489784db570a`
- `docs/plans/2026-08-08-symbol-set-management-decision-addendum.md` — worktree SHA-256 `fa56ee7685e14103423baf1ed347e2277a0b9e50e9efe89fa892f058cb0af071`
- `docs/plans/2026-08-08-symbol-set-management-implementation-plan.md` — worktree SHA-256 `e69682310400c56af8b0633d01e57cbc3fa913b08a37485665ea0d5448dba283`
- `docs/plans/2026-08-08-symbol-set-management-luna-resume.md` — worktree SHA-256 `c3863105d9de5460af702871d134c4adb776af9ff3422ae9864085f7249aa681`
- `tests/conftest.py` — worktree SHA-256 `dfcb5faa7018c18c463c228c4acef7e3e79a04ba5dbce6a0ab88322afcfb7091`
- `tests/test_auth_login_security.py` — worktree SHA-256 `f6994994a618fc59a79416829146a012f951ac990020e6c43db0b17226cb94c5`
- `tests/test_auth_security_migration.py` — worktree SHA-256 `a133ee1b53cf2db19ceff2b684ce6c405a5ee5da132994c577c4cec5a5c6033c`
- `tests/test_csrf_policy.py` — worktree SHA-256 `c7623fc42493c56d3702398be1f4e633c65e6b467df5779e42ba05e96681c55c`
- `tests/test_f0_5_account_security.py` — worktree SHA-256 `88511eb906ab9b5f5bb2186925d34b1c57ac8a1cbe41b8cb5243450abb03f64e`
- `tests/test_f0_5_postgresql_security.py` — worktree SHA-256 `206d8982395e7c22ece53b7b1026e933429704a7f5ecaeadc1c3f2b51c6f3348`

## 4. Current Kanban graph and prerequisite disposition

The compact board refresh was captured at `2026-08-10T19:48:44Z`. Current statistics were:

```text
By status:
  triage    0
  todo      2
  scheduled  0
  ready     0
  running   0
  blocked   1
  done      123

By assignee:
  cody                  done=108
  symgov                blocked=1, done=15, todo=2
```

Fresh compact inspections also returned diagnostics `[]`, no ready tasks, and no running tasks. The verified profile inventory contains `cody`; no CP0 review card or worker was created during this preparation.

- **F0.5 — `t_d0712f83`: DONE/PASS.** Live root evidence names accepted child chain `t_f54d897a -> t_36c28c44 -> t_2f480004 -> t_4e80d006`, followed by cleanup/identity `t_66ecd375` PASS. The accepted evidence covers forced-PIN/session security, CSRF, throttling/audit, revocation, focused regressions, PostgreSQL security, canonical backend regression, compilation and sole Alembic head `20260808_0027`. The accepted F0.5 result is historical evidence from its frozen snapshot; it is not a claim that those tests were rerun in this CP0 session.
- **F0.6 — `t_c7e92882`: DONE and explicitly reconciled.** Current root evidence records Hermes profile `symgov`/Alfi-main as the sole active Telegram orchestrator. Fresh Stage 1 `t_64f13bc4` returned literal PASS, Stage 2 `t_fd69d308` returned literal APPROVE, and replacement final `t_17d6cbdf` returned literal PASS for commit `33d49b38ac7784b31357385b35b4800ed0824b5c`. The retained `/data/.openclaw/openclaw.json` direct Libby Telegram route remains unchanged historical compatibility evidence with SHA-256 `a4f05abc072bd299b2cb11c606474e7c72dec7b929c6208c51ed69d76fc89ebb`; it is not an active competing consumer and was not mutated.
- **F0.7: UNDEFINED / NOT SPECIFIED.** There is no live F0.7 card or controlling-plan definition. It must not be inferred as complete or not started.
- **CP0 — `t_08809b70`: BLOCKED/PARKED.** Parent `t_c7e92882`; old child `t_630bd74e`. Its live comments explicitly say manual CP0 preparation is in progress and the card must remain parked without dispatch.
- **Old F1 — `t_630bd74e`: TODO/PARKED.** Do not dispatch; it is the superseded reviewer-scope phase and is not the next controlling Symbol Set path.
- **F2 — `t_549b7971`: TODO/PARKED.** Do not dispatch.
- **Historical `t_09d190f5`:** archived capability-blocked evidence only; it is not a controlling F0.7 definition.

The F0.6 root already contains the concise reconciliation audit; no historical comments or failed/blocked runs were rewritten or deleted.

## 5. Commands and exact evidence

### Freshly run in this CP0 session (read-only)
- command: `git status --short --branch`; exit code: `0`; result: status output: 39 entries are enumerated in the manifest below; duration: `10.7` ms
- command: `git branch --show-current`; exit code: `0`; result: main; duration: `3.2` ms
- command: `git rev-parse HEAD`; exit code: `0`; result: 33d49b38ac7784b31357385b35b4800ed0824b5c; duration: `3.9` ms
- command: `git rev-parse HEAD^`; exit code: `0`; result: a18d5b3587ebb11c95f45ca16643efe94b322c61; duration: `3.6` ms
- command: `git rev-parse origin/main`; exit code: `0`; result: a18d5b3587ebb11c95f45ca16643efe94b322c61; duration: `3.7` ms
- command: `git rev-parse HEAD^{tree}`; exit code: `0`; result: 6fe8672434ebb0000a6e8982655674b1293fe3bc; duration: `4.2` ms
- command: `git diff --cached --binary | sha256sum`; exit code: `0`; result: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855; duration: `4.8` ms
- command: `git diff HEAD --binary | sha256sum`; exit code: `0`; result: 8584f91fc93438798cbc46ec159d90ef82610bd343c28ccaa8f7a720a06921c0; duration: `17.7` ms
- command: `git ls-files --others --exclude-standard | sha256sum`; exit code: `0`; result: f526a7fa7dd5b3d449997369a213702657593eca1944904effa6590cf11f67b6; duration: `6.5` ms
- command: `git diff --check`; exit code: `0`; result: empty stdout; whitespace check passed; duration: `15.0` ms
- command: `PYTHONPATH=. uv run --isolated --with-requirements requirements.txt --with-requirements requirements-test.txt alembic heads`; exit code: `0`; result: 20260808_0027 (head); duration: `474.1` ms

The following are inherited immutable-card results. They are recorded as inherited evidence, not as commands rerun by CP0:

### F0.5 final verification t_4e80d006 (inherited; not rerun in CP0)
- command: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend pytest -q -p no:cacheprovider tests/test_auth_login_security.py tests/test_auth_security_migration.py tests/test_csrf_policy.py tests/test_f0_5_account_security.py -k 'not postgresql'`; exit code: `0`; result: 91 passed, 265 warnings; duration: `not recorded` seconds
- command: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend pytest -q -p no:cacheprovider tests/test_f0_5_account_security.py -k 'mutation_body_middleware_stops_receiving_after_limit_is_crossed or external_submission_rejects_chunked_oversized_unauthenticated_request_before_all_side_effects'`; exit code: `0`; result: 6 passed, 18 deselected; duration: `not recorded` seconds
- command: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend pytest -q -p no:cacheprovider tests/test_f0_5_postgresql_security.py`; exit code: `0`; result: 7 passed; duration: `not recorded` seconds
- command: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend pytest -q -p no:cacheprovider tests/test_auth_routes.py tests/test_admin_user_management_routes.py tests/test_profile_subscriptions.py tests/test_catalog_symbol_download.py`; exit code: `0`; result: 32 passed, 124 warnings; duration: `not recorded` seconds
- command: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend pytest -q -p no:cacheprovider tests/test_catalog_feedback.py::test_feedback_body_is_capped_before_endpoint_buffering tests/test_catalog_feedback.py::test_feedback_rejects_malformed_non_object_and_nonfinite_json tests/test_catalog_contextual_search.py::test_catalog_contextual_search_body_is_capped_before_endpoint_buffering tests/test_csrf_policy.py::test_api_key_only_surfaces_are_explicitly_excluded_from_cookie_csrf_policy tests/test_catalog_symbol_download.py`; exit code: `0`; result: 16 passed, 60 warnings; duration: `not recorded` seconds
- command: `PYTHONDONTWRITEBYTECODE=1 ./scripts/test-backend.sh`; exit code: `0`; result: 1588 passed, 3 skipped, 3 deselected; duration: `not recorded` seconds
### F0.6 fresh Stage 1 t_64f13bc4 (inherited; not rerun in CP0)
- command: `cd /data/symgov && /usr/bin/time -p pytest tests/test_openclaw_sync.py -q`; exit code: `0`; result: initial attempt failed: ModuleNotFoundError: symgov_backend; duration: `0.38` seconds
- command: `cd /data/symgov && /usr/bin/time -p pytest tests/test_llm_usage_migration.py -q`; exit code: `0`; result: initial attempt failed: ModuleNotFoundError: symgov_backend; duration: `0.8` seconds
- command: `cd /data/symgov && /usr/bin/time -p sh -c 'PYTHONPATH=backend pytest tests/test_openclaw_sync.py -q'`; exit code: `0`; result: 3 passed; duration: `0.92` seconds
- command: `cd /data/symgov && /usr/bin/time -p sh -c 'PYTHONPATH=backend pytest tests/test_llm_usage_migration.py -q'`; exit code: `0`; result: 2 passed, 1 warning (alembic path_separator deprecation); duration: `1.05` seconds
- command: `cd /data/symgov && /usr/bin/time -p sh -c 'PYTHONPATH=backend pytest tests/test_profile_subscriptions.py tests/test_admin_llm_management_routes.py -q'`; exit code: `0`; result: 12 passed, 52 warnings (FastAPI on_event deprecation warnings); duration: `9.68` seconds
### F0.6 Stage 2 t_fd69d308 (inherited; not rerun in CP0)
- command: `PYTHONPATH=backend pytest tests/test_openclaw_sync.py -q`; exit code: `0`; result: 3 passed; duration: `0.89` seconds
- command: `PYTHONPATH=backend pytest tests/test_llm_usage_migration.py -q`; exit code: `0`; result: 2 passed, 1 warning (alembic path_separator deprecation); duration: `1.33` seconds
- command: `PYTHONPATH=backend pytest tests/test_profile_subscriptions.py -q`; exit code: `0`; result: 8 passed, warnings only (FastAPI on_event deprecation); duration: `7.46` seconds
### F0.6 replacement final t_17d6cbdf (inherited; not rerun in CP0)
- command: `pytest tests/test_openclaw_sync.py -q --override-ini=pythonpath=backend`; exit code: `0`; result: 3 passed; duration: `0.88` seconds
- command: `pytest tests/test_llm_usage_migration.py -q --override-ini=pythonpath=backend`; exit code: `0`; result: 2 passed, 1 warning; duration: `1.07` seconds
- command: `pytest tests/test_profile_subscriptions.py -q --override-ini=pythonpath=backend`; exit code: `0`; result: 8 passed, warnings only; duration: `7.25` seconds
- command: `./scripts/test-backend.sh`; exit code: `0`; result: 1591 passed, 3 skipped, 3 deselected; duration: `68.53` seconds
- command: `python -m json.tool openclaw-agents.manifest.json >/dev/null`; exit code: `0`; result: ; duration: `0.03` seconds
- command: `python -m compileall -q backend/symgov_backend`; exit code: `0`; result: ; duration: `0.03` seconds

Additional inherited gate records:

- F0.5 `t_4e80d006`: compile gate passed with exit 0 using a temporary bytecode cache; isolated Alembic head gate returned `20260808_0027 (head)`; tracked and all 13 untracked whitespace gates passed; `git diff --check` passed; no repository edits by that run.
- F0.5 `t_66ecd375`: post-disposition cleanup/identity PASS; opening/closing identity, dirty manifest and changed-path hashes matched; no repository or temporary-namespace drift.
- F0.6 `t_fd69d308`: literal `APPROVE`; exact-snapshot identity and required hashes matched; inherited tests included `3 passed`, `2 passed` plus one Alembic deprecation warning, and `8 passed` with FastAPI warnings.
- F0.6 `t_17d6cbdf`: literal `PASS`; canonical backend wrapper returned `1591 passed, 3 skipped, 3 deselected`; focused suites returned `3 passed`, `2 passed` plus one warning, and `8 passed` with warnings; compile and JSON checks passed. Initial direct pytest attempts without the repository's Python path failed with `ModuleNotFoundError: symgov_backend`, then the exact repository-compatible reruns passed; this is preserved as execution evidence, not hidden.

## 6. Migration ownership

- Current Alembic state: one sole head, `20260808_0027`.
- No new migration was created, assigned, upgraded, downgraded, or applied by CP0.
- The unfinished canonical Catalog identity work retains the next migration slot conceptually. Symbol Set organization work must not claim a revision until that predecessor work is explicitly completed or rebased and the live head is rechecked.
- Stage 1 must begin with a fresh migration-ownership reconciliation; this note does not authorize a migration or database write.

## 7. Runtime, deployment and no-side-effect boundary

This is a local repository checkpoint only. The following were not performed or authorized: push, deployment, production activation, Alembic upgrade/downgrade, database mutation, service or gateway restart/stop, retained OpenClaw-config mutation, credential change/rotation, publication, withdrawal, external message, or runtime configuration change. No Stage 1/F1/F2 implementation or dispatch occurred. No existing dirty worktree path was altered.

## 8. Residual risks and gaps

- F0.4 source completion does not prove deployment verification; the backlog records its deployment-activation boundary as a residual. F2.4 crash-proof automatic delivery also remains residual work.
- The retained OpenClaw configuration still contains the direct Libby Telegram route; it is parked historical/runtime-cleanup evidence under the clarified Hermes-only scope and must not be mutated by this checkpoint.
- The worktree is intentionally dirty with preserved F0.5/security and later OpenClaw-decommission changes. The current live manifest above supersedes older 17-modified-path historical snapshots.
- The resume and related planning artifacts contain historical embedded status/hash prose; frozen files were not edited, and their verified current hashes are recorded above.
- F0.7 has no controlling definition and remains UNDEFINED / NOT SPECIFIED.
- Inherited review/test records include environment-corrected initial import attempts and unchanged deprecation warnings; no current CP0 command failure was observed.
- A fresh independent CP0 review is still required. A local checkpoint commit is also required for CP0 acceptance, but no explicit local commit authority is available in this session.

## 9. Approved future execution lane

After CP0 is independently reviewed and terminally accepted, fresh Symbol Set implementation must use the serialized chain:

`one implementation card -> fresh Stage 1 specification review -> fresh Stage 2 security/code-quality review -> final verification`

That future lane is not created or dispatched by this CP0 session. The old `t_630bd74e` F1 card must not be substituted for the next lane.

## 10. Next controlling plan path

`/data/symgov/docs/plans/2026-08-08-symbol-set-management-implementation-plan.md`, Section 7, **“Stage 1 — organization schema and invariant services”**.

Section 7's outcome is additive tenant identity storage and deterministic invariants, still disabled at runtime. It explicitly excludes project, Symbol Set, release, private-symbol, telemetry and agent tables from this first stage.

## 11. CP0 acceptance state at immutable-note creation

The controlling hashes, repository source snapshot, preserved dirty/untracked manifest, F0.5/F0.6 evidence, F0.7 undefined status, sole migration head, exact current checks, inherited test evidence, residual risks, runtime boundary and next controlling path are recorded above. The remaining gates are a fresh independent read-only CP0 review and an authorized local checkpoint commit containing only this note. Until both are satisfied, CP0 must remain blocked and no downstream card may be dispatched.
