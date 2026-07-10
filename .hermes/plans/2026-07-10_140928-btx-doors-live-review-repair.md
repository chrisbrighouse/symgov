# BTX Doors Live Review Repair Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make a `Doors.btx` submission deterministically produce one visible, PNG-backed review child for every converted door symbol, preserve BTX provenance/metadata, classify each child as Architectural, and make queue state accurately reflect live processing.

**Architecture:** Keep BTX on the established Scott → Vlad → Libby → `raster_split_review` path. Vlad is the single producer of the persisted `ValidationReport.derivative_manifest`; Workspace materializes `review_split_items` only from that manifest. Treat the library-level Tracy/provenance case separately from the child-symbol review case so a generic/provenance card cannot masquerade as the Doors review UI.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL, MinIO/S3 attachment storage, Python runners, Vite/React, pytest.

---

## Evidence gathered from the live 10 July submission

This is a planning-only investigation. No live records were changed.

- Two submissions exist: package `0086` at 13:42 UTC and package `0087` at 13:59 UTC. Both are titled `Doors.btx` and retain `01-Doors.btx` in `raw_object_key`.
- `0086`’s Vlad queue completed but persisted a `pass` validation report with **zero** derivative children and no review recommendation. Its five Libby cards are separate `btx_extracted_symbol` records, not materialized review children. Their raw classifications were `unclassified_symbol / instrumentation`.
- `0087` has a valid queued Vlad item (`61e2f5bb-575c-5fee-a305-003aa004b943`) and a completed Tracy provenance item, but no validation report yet. It therefore cannot yet have BTX-derived review children.
- The only current review cases associated with the two submissions are Tracy provenance cases in `classification_review`, with zero split items. The card labelled `0087-01` is therefore not a BTX child review case; its default valve is fallback rendering for a generic/provenance record with no preview asset.
- The checked-in current `scripts/run_vlad_validation.py` does generate `derivative_manifest.children`, PNG previews, SVG/DXF/PNG assets, and `raster_split_review`, but that logic was not used for the completed `0086` report. The running API container mounts `/data/symgov`, but it has been up for 24 hours; dispatch lifecycle and the live worker task need explicit health proof before relying on automatic draining.
- The live config enables direct agent workers and includes Vlad, but `run_agent_queue_worker()` can terminate if `drain_agent_queues()` raises outside its per-item error handling; it exposes no worker heartbeat/status or task-failure log. This is the most likely explanation for `0087` remaining queued.
- `catalog_taxonomy.py` already supports `architectural → Architectural`, but `filename_inference.py` lacks the Architectural prefix and Libby defaults unknown symbols to instrumentation. BTX child titles such as `Single Door`, `Double Door`, `Sliding Door`, `Pocket Door`, and `Bi-fold Door` need a deterministic architectural semantic rule, not an LLM/default fallback.

## Acceptance criteria

For one new or replayed `Doors.btx` package:

1. Scott completes, Vlad completes, and the UI/DB queue states match the file-backed runtime states.
2. Vlad persists one `validation_reports` row with `decision = escalate`, `current_stage = raster_split_review`, and exactly five `derivative_manifest.children`.
3. All fifteen generated assets are attached/uploaded; each child has a persisted PNG `attachment_object_key`, the original BTX source key, title/ordinal/internal-name metadata, and its SVG/DXF derivatives.
4. Workspace creates exactly one BTX child-review case containing five open `review_split_items`, displayed `NNNN-1` through `NNNN-5`; each child preview endpoint returns image/png and the review UI shows the actual door drawing rather than the valve fallback.
5. Every child classification is `Doors` / `Architectural`, with a trace identifying BTX subject/semantic evidence. `Doors` is a canonical enumerated catalog category and `Architectural` is available in reviewer discipline choices.
6. Tracy’s library-level provenance case remains separately identifiable and does not replace, hide, or supply the preview for the child review case.
7. Existing bad `0086` artifacts remain auditable but are superseded/closed; the live recovery produces no duplicate open review children.

## Task 1: Add worker-liveness diagnostics and make the loop failure-tolerant

**Objective:** Prevent a single unexpected drain-level exception from silently leaving Vlad items indefinitely queued, and give operators a concrete way to distinguish queued, processing, and dead-worker states.

**Files:**
- Modify: `backend/symgov_backend/agent_queue_worker.py:509-521`
- Modify: `backend/symgov_backend/app.py:83-116`
- Modify: `backend/symgov_backend/routes/workspace.py` (or the existing operational health route)
- Test: `tests/test_agent_queue_state_machine.py` (create if missing)
- Test: `tests/test_workspace_agent_queue_routes.py` or the established agent-workspace route test

**Step 1: Write failing worker-loop tests**

Cover a drain-cycle exception and assert that a subsequent cycle still invokes queue processing. Add a second test that verifies an operator-visible health payload reports configured agents, last successful cycle, last error, and whether the background task is running/done.

```python
async def test_agent_worker_survives_a_failed_drain_cycle(monkeypatch):
    calls = []
    def drain(_config):
        calls.append(len(calls))
        if len(calls) == 1:
            raise RuntimeError("synthetic worker failure")
    # run the worker with a controllable stop event; assert it reaches call two
```

**Step 2: Run the focused tests and confirm RED**

```bash
PYTHONPATH=backend pytest -q tests/test_agent_queue_state_machine.py -k 'survives or health'
```

Expected: failure because there is no resilient loop/health state.

**Step 3: Implement the smallest robust worker state holder**

- Store a lightweight worker state object on `app.state`: configured agents, `last_started_at`, `last_success_at`, `last_error`, `last_result`, and task status.
- Wrap each `drain_agent_queues`/`process_agent_queues_once` call in `try/except Exception`; log the exception, update `last_error`, sleep for the configured interval, then continue.
- Do not mark a failed item complete and do not swallow the per-item error details already returned by `process_agent_queue_once`.
- Expose the state through an authenticated admin/workspace operation endpoint, not through the public API.

**Step 4: Run focused tests and verify GREEN**

```bash
PYTHONPATH=backend pytest -q tests/test_agent_queue_state_machine.py -k 'survives or health'
```

Expected: pass.

**Step 5: Commit**

```bash
git add backend/symgov_backend/agent_queue_worker.py backend/symgov_backend/app.py backend/symgov_backend/routes/workspace.py tests/
git commit -m "fix: keep Symgov agent worker alive after cycle failures"
```

## Task 2: Lock the BTX durable review contract with persistence-level tests

**Objective:** Prove the stored report—not merely the in-memory converter result—creates five preview-bearing review children.

**Files:**
- Modify: `scripts/run_vlad_validation.py:1768-1801, 2289-2347`
- Test: `tests/test_btx_integration.py`
- Test: `tests/test_workspace_split_items.py`
- Test: `tests/test_workspace_asset_preview.py`

**Step 1: Write failing integration tests**

Use the handoff `Doors.btx` fixture and fake persistence/storage bridge to assert:

```python
assert report["validation_status"] == "escalate"
assert report["report_json"]["review_recommendation"]["current_stage"] == "raster_split_review"
assert len(report["normalized_payload_json"]["derivative_manifest"]["children"]) == 5
for child in children:
    assert child["attachment_object_key"].endswith(".png")
    assert child["visual_assets"]["preview"]["format"] == "png"
    assert {asset["format"] for asset in child["assets"]} == {"svg", "dxf", "png"}
```

Then feed that persisted report through `ensure_split_items` and assert five items, package labels `0087-1` through `0087-5`, and real preview URLs.

**Step 2: Run to establish RED if any persistence field is absent**

```bash
PYTHONPATH=backend pytest -q tests/test_btx_integration.py tests/test_workspace_split_items.py tests/test_workspace_asset_preview.py
```

**Step 3: Implement only necessary contract repairs**

- Keep `raster_split_review`; do not add a BTX-only stage, because the existing human-visible filter and child-decision routing are intentionally based on the established split-review stage.
- Ensure the final persisted `validation_report.report_json` includes the review recommendation. At present the local report JSON copies decision/confidence/target/defects/trace but not `review_recommendation`; add it if the review-case bridge consumes it there.
- Ensure `derivative_manifest.children` hold the final persisted PNG attachment key and a child-level `visual_assets.preview` plus `derivatives`. Do not rely on a local `path` from the Vlad runtime as browser evidence.
- Preserve BTX source key, ordinal, subject, internal name, warnings, dimensions, SHA-256, and all three derivative formats in child payloads.

**Step 4: Run focused tests and verify GREEN**

```bash
PYTHONPATH=backend pytest -q tests/test_btx_integration.py tests/test_workspace_split_items.py tests/test_workspace_asset_preview.py
```

**Step 5: Commit**

```bash
git add scripts/run_vlad_validation.py tests/test_btx_integration.py tests/test_workspace_split_items.py tests/test_workspace_asset_preview.py
git commit -m "fix: persist BTX child previews for split review"
```

## Task 3: Make BTX door classification deterministic and taxonomy-compatible

**Objective:** Classify BTX door subjects as architectural symbols before optional LLM enrichment, instead of allowing generic fallback instrumentation.

**Files:**
- Modify: `scripts/run_vlad_validation.py:2176-2213`
- Modify: `scripts/run_libby_classification.py:476-576`
- Modify: `backend/symgov_backend/filename_inference.py:7-20, 81-96`
- Modify: `backend/symgov_backend/catalog_taxonomy.py` to add the canonical `Doors` category and its normalizations
- Modify: any taxonomy/category API fixture that asserts the enumerated list
- Test: `tests/test_btx_integration.py`
- Test: `tests/test_filename_inference.py`
- Test: `tests/test_libby_symbol_vision.py` or a new `tests/test_libby_btx_classification.py`

**Step 1: Write failing tests**

Assert every `Doors.btx` child queue payload includes its BTX subject and an explicit semantic hint, and that Libby produces normalized display values:

```python
assert artifact["category"] == "Doors"
assert artifact["discipline"] == "Architectural"
assert "btx_subject_architectural_match" in trace_codes(artifact)
```

Also test `infer_filename_metadata("Architectural-Doors.btx")` returns `Architectural`, without changing generic `Doors.btx` filename confidence by itself.

**Step 2: Run to establish RED**

```bash
PYTHONPATH=backend pytest -q tests/test_btx_integration.py tests/test_filename_inference.py tests/test_libby_symbol_vision.py
```

**Step 3: Implement the semantic rule at the source**

- Add `Architectural` to filename discipline prefixes for explicit filenames only.
- In `build_btx_libby_queue_item`, pass a structured `btx_subject`, `btx_annotation_type`, and a deterministic `classification_hints` payload instead of expecting Libby to infer the domain from a hash-like `candidate_symbol_id`.
- In Libby, evaluate authoritative BTX subject words before generic defaults. Match `door`, `bi-fold door`, `double door`, `pocket door`, and `sliding door` case-insensitively to `category = "Doors"`, `discipline = "Architectural"`, architectural family/industry fields, and an evidence-trace entry.
- Maintain unknown BTX subjects as provisional/human-reviewed; do not classify all BTX assets as architectural.
- Normalize review-facing values through the existing catalog taxonomy, add canonical `Doors` to `CATALOG_CATEGORY_ORDER` and its normalization map, and upsert/remember `Doors` and `Architectural` into review property options.

**Step 4: Run focused tests and verify GREEN**

```bash
PYTHONPATH=backend pytest -q tests/test_btx_integration.py tests/test_filename_inference.py tests/test_libby_symbol_vision.py
```

**Step 5: Commit**

```bash
git add scripts/run_vlad_validation.py scripts/run_libby_classification.py backend/symgov_backend/filename_inference.py backend/symgov_backend/catalog_taxonomy.py tests/
git commit -m "fix: classify BTX door symbols as architectural"
```

## Task 4: Separate BTX library provenance review from child-symbol review in the UI

**Objective:** Prevent a Tracy provenance card such as `0087-01` from being mistaken for the five actual door review cards or showing a default valve preview.

**Files:**
- Modify: `backend/symgov_backend/routes/workspace.py:3599+` and review response builders
- Modify: `frontend/src/App.jsx` (review case/card rendering and empty preview state)
- Test: `tests/test_workspace_split_items.py`
- Test: `tests/test_workspace_review_cases.py` (create if missing)
- Test: frontend test location already used by this repository, if configured

**Step 1: Write failing API/UI contract tests**

- A `classification_review` provenance case with no `attachment_object_key` must return an explicit `sourcePreviewUnavailable`/library provenance state, not a symbol preview URL.
- A `raster_split_review` BTX case must return five children whose parent filename is `01-Doors.btx`, labels are `0087-1` … `0087-5`, and preview URLs point at their unique PNG object keys.
- The UI must label the parent/library provenance case distinctly and must not render the default valve as if it were source evidence.

**Step 2: Run tests to establish RED**

```bash
PYTHONPATH=backend pytest -q tests/test_workspace_split_items.py tests/test_workspace_review_cases.py
cd frontend && npm test -- --run
```

Use the repository’s actual frontend test command if `package.json` specifies another script.

**Step 3: Implement the minimum UI/API distinction**

- Preserve the existing child-review card model; do not create a parallel BTX review component.
- Add `reviewKind`/`sourceClassification` metadata so a provenance-only card says “Library provenance review — no converted symbol preview” and links to the original BTX download/source metadata.
- Only request/render `previewUrl` when a child has a persisted attachment key. Render an explicit unavailable state otherwise; never substitute seeded valve imagery.
- Display child BTX title, ordinal, original file, source SHA-256, warning count, classification/category/discipline, and SVG/DXF download indicators in the review detail.

**Step 4: Run tests and build**

```bash
PYTHONPATH=backend pytest -q tests/test_workspace_split_items.py tests/test_workspace_review_cases.py
cd frontend && npm run build
```

**Step 5: Commit**

```bash
git add backend/symgov_backend/routes/workspace.py frontend/src/App.jsx tests/
git commit -m "fix: distinguish BTX provenance cases from symbol reviews"
```

## Task 5: Deploy safely and recover the two live Doors submissions

**Objective:** Put the verified worker code into the active process, preserve audit history, and produce one clean reviewable Doors package.

**Files:**
- Modify: `docs/btx-submission-workflow.md`
- Modify: `docs/restart-notes/2026-07-10-btx-review-visibility-fix.md`
- Modify: deployment/runbook documentation if the service restart procedure lives elsewhere

**Step 1: Capture pre-deployment state**

Record IDs, statuses, and reports for `0086`/`0087` without deleting rows. In particular retain:

- `0086` Vlad queue `083e9485-811c-5c95-83f4-1602b85f62f8` and its invalid pass report;
- `0087` queued Vlad item `61e2f5bb-575c-5fee-a305-003aa004b943`;
- both Tracy provenance cases.

**Step 2: Run all relevant verification before service restart**

```bash
cd /data/symgov
PYTHONPATH=backend pytest -q \
  tests/test_btx_integration.py \
  tests/test_agent_queue_state_machine.py \
  tests/test_workspace_split_items.py \
  tests/test_workspace_asset_preview.py \
  tests/test_workspace_review_cases.py \
  tests/test_filename_inference.py \
  tests/test_libby_symbol_vision.py
cd frontend && npm run build
```

Then run the full suite. Treat any unrelated suite failure as a separately documented release blocker; do not call the deployment clean without evidence.

**Step 3: Deploy/restart the bind-mounted API process and prove imports**

Use the approved Symgov deployment mechanism to restart `symgov-hermes-api`; do not assume a bind mount hot-reloads Python. Verify:

```bash
docker exec symgov-hermes-api python -c \
  "import symgov_backend.agent_queue_worker as w; print(w.__file__)"
docker exec symgov-hermes-api python -c \
  "from pathlib import Path; print(Path('/data/symgov/scripts/run_vlad_validation.py').read_text().count('btx_library_expansion'))"
```

Then call the new worker-health endpoint and require a fresh successful cycle timestamp plus no unhandled worker-task failure.

**Step 4: Recover live data without hiding audit history**

- Do not edit `0086`’s invalid validation report in place and do not delete its classification/provenance history.
- Mark the obsolete `0086` processing/review attempt superseded through an auditable operator action and close its generic provenance review only if policy allows.
- After the deployment proof, let the repaired worker process `0087` once, or create one explicitly audited replacement queue item for the same `0087` intake record if it was previously claimed/stuck.
- Do not manually create review split items. They must originate from the new persisted Vlad validation report.
- If the original source is resubmitted instead, use the resulting new package code and mark both prior packages superseded; never merge derivatives across package IDs.

**Step 5: Perform end-to-end live acceptance checks**

Query DB/storage/API and inspect the browser:

1. `0087`’s Vlad item transitions `queued → running → completed` with matching runtime JSON and DB state.
2. Its report has five children; MinIO has 15 BTX derivative objects; every child attachment key exists.
3. `GET /api/v1/workspace/review-cases` returns one `raster_split_review` case for the library with five child responses.
4. Each `children/preview?object_key=...` returns `200`, `Content-Type: image/png`, and a different door image.
5. The review UI shows five symbols and the metadata: `Single Door`, `Double Door`, `Sliding Door`, `Bi-fold Door`, `Pocket Door`; original `01-Doors.btx`; BTX ordinal/source SHA; `Doors / Architectural`.
6. The provenance case is visibly distinct and cannot show a default valve as the original file.

**Step 6: Update documentation and commit**

Add worker-liveness checks, stale-worker recovery, BTX classification semantics, and the provenance-vs-child review distinction to the BTX workflow doc and restart note.

```bash
git add docs/
git commit -m "docs: add BTX live-review recovery runbook"
```

## Risks and decisions

- **Do not repair the user-visible symptom by manually assigning five review rows.** That would bypass storage/persistence and fail again for the next BTX library.
- **Do not broaden all BTX compatibility claims.** Only the supplied Version 1 stamp-snapshot structure and the demonstrated Door subjects are covered.
- **Use `raster_split_review`, not `btx_library_review`.** The former is already human-visible and has child-decision routing; a novel stage caused/causes invisibility unless every filter and API path is extended.
- **Taxonomy decision confirmed:** Add `Doors` as a canonical enumerated catalog category and classify BTX door subjects as `Doors / Architectural`. Preserve this exact capitalization across Libby, reviewer property options, review payloads, and publication handoff.
- **Deployment is necessary for existing API processes.** The mounted repo proves the source path is available, not that a long-lived Python process has loaded its newer code.
