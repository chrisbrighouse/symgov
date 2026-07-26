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
