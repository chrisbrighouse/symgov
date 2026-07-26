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
- Remote relation immediately after controlling commit: `main` was two commits ahead of `origin/main`
- Push status: neither local commit was pushed; no push is authorized by this note
- Preserved dirty/untracked application files: none

## Approved execution lane

Use the durable Symgov Kanban/Cody lane:

1. serialized Cody implementation of F0.1 only;
2. fresh Stage 1 specification review;
3. fresh Stage 2 security/code-quality review;
4. final focused and partitioned verification;
5. local commit only after review findings are closed.

Do not start F0.2 until F0.1 passes its completion gate. Do not use direct implementation unless Chris explicitly changes the lane.

## Current verified facts

- `git diff --cached --check` passed before controlling-plan commit.
- The focused subscription email tests previously passed: `9 passed in 1.01s`.
- Clean temporary backend dependency installation lacks the TestClient dependency imported as `httpx2` in this environment.
- `tests/test_published_feedback_service.py` contains stale fake service users after subscription enforcement.
- The broad backend suite did not produce a bounded portable result within 600 seconds.
- The Langfuse proof-of-concept tests require their own import-path/partition contract.
- Frontend tests use Node's built-in runner rather than an `npm test` script.
- F0.1 must preserve the current unsafe feedback/unpublication assertion only as a temporary baseline; F0.4 owns its immediate governance correction before feature work.

## Runtime and migration state

- No migration was created or executed by planning.
- No service, worker, gateway or deployment was restarted.
- No symbol was published, withdrawn or changed.
- No external message was sent.

## Residual risks

- The full portable test baseline is not yet trustworthy.
- The live review-request path can still set a published revision back to `review`; F0.4 is a live governance blocker.
- Workspace authorization and actor attribution blockers F0.2–F0.3 remain open.

## Copy-ready fresh-session prompt

```text
Continue the Symgov Trial Readiness programme in:
/docke...[truncated]