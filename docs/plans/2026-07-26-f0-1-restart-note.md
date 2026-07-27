# Restart Note — Begin F0.1 Test Baseline

**Recorded:** 2026-07-26
**Programme:** Symgov Trial Readiness
**Next goal:** F0.1 — Repair and codify the test baseline
**Goal spec:** `docs/plans/2026-07-26-f0-1-test-baseline-spec.md`

## Immutable repository position

- Repository: `/docker/openclaw-hz0t/data/symgov`
- Branch: `main`
- Baseline product commit: `f5b1381` (`feat: add AgentMail subscription email delivery`)
- Controlling backlog/spec commit: `312aafeb5f495948977db1c785976a408edbb0cd`
- Initial restart-note commit: `17d2f4acf43cb2263e07cf875bc848d68663d922`
- This corrected note supersedes the truncated prompt in `17d2f4a`; use the latest commit containing this file.
- Remote relation after `17d2f4a`: `main` was three commits ahead of and zero behind `origin/main`.
- Push status: no programme commit has been pushed; this note does not authorize a push.
- Preserved dirty/untracked application files before correcting this note: none.

## Approved execution lane

Use the durable Symgov Kanban/Cody lane:

1. serialized Cody implementation of F0.1 only;
2. fresh Stage 1 specification review;
3. fresh Stage 2 security/code-quality review;
4. final focused and partitioned verification;
5. local commit only after review findings are closed.

Do not start F0.2 until F0.1 passes its completion gate. Do not use direct implementation unless Chris explicitly changes the lane.

## Exact verification evidence

### Focused subscription-email regression

Command:

```bash
PYTHONPATH=backend uv run --with-requirements backend/requirements.txt --with pytest python -m pytest tests/test_email_outbox.py -q
```

Result recorded on 2026-07-26:

```text
9 passed in 0.35s
wall duration: 1.01 seconds
exit: 0
```

### Python import/compile smoke check

Command:

```bash
python3 -m py_compile backend/symgov_backend/app.py backend/symgov_backend/email_worker.py backend/symgov_backend/settings.py
```

Result recorded on 2026-07-26:

```text
no output
wall duration: 0.04 seconds
exit: 0
```

### Planning commit whitespace gate

Command used before commit `312aafe`:

```bash
git diff --cached --check
```

Result:

```text
no output
exit: 0
```

The original run did not capture wall duration. The final handoff correction must rerun `git diff --check` with duration and record its result in the completion report; do not fabricate a historical duration.

### Known failing/unbounded baseline evidence

Clean temporary dependency command previously used:

```bash
PYTHONPATH=backend uv run --with-requirements backend/requirements.txt --with pytest python -m pytest -q
```

Observed result:

```text
collection error: ModuleNotFoundError: No module named 'httpx2'
exit: 2
```

The original invocation did not preserve a trustworthy wall duration. F0.1 must reproduce the failure in a fresh temporary environment and record the complete command, output and duration before fixing it.

Broad host-environment command previously used:

```bash
PYTHONPATH=backend pytest tests -q
```

Observed result:

```text
partial progress: ..FF.........................
terminated at timeout: 600 seconds
exit: 124
```

F0.1 must identify the slow/external partition rather than increasing or hiding the timeout.

## Current verified facts

- The focused subscription email tests pass.
- A clean temporary backend dependency installation lacks the TestClient dependency imported as `httpx2` in this environment.
- `tests/test_published_feedback_service.py` contains stale fake service users after subscription enforcement.
- The broad backend suite does not yet provide a bounded portable result.
- Langfuse proof-of-concept tests require their own import-path/partition contract.
- Frontend tests use Node's built-in runner rather than an `npm test` script.
- F0.1 may preserve the unsafe feedback/unpublication assertion only as a temporary baseline; F0.4 owns its immediate governance correction before feature work.

## Runtime and migration state

- No migration was created or executed by planning.
- No service, worker, gateway or deployment was restarted.
- No symbol was published, withdrawn or changed.
- No external message was sent.

## Residual risks

- The full portable test baseline is not yet trustworthy.
- The live review-request path can still set a published revision back to `review`; F0.4 is a live governance blocker.
- Workspace authorization and actor-attribution blockers F0.2–F0.3 remain open.

## Copy-ready fresh-session prompt

```text
Continue the Symgov Trial Readiness programme in:
/docker/openclaw-hz0t/data/symgov

Repository baseline:
- branch: main
- product baseline commit: f5b1381
- controlling backlog/spec commit: 312aafeb5f495948977db1c785976a408edbb0cd
- initial restart-note commit: 17d2f4acf43cb2263e07cf875bc848d68663d922
- use the latest committed version of docs/plans/2026-07-26-f0-1-restart-note.md,
  which supersedes the truncated prompt in 17d2f4a
- no push, deployment, production migration or public-service restart is authorized

Read first:
- docs/plans/2026-07-26-symgov-trial-readiness-implementation-backlog.md
- docs/plans/2026-07-26-f0-1-test-baseline-spec.md
- docs/plans/2026-07-26-f0-1-restart-note.md
- current git log/status and the exact files/tests named by the F0.1 spec

Execute F0.1 only through the durable Symgov Kanban/Cody lane: serialized Cody
implementation, fresh Stage 1 specification review, fresh Stage 2 security/code-quality
review, and final verification. This is spec-driven, not strict-TDD. Reproduce and time
the clean-environment httpx2 collection failure before correcting the dependency
contract. Repair only stale test infrastructure, codify bounded backend/frontend/
Langfuse partitions, and run every command required by the F0.1 completion gate.

Do not change the current feedback/unpublication product behavior in F0.1; F0.4 owns
that immediate governance correction. Do not hide failures with unjustified skips or
longer timeouts. Preserve unrelated work. Do not start F0.2. Do not push, deploy,
migrate production, restart public services, publish/withdraw symbols, send external
messages, or change gateways.

Finish by recording exact commands, outputs, exit codes and durations; changed files;
review findings and resolutions; commits; migration/runtime state; residual risks;
branch/status; and the concrete next handoff for F0.2.
```

---

## F0.1 completion report — 2026-07-27

### Outcome

F0.1 is complete and green on branch `main`. The implementation adds a bounded,
clean-environment test dependency layer; repairs stale test fixtures; separates the
portable, external-workspace, Langfuse, frontend-test and frontend-build partitions;
and documents the quality-gate matrix. Product behaviour changed: **no**. In
particular, the current published-to-review transition remains deliberately unchanged
and belongs to F0.4.

The pre-commit identity for the final gate was
`14e29cea00c05eaa43534aaa9b047f3705fa4625`. The F0.1 completion commit is local on
`main`; its immutable SHA is recorded in the Kanban completion handoff and can be read
with `git log -1 --oneline`. It has not been pushed.

### Exact committed scope

- `README.md`
- `backend/README.md`
- `backend/requirements-test.txt`
- `docs/plans/2026-07-26-f0-1-restart-note.md`
- `docs/plans/2026-07-26-f0-1-test-baseline-spec.md`
- `docs/plans/2026-07-26-symgov-trial-readiness-implementation-backlog.md`
- `package.json`
- `pytest.ini`
- `scripts/build-frontend-isolated.sh`
- `scripts/test-backend.sh`
- `scripts/test-frontend.sh`
- `scripts/test-langfuse-poc.sh`
- `scripts/test-verification-scripts.sh`
- `tests/test_daisy_rights_review_coordination.py`
- `tests/test_dxf_phase1.py`
- `tests/test_libby_duplicate_triage.py`
- `tests/test_libby_symbol_vision.py`
- `tests/test_published_feedback_service.py`
- `tests/test_published_symbol_review_workflow.py`
- `tests/test_vlad_hardening.py`
- `tests/test_zip_phase2.py`

No product-source or migration file changed.

### Final fresh verification evidence

All commands below were bounded and exited 0 unless an expected rejection exit is
stated. Durations are measured wall times from the final completion run.

| Command / gate | Working directory | Result | Wall duration |
|---|---|---|---:|
| `git diff --check` | repository root | no output; pass | 0.011 s |
| `python3 -m py_compile` on all eight changed Python test files | repository root | no output; pass | 0.050 s |
| `./scripts/test-backend.sh` | repository root | 756 passed, 3 deselected; existing FastAPI warnings only | 20.308 s |
| `./scripts/test-backend.sh --full` | repository root | portable 756 passed/3 deselected; external 2+7+3+4+9+2+1 passed; 784 backend nodes executed | 28.635 s |
| `./scripts/test-frontend.sh` | repository root | 65 passed, 0 failed/skipped | 0.311 s |
| `./scripts/test-langfuse-poc.sh` | repository root | 12 passed | 0.285 s |
| `SYMGOV_BUILD_OUT_DIR=<fresh /tmp path> ./scripts/build-frontend-isolated.sh` | repository root | Vite 7.3.6; 54 modules; external index/assets only | 1.815 s |
| `SYMGOV_BUILD_OUT_DIR=<fresh /tmp path> npm run build:isolated` | repository root | Vite 7.3.6; 54 modules; external index/assets only | 1.991 s |
| `./scripts/test-verification-scripts.sh` | repository root | partition, marker, timeout, exit, containment and argument contracts passed | 2.330 s |
| clean layered `uv` run of `tests/test_email_outbox.py -q` | repository root | 9 passed | 0.959 s |
| absolute `scripts/test-backend.sh` | `/tmp` | 756 passed, 3 deselected | 21.160 s |
| absolute `scripts/test-frontend.sh` | `/tmp` | 65 passed | 0.251 s |
| absolute `scripts/test-langfuse-poc.sh` | `/tmp` | 12 passed | 0.288 s |
| absolute zero-argument isolated build wrapper | `/tmp` | Vite 7.3.6; 54 modules; external index/assets only | 1.893 s |
| absolute `scripts/test-verification-scripts.sh` | `/tmp` | all contracts passed | 2.348 s |

The explicit negative matrix also passed. Both backend timeout variables and the
frontend, Langfuse and build timeout variables rejected representative empty, zero,
negative, fractional or nonnumeric values with exit 2 before execution (individual
probes: 0.006–0.008 s). Relative, root, repository-resolving and empty-relative build
output environments rejected with exit 2 (0.006–0.011 s). Standalone `--`, config,
positional-root, `--root`, `--outDir`, mode and arbitrary build arguments all rejected
with the same value-independent diagnostic and exit 2 before npm/Vite (0.006 s each).
No `dist-argument-probe` or repository build output appeared.

### External partitions and prerequisites

The full backend gate runs portable tests once, then seven isolated external
processes. Those processes require the managed Daisy, DXF/Scott, Libby and Ed workspace
files used by their named tests. Vlad's single external node requires the retired host
runner state: `/data/.openclaw/workspaces/vlad/run_vlad_validation.py` must remain
absent. The other four Vlad tests are repository-portable. These prerequisites were
present in the final environment and every external process passed.

### Review findings and fixes

Every actionable review finding was closed before the final immutable reviews:

1. **Lexical build-output containment could be bypassed by relative, `..`, `TMPDIR=.`
   or symlink paths.** The wrapper now requires an absolute destination, canonicalizes
   root/output paths and rejects root or any repository-resolving destination; shell
   contracts cover valid and invalid paths.
2. **Frontend, Langfuse and isolated-build wrappers lacked finite outer timeouts.**
   Each now has a configurable positive-integer timeout, fail-closed validation and
   preserved underlying/timeout exit behaviour.
3. **One Vlad host-state assertion was incorrectly portable.** Only
   `test_legacy_vlad_runner_code_is_retired` is marked external; `--full` runs it in a
   dedicated process and keeps the other four Vlad nodes portable.
4. **Any-cwd documentation showed relative commands.** Both READMEs now distinguish
   repository-root `./scripts/...` use from absolute `REPO_ROOT/scripts/...` invocation
   from another directory; final wrappers passed from `/tmp`.
5. **Backend timeout overrides accepted zero and other invalid durations.** Both
   backend timeout variables now validate positive decimal integers before pytest/uv;
   invalid input exits 2 without invoking the wrapped command.
6. **A forwarded standalone `--` could terminate Vite option parsing and defeat the
   appended canonical outDir.** The first correction rejected that terminator and
   added RED-to-GREEN containment probes.
7. **Forwarded `--config`/`-c` or positional root could load caller configuration whose
   Rollup output overrides Vite outDir.** The final fail-closed design rejects every
   build CLI argument before timeout/npm. Output selection is environment-only through
   validated `SYMGOV_BUILD_OUT_DIR`; representative argument classes have regression
   coverage.

The final fresh Stage 1 review (`t_9375d91d`) returned **PASS** with no actionable
specification gaps on the unchanged 20-path implementation snapshot. The final fresh
Stage 2 review (`t_2b887013`) returned **APPROVED** with no Critical, Important or drift
findings on that same snapshot. This completion-report edit occurred only after those
implementation reviews; all canonical gates were therefore rerun after this edit
before commit.

### Runtime, migration and side effects

- No migration was created or executed.
- No service, worker, gateway or public runtime was started, stopped, restarted,
  deployed or reconfigured.
- No symbol was published, withdrawn or otherwise mutated.
- No external email, post or message was sent.
- No push, clean, reset, stash or F0.2 work was performed.
- Verification wrote only fresh build/probe artifacts under `/tmp`, including
  `/tmp/symgov-f01-final-direct.lxRx0j`,
  `/tmp/symgov-f01-final-npm.s4OJNP` and
  `/tmp/symgov-f01-from-tmp.M1JF9l`; repository build destinations remained absent.

### Residual risks and next handoff

- F0.4 still owns the governance correction that prevents a review request from moving
  a published revision back to `review`.
- External partitions remain dependent on the managed host workspaces/files and Vlad
  retirement state described above.
- The portable suite emits existing FastAPI `on_event` deprecation warnings.
- The build wrapper intentionally accepts no CLI arguments; callers must use
  `SYMGOV_BUILD_OUT_DIR` and the documented timeout environment variable.

Next goal: **F0.2 — Split workspace authorization by operation**. Start only in a new,
explicitly authorized task after reading the parent backlog and this completion report;
do not infer any deployment, migration, restart or push authorization from F0.1.
