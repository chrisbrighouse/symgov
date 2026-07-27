# F0.1 — Repair and Codify the Test Baseline

> **For Hermes:** Implement this goal only. It is a verification-infrastructure slice, not an opportunity to change product behaviour. Use the durable Symgov Kanban/Cody lane with its review gates unless Chris explicitly selects direct implementation. Request independent review before commit.

**Parent backlog:** `docs/plans/2026-07-26-symgov-trial-readiness-implementation-backlog.md`

**Goal:** Make Symgov’s backend, frontend and Langfuse test partitions repeatable from a clean environment, repair known stale fixtures, and provide commands that reliably distinguish product regressions from test-environment failures.

**Architecture:** Add an explicit test dependency layer on top of production backend requirements, centralize repeatable commands in repository scripts, and keep special-path/integration partitions explicit. Repair fake service-user objects to satisfy the current subscription-aware service-user contract without changing the published-feedback behaviour that later backlog goal F0.4 will deliberately redesign.

**Tech Stack:** pytest, uv, FastAPI/Starlette TestClient, Node’s built-in test runner, Vite.

---

## 1. Current-state evidence

1. `backend/requirements.txt` contains runtime dependencies but no pytest/TestClient dependency declaration.
2. The current installed FastAPI/Starlette TestClient raises during collection unless `httpx2` is present.
3. The repository has no `pytest.ini`, `pyproject.toml` or `conftest.py` defining suite boundaries.
4. `package.json` has build/dev/publish scripts but no test scripts.
5. Frontend Node tests are six files under `frontend/src/*.test.js`.
6. The Langfuse POC requires `PYTHONPATH=langfuse-poc/scripts` and is not part of the main backend import root.
7. Known stale fixtures:
   - `tests/test_published_feedback_service.py:64` creates an Ed service user with only `id`; subscription-aware resolution now requires at least an email-compatible user object.
   - `tests/test_published_symbol_review_workflow.py:63-67` creates the same incomplete Ed service user.
   - the latter fake session has no `rollback`, while the current route error path calls it.
8. Audit runs found:
   - auth/subscription/admin/profile: 66 passed;
   - frontend Node: 68 passed;
   - review/lifecycle subset: three stale-fixture failures;
   - published feedback/Catalog support: two stale-fixture failures;
   - focused email outbox after AgentMail work: 9 passed.
9. A broad ad-hoc command using only production requirements failed collection because test dependencies and the Langfuse import root were missing. A later system-environment run exceeded ten minutes, so a single unbounded command is not yet a useful per-goal gate.

---

## 2. Scope

### In scope

- Explicit, reproducible Python test dependencies.
- Repair of the stale service-user and fake-session fixtures reported above.
- Named commands for:
  - fast/main backend tests;
  - full backend tests with a documented timeout expectation;
  - Langfuse POC tests;
  - frontend Node tests;
  - isolated frontend production build.
- Identification and documentation of tests requiring external `/data` workspaces, services or unusually long execution.
- A short contributor-facing explanation of which partition to run per implementation goal.
- Verification that commands work from repository root in a clean `uv` environment.

### Explicitly out of scope

- Changing published feedback so review requests leave the symbol published. That governance correction belongs to F0.4.
- Refactoring product code merely to make fake sessions easier to maintain, unless the existing interface is impossible to test without such a change.
- Changing production dependency versions.
- Adding a JavaScript test framework when Node’s built-in runner already executes the current tests.
- Baking an exact expected test count into permanent policy.
- Making external OpenClaw workspaces part of the portable unit-test baseline.

---

## 3. Required design decisions

### 3.1 Python test dependencies

Create `backend/requirements-test.txt` containing only test/development additions and referencing or being layered with `backend/requirements.txt` through the documented `uv` command. At minimum it must pin compatible ranges for `pytest` and the TestClient dependency actually required by the installed Starlette version (`httpx2` in the verified environment). Do not guess the package name: prove the clean-environment import and one route test before finalizing.

Preferred invocation pattern:

```bash
PYTHONPATH=backend uv run \
  --with-requirements backend/requirements.txt \
  --with-requirements backend/requirements-test.txt \
  python -m pytest <targets> -q
```

If `uv` does not support multiple `--with-requirements` options in the installed version, create one test requirements file that includes the runtime file using a standard `-r requirements.txt` relative reference and verify resolution from repository root.

### 3.2 Repository commands

Add narrow scripts rather than a monolithic shell pipeline that hides the failing partition. Preferred files:

- `scripts/test-backend.sh`
- `scripts/test-frontend.sh`
- `scripts/test-langfuse-poc.sh`
- `scripts/build-frontend-isolated.sh`

Each script must:

- use `set -eu` (and `pipefail` where supported/needed);
- resolve repository root from its own location;
- accept optional backend pytest targets/arguments where useful;
- avoid writing generated artifacts into tracked `dist/` during verification;
- return the underlying command’s non-zero exit code;
- print no credentials or protected environment values.

Add package scripts only as thin conveniences, for example `test:frontend` and `build:isolated`; do not make `npm test` run Python.

### 3.3 Test partition classification

During implementation, time the test files or use pytest duration reporting to identify the source of the ten-minute run. Classify tests into:

1. **portable main** — no live service or external workspace required;
2. **external-workspace/integration** — depends on `/data/.openclaw/...`, Docker, network or another live component;
3. **Langfuse POC** — separate import root;
4. **frontend Node**;
5. **frontend build**.

Use pytest markers only if they improve selection and can be applied accurately. Do not mark a failing or slow test as optional merely to obtain green output. If a test claims to be portable but hangs because of a defect, record and fix the defect or create a follow-up blocker.

### 3.4 Fixture repair

Use one small helper/factory where it reduces repeated drift, but avoid a broad fake-ORM framework.

The Ed service-user fixture must include the attributes consumed by current code, expected to include:

```python
SimpleNamespace(
    id=ED_USER_ID,
    email="ed@symgov.local",
    display_name="Ed",
    is_active=True,
    deleted_at=None,
)
```

Add `rollback()` state tracking to fake sessions where route error handling requires it. Confirm the exact required attributes from current `service_users.py` and `subscriptions.py`; do not blindly copy this example.

The existing assertion that a review request changes `published` to `review` remains unchanged in this goal so the baseline describes current behaviour. Add a comment linking it to F0.4 if needed, rather than silently correcting product behaviour in test-infrastructure work.

---

## 4. Implementation tasks

### Task 1: Prove the clean TestClient dependency contract

**Files:** inspect `backend/requirements.txt`, create a temporary clean `uv` invocation only; no product edits yet.

**Steps:**
1. Run one focused route test with production requirements plus `pytest`; capture the TestClient dependency error.
2. Re-run with the candidate `httpx2` dependency.
3. Confirm collection and test execution succeed.
4. Record the compatible package/version range selected for the test requirements file.

**Gate:** one auth/profile route test passes in the clean `uv` environment.

### Task 2: Add the explicit test requirements file

**Files:**
- Create: `backend/requirements-test.txt`
- Modify: `backend/README.md`

**Steps:** add only the verified test dependencies; document the layered `uv` invocation; verify a route test and a non-route service test.

### Task 3: Repair the published-feedback service fixtures

**Files:**
- Modify: `tests/test_published_feedback_service.py`

**Steps:** inspect current service-user resolution; add the smallest realistic Ed user fixture; run the file; confirm all tests pass without product-code changes.

### Task 4: Repair the published-symbol route fixture

**Files:**
- Modify: `tests/test_published_symbol_review_workflow.py`

**Steps:** add required Ed user fields and rollback support; preserve the current behaviour assertions; run the file. If the external Ed workspace test cannot run portably, classify it explicitly rather than deleting or silently skipping it.

### Task 5: Measure and classify the backend suite

**Files:** potentially create `pytest.ini` only if markers are justified.

**Steps:**
1. Run tests with duration reporting and a generous but bounded outer timeout.
2. Identify any live external dependency, sleep, polling loop or hang.
3. Mark/document only genuinely external tests.
4. Define `portable main` and `full/integration` commands.
5. Re-run each partition independently.

**Gate:** every collected test belongs to a documented partition; no unexplained hang remains.

### Task 6: Add repository verification scripts

**Files:**
- Create: `scripts/test-backend.sh`
- Create: `scripts/test-frontend.sh`
- Create: `scripts/test-langfuse-poc.sh`
- Create: `scripts/build-frontend-isolated.sh`
- Modify: `package.json` only for thin frontend conveniences

**Steps:** implement root resolution and isolated output; make the build wrapper
reject all CLI arguments so output customization is available only through the
validated `SYMGOV_BUILD_OUT_DIR` environment contract; execute every script from
repository root and from one different working directory.

### Task 7: Document the quality-gate matrix

**Files:**
- Modify: `README.md`
- Modify: `backend/README.md`
- Update parent backlog command examples if implementation proves them wrong.

**Matrix:**
- backend-only domain change: focused test + portable main;
- backend route/auth change: focused route matrix + portable main;
- frontend change: relevant Node tests + isolated build;
- agent/external workspace change: focused portable test + named integration partition;
- Langfuse POC change: POC partition;
- migration change: focused tests + Alembic single-head/disposable upgrade checks.

### Task 8: Independent review and commit

Run:

```bash
git diff --check
python3 -m py_compile <changed-python-files>
./scripts/test-backend.sh
./scripts/test-frontend.sh
./scripts/test-langfuse-poc.sh
./scripts/build-frontend-isolated.sh
```

Request independent review focused on dependency hygiene, accidental product-behaviour changes, false skips/markers, script portability and whether the partitions can hide regressions. Correct blocking findings, then commit exact files only.

---

## 5. Acceptance criteria

- [ ] A clean `uv` environment can collect and run FastAPI TestClient tests without ad-hoc undeclared packages.
- [ ] The known stale Ed service-user fixtures pass against the current subscription-aware code.
- [ ] No product behaviour was changed to repair the test baseline.
- [ ] Portable backend, external/integration, Langfuse POC, frontend Node and frontend-build partitions are explicit.
- [ ] The source of the previous ten-minute timeout is identified; it is fixed or assigned to a named bounded partition with a follow-up issue.
- [ ] No test is skipped or marked external merely because it fails.
- [ ] Frontend verification does not overwrite tracked production `dist/` output.
- [ ] Commands work from repository root and do not depend on the active Hermes Python environment.
- [ ] Documentation states which quality gates apply to each kind of change.
- [ ] `git diff --check` and independent review pass.

---

## 6. Risks and controls

- **Risk:** Fixture repair accidentally blesses the unsafe unpublication behaviour.
  **Control:** preserve current assertions in F0.1 and link explicitly to F0.4.

- **Risk:** Test markers hide failures.
  **Control:** require evidence of a real external dependency and provide a command that still runs every marked test.

- **Risk:** Test dependencies drift independently from runtime FastAPI/Starlette.
  **Control:** compatible bounded ranges plus a clean-environment TestClient smoke test.

- **Risk:** A convenience script mutates production assets.
  **Control:** isolated Vite output outside the repository by default.

- **Risk:** The full suite remains too slow for every small goal.
  **Control:** focused + portable-main gate per goal, with full/integration gate at phase checkpoints; never omit the full gate before release/trial.

---

## 7. Completion handoff

```text
Backlog goal: F0.1 Repair and codify the test baseline
Implemented: <dependency file, fixture repairs, scripts, partition docs>
Verification: <exact command results and durations>
Known external tests: <list and prerequisites>
Product behaviour changed: no
Commit(s): <local commits; pushed or not>
Workspace: <branch and git status>
Next goal: F0.2 Split workspace authorization by operation
```

### Fresh-session implementation prompt

```text
Implement F0.1 only in /docker/openclaw-hz0t/data/symgov.
Read:
- docs/plans/2026-07-26-symgov-trial-readiness-implementation-backlog.md
- docs/plans/2026-07-26-f0-1-test-baseline-spec.md
- current git status/log and the exact current test/product code

This is spec-driven, not strict-TDD. Prove the clean TestClient dependency first,
then repair only stale test infrastructure and codify repeatable test partitions.
Do not change the current published-feedback/unpublication behaviour; F0.4 owns that
governance correction. Do not hide failures with unjustified skips. Run every
specified verification command, request independent review, and stop at F0.1's
completion gate. Preserve unrelated work. Do not push, deploy, migrate production,
restart public services, publish/withdraw symbols or alter live gateways.
```
