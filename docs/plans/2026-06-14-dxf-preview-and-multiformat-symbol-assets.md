# DXF Preview and Multi-Format Symbol Assets Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make DXF-backed symbols visible in review and catalog, support companion JPG/PNG/SVG/DXF assets as one symbol record, and prepare the catalog for future per-format downloads.

**Architecture:** Keep the original DXF as a downloadable/provenance source asset, but never depend on the browser rendering DXF directly. Promote every symbol to a small visual asset manifest with a chosen preview asset plus all available source/derivative/downloadable formats. Store the manifest in existing JSON payloads first (`normalized_payload_json`, `review_split_items.payload_json`, `symbol_revisions.payload_json`) and use `attachments`/object storage for the actual files; add a dedicated relational asset table only if JSON manifests become too hard to query.

**Tech Stack:** Python/FastAPI/SQLAlchemy/Postgres JSONB, existing object storage attachment model, ezdxf for DXF metadata and SVG/PNG derivatives, React catalog/review UI.

---

## Current verified findings

Date checked: 2026-06-14.

Relevant current code paths:

- `/data/.openclaw/workspaces/vlad/run_vlad_validation.py`
  - Direct DXF support exists.
  - `create_dxf_svg_derivative()` currently writes a local SVG manifest under `runtime_root/dxf_derivatives/<queue_item_id>/`.
  - The generated derivative is recorded in `normalized_technical_metadata.dxf_derivative` as local paths (`svg_path`, `metadata_path`), not as an object-storage `object_key`.
  - The current derivative is a conservative accessible technical SVG summary, not yet a proper CAD-rendered symbol preview.
- `/data/symgov/backend/symgov_backend/routes/workspace.py`
  - Review UI preview URLs are built from `source_object_key` / child `attachment_object_key`.
  - `/api/v1/workspace/review-cases/{id}/source/preview` downloads and returns the source object as-is.
  - `/api/v1/workspace/review-cases/{id}/children/preview?object_key=...` downloads and returns the child object as-is.
  - This works for PNG/JPG/SVG children, but not for original DXF because the frontend uses `<img>` and browsers do not display DXF as images.
- `/data/symgov/backend/symgov_backend/routes/published.py`
  - Published rows set `previewUrl` only when `payload_json.source_object_key` exists.
  - `/api/v1/published/symbols/{slug}/preview` currently serves the `source_object_key` (same fundamental DXF problem).
  - `published_symbol_row()` already exposes `downloads = payload.get("downloads") or []`, but those are just labels today, not download descriptors/endpoints.
- `/data/symgov/backend/symgov_backend/publication_handoff.py`
  - Publishing writes `symbol_revisions.payload_json.source_object_key` from classification/intake or split child attachment.
  - It does not yet write `preview_object_key`, `visual_assets`, or full multi-format asset manifests.
- `/data/symgov/frontend/src/App.jsx`
  - Review preview components (`ReviewSourceVisual`, `SplitSymbolPreview`) and catalog preview (`PublishedSymbolPreview`) all render with `<img>`.
  - This is fine if the backend points them at SVG/PNG/JPG preview assets; it will fail if pointed at raw DXF.

Conclusion: DXF ingestion works, but DXF preview is absent because derivatives are local runtime files, not object-store attachments, and review/catalog preview endpoints still point at original source objects.

## Design decisions

### 1. Use SVG/PNG/JPG as preview formats, not DXF

- Treat DXF as a source/download format.
- Use a browser-renderable preview for review/catalog:
  - Prefer companion raster image if present and confidence is high (`.jpg`, `.jpeg`, `.png` with same normalized basename).
  - Else prefer generated SVG derivative from DXF.
  - Else generated PNG derivative if SVG is incomplete or unsafe.
  - Else fallback glyph with clear `Preview unavailable`.

Note: the user mentioned "SVF"; for the browser/catalog path this plan assumes SVG unless a later CAD-specific SVF/Forge-style viewer is explicitly selected. SVG is simpler, open, and already fits Symgov UI patterns.

### 2. Represent one symbol as a manifest of assets

Add a common JSON shape wherever a symbol candidate/revision is represented:

```json
{
  "visual_assets": {
    "preview": {
      "role": "preview",
      "format": "jpg",
      "object_key": "...",
      "filename": "pump.jpg",
      "content_type": "image/jpeg",
      "source": "companion_basename_match"
    },
    "source_assets": [
      {
        "role": "source",
        "format": "dxf",
        "object_key": "...",
        "filename": "pump.dxf",
        "content_type": "application/dxf",
        "downloadable": true
      },
      {
        "role": "companion_preview",
        "format": "jpg",
        "object_key": "...",
        "filename": "pump.jpg",
        "content_type": "image/jpeg",
        "downloadable": true
      }
    ],
    "derivatives": [
      {
        "role": "generated_preview",
        "format": "svg",
        "object_key": "...",
        "filename": "pump.preview.svg",
        "content_type": "image/svg+xml",
        "derived_from_object_key": "...dxf...",
        "downloadable": false
      }
    ]
  }
}
```

Keep a compatibility shortcut too:

- `preview_object_key`
- `preview_content_type`
- `source_object_key`
- `downloads` as structured descriptors, not only strings:

```json
[
  {"label": "DXF", "format": "dxf", "object_key": "...", "filename": "pump.dxf"},
  {"label": "JPG", "format": "jpg", "object_key": "...", "filename": "pump.jpg"}
]
```

Frontend can render old string downloads and new object downloads during transition.

### 3. Companion file grouping rule

For libraries/ZIPs containing `pump.dxf` and `pump.jpg`:

- Group by normalized basename within the same package/member directory scope:
  - `pump.dxf`, `pump.jpg`, `pump.png`, `pump.svg` -> same symbol candidate.
  - Preserve full member path and member id, so duplicate filenames in different folders do not collide.
- The primary source asset is the most authoring-native format, usually DXF.
- The preview is the companion image if present; it should outrank generated previews because it is likely the library's intended visual thumbnail.
- All formats stay attached to the same candidate/revision and should later be downloadable.

Grouping key recommendation:

```text
source_package_id + normalized_parent_path + normalized_stem
```

Do not key only by basename; Symgov already requires duplicate filenames across symbols to be safe.

### 4. Derivative storage rule

Whenever Vlad generates `*.preview.svg` or `*.preview.png` from DXF:

- Upload/store it through the same object storage path used for attachments.
- Create or record an `Attachment` row with:
  - `parent_type='validation_report'` or `parent_type='intake_record'` initially.
  - A clear filename like `<original-stem>.preview.svg`.
  - `content_type='image/svg+xml'` or `image/png`.
- Add the resulting `object_key` to `normalized_payload_json.visual_assets.derivatives` and `preview_object_key` if no companion JPG/PNG exists.

Because the current Vlad runner is outside the repo, implementation must preserve any live runner changes back into the repo plan/status, as done in the DXF Phase 1 plan.

### 5. Preview endpoints should choose preview object keys

Backend preview endpoints should resolve in order:

1. Explicit `preview_object_key`.
2. `visual_assets.preview.object_key`.
3. Best browser-renderable derivative (`svg`, `png`, `jpg`, `jpeg`).
4. Browser-renderable source object if source is already `image/*` or SVG.
5. 404/fallback rather than returning raw DXF to `<img>`.

This applies to:

- `GET /api/v1/workspace/review-cases/{id}/source/preview`
- `GET /api/v1/workspace/review-cases/{id}/children/preview?...`
- `GET /api/v1/published/symbols/{slug}/preview`

Add a helper, e.g. `resolve_preview_object_key_from_asset_manifest(payload, fallback_source_key)`.

### 6. Future download endpoints

Prepare the catalog now by exposing structured download metadata, but the actual UI buttons can come later.

Backend design:

- `GET /api/v1/published/symbols/{slug}/downloads`
  - Returns available assets: DXF, SVG, JPG/PNG, original ZIP/package if allowed, generated derivatives if marked downloadable.
- `GET /api/v1/published/symbols/{slug}/downloads/{asset_id}`
  - Validates that asset belongs to the current published revision.
  - Streams object storage bytes with `Content-Disposition: attachment; filename="..."`.

In the current `published_symbol_row()`, return `downloads` as objects when present:

```json
{
  "label": "DXF",
  "format": "dxf",
  "href": "/api/v1/published/symbols/pump/downloads/dxf",
  "filename": "pump.dxf"
}
```

---

## Implementation tasks

### Task 1: Add shared asset-manifest helper tests

**Objective:** Define and test the preview/download selection rules independently of routes.

**Files:**

- Create: `/data/symgov/backend/symgov_backend/asset_manifest.py`
- Create/modify: `/data/symgov/tests/test_symbol_asset_manifest.py`

**Steps:**

1. Add tests for:
   - companion JPG chosen over generated SVG;
   - generated SVG chosen over raw DXF;
   - raw PNG/JPG/SVG source is usable as preview;
   - raw DXF is not usable as preview;
   - downloads include all source assets and companion assets.
2. Implement `is_browser_previewable(content_type, format, filename)`.
3. Implement `choose_preview_asset(payload, fallback_source_asset=None)`.
4. Implement `list_download_assets(payload)`.
5. Run:
   - `python3 -m pytest tests/test_symbol_asset_manifest.py -q`

### Task 2: Persist DXF derivative object keys from Vlad

**Objective:** Convert the existing local DXF SVG derivative into an object-store attachment/manifest entry.

**Files:**

- Modify live worker: `/data/.openclaw/workspaces/vlad/run_vlad_validation.py`
- Modify/preserve repo plan/status as needed because Vlad is not repo-managed yet.
- Modify/add tests: `/data/symgov/tests/test_dxf_phase1.py`

**Steps:**

1. Extend Vlad DXF derivative generation to produce a real rendered SVG using ezdxf drawing backend where practical; retain current summary SVG only as fallback.
2. Add derivative manifest fields:
   - `format`, `filename`, `content_type`, `sha256`, `size_bytes`, `object_key`.
3. Store/upload derivative bytes using existing object storage helper path used by agents, or add a small worker-side uploader consistent with current edited-symbol artifact handling.
4. Update normalized output with:
   - `visual_assets.preview` = derivative if no companion image.
   - `preview_object_key` = derivative object key.
   - `visual_assets.source_assets[]` includes original DXF.
   - `visual_assets.derivatives[]` includes generated SVG/PNG.
5. Run:
   - `python3 -m pytest tests/test_dxf_phase1.py -q`
   - Synthetic in-container DXF smoke.

### Task 3: Make review preview endpoints use preview manifests

**Objective:** Review screens should show DXF-derived or companion preview assets, not raw DXF.

**Files:**

- Modify: `/data/symgov/backend/symgov_backend/routes/workspace.py`
- Modify tests: add route-level tests if existing route test scaffolding exists, otherwise helper-level coverage plus one integration smoke.

**Steps:**

1. Use `asset_manifest.choose_preview_asset()` in source and child preview resolution.
2. For validation reports, look in `normalized_payload_json.preview_object_key` and `normalized_payload_json.visual_assets` before `resolve_source_object_key()`.
3. For `ReviewSplitItem`, look in `split_item.payload_json.preview_object_key` / `visual_assets` before `attachment_object_key`.
4. If the only available asset is DXF, return 404 so frontend fallback is used; do not serve raw DXF as an image preview.
5. Run focused backend tests.

### Task 4: Group companion files in package/ZIP intake

**Objective:** A library with `pump.dxf` and `pump.jpg` should create one symbol candidate with two formats, not two unrelated symbol records.

**Files:**

- Modify: `/data/symgov/scripts/run_scott_intake.py`
- Modify live Scott helper if ZIP expansion lives under `/data/.openclaw/workspaces/scott/`.
- Add tests: `/data/symgov/tests/test_dxf_companion_assets.py` or extend future ZIP tests.

**Steps:**

1. During package/member manifest creation, group supported members by `source_package_id + normalized_parent_path + normalized_stem`.
2. Build child payloads with `visual_assets.source_assets[]` containing all grouped assets.
3. Set `asset_format` to the primary source format (`dxf` if present; else `svg`; else raster).
4. Set `preview_object_key` to companion JPG/PNG/SVG if present.
5. Route grouped candidate once to Vlad + Tracy, not once per file, when assets are clearly companion formats.
6. Verify duplicate basenames in different folders produce separate groups.

### Task 5: Carry manifests through publication

**Objective:** Published catalog rows should know both what to preview and what can later be downloaded.

**Files:**

- Modify: `/data/symgov/backend/symgov_backend/publication_handoff.py`
- Modify tests: publication handoff tests or add `tests/test_publication_multiformat_assets.py`.

**Steps:**

1. In `ensure_approved_symbol_revision()`, copy visual/download asset manifests from validation/classification/intake context into `revision.payload_json`.
2. In `ensure_approved_child_symbol_revision()`, copy `ReviewSplitItem.payload_json.visual_assets` plus the reviewed child source asset.
3. Preserve compatibility fields:
   - `source_object_key`
   - `preview_object_key`
   - `downloads`
4. Ensure `downloads` includes all available source formats, including DXF and companion JPG/SVG/PNG.
5. Run publication handoff regression tests.

### Task 6: Make published preview and catalog downloads manifest-aware

**Objective:** Catalog preview should show DXF-derived/companion images, and catalog rows should expose structured download descriptors.

**Files:**

- Modify: `/data/symgov/backend/symgov_backend/routes/published.py`
- Modify: `/data/symgov/frontend/src/App.jsx`
- Add/modify tests around published symbol row/preview.

**Steps:**

1. In `published_symbol_row()`, set `previewUrl` when `preview_object_key` or `visual_assets.preview` exists, not only `source_object_key`.
2. Add structured download descriptors to `downloads`.
3. Update `/symbols/{symbol_id}/preview` to serve chosen preview asset.
4. Add future-ready download endpoints or at least route stubs with tests if UI is deferred.
5. Frontend: keep rendering preview with `<img>`, but display structured download labels in the detail panel.
6. Later UI: replace tag chips with actual download buttons once endpoint is active.
7. Run:
   - `npm run build`
   - backend focused tests.

### Task 7: End-to-end verification

**Objective:** Prove the design covers all requested scenarios.

**Synthetic fixtures:**

1. Direct DXF only:
   - Review screen shows generated SVG/PNG preview.
   - Catalog shows same preview after publish.
   - Downloads include DXF.
2. ZIP/package with `pump.dxf` + `pump.jpg`:
   - One symbol candidate/review item.
   - Review preview uses JPG.
   - Catalog preview uses JPG.
   - Downloads include both DXF and JPG.
3. ZIP/package with duplicate names:
   - `folder-a/pump.dxf` + `folder-a/pump.jpg`
   - `folder-b/pump.dxf` + `folder-b/pump.jpg`
   - Two separate symbol candidates; no collision.
4. DXF with failed derivative:
   - Review/catalog fall back gracefully and show clear unavailable state.
   - Original DXF still remains downloadable/provenance evidence.

**Commands:**

- `python3 -m pytest tests/test_dxf_phase1.py tests/test_symbol_asset_manifest.py tests/test_publication_multiformat_assets.py -q`
- `python3 -m pytest tests -q`
- `npm run build`
- API health check after any container rebuild.

---

## Recommended first slice

Do this in the next implementation session before full ZIP grouping:

1. Add `asset_manifest.py` helper and tests.
2. Make published/review preview routes refuse raw DXF and prefer explicit `preview_object_key`.
3. Update Vlad DXF derivative output so the generated SVG has an object key and becomes `preview_object_key`.
4. Confirm direct-DXF-only flow previews in review and catalog.

Then implement companion grouping for ZIP/library intake as the second slice.

## Open questions / choices

- Whether to add a dedicated `symbol_revision_assets` table now. Recommendation: not yet; use JSON manifests first because the existing system already stores revision payloads in JSONB and `attachments` stores object metadata.
- Whether generated DXF preview should be SVG only, PNG only, or both. Recommendation: generate SVG first for crisp line art; add PNG fallback where ezdxf/SVG rendering is incomplete or if browser handling/security policy requires rasterization.
- Whether companion JPG should be treated as downloadable. Recommendation: yes, unless provenance/rights says otherwise, because the future catalog download menu should expose all available submitted formats.

## Current repo state at investigation close

`git status --short --branch` on 2026-06-14:

```text
## main...origin/main
 M backend/requirements.txt
 M backend/symgov_backend/services/external_submissions.py
 M scripts/run_scott_intake.py
?? docs/plans/2026-06-13-dxf-zip-symbol-intake-implementation-plan.md
?? tests/test_dxf_phase1.py
```

This plan file is additional uncommitted work.

## Copyable restart prompt

Continue Symgov DXF preview and multi-format symbol asset work from `/data/symgov/docs/plans/2026-06-14-dxf-preview-and-multiformat-symbol-assets.md`. First inspect current repo/live worker divergence and read the existing DXF Phase 1 plan at `/data/symgov/docs/plans/2026-06-13-dxf-zip-symbol-intake-implementation-plan.md`. Implement the recommended first slice: shared asset manifest helper/tests, preview route selection that never serves raw DXF to `<img>`, and Vlad-generated DXF SVG/PNG derivatives stored as object-key-backed preview assets. Preserve original DXF as downloadable/provenance source, support companion JPG/PNG/SVG as preferred previews when grouped with same-stem DXF, and verify with focused pytest, full pytest, frontend build if UI changes, plus a synthetic DXF smoke.
