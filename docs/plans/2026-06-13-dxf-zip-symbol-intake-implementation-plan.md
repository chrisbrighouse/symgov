# DXF and ZIP Symbol Intake Implementation Plan

Date: 2026-06-13
Status: Research/design recorded; implementation not started in this plan.
Owner context: Symgov agent pipeline, especially Scott intake/normalisation and Vlad technical validation.

## Goal

Add full end-to-end handling for:

- DXF files submitted directly.
- ZIP packages that may contain DXF, SVG, JSON, PNG, JPG/JPEG.

The implementation should preserve provenance, use low-friction/free/open tooling where practical, and carry each symbol candidate through the existing Scott -> Vlad/Tracy -> Libby -> Daisy -> Rupert process without losing package/member traceability.

## Current verified baseline

As of 2026-06-13:

- Scott intake accepts these extensions:
  - `.svg` -> `svg`
  - `.json` -> `json`
  - `.png` -> `png`
  - `.jpg` / `.jpeg` -> `jpeg`
- Scott currently rejects other file extensions as unsupported.
- Scott routes accepted SVG/PNG/JPEG to Vlad + Tracy.
- Scott accepts JSON but routes it to Tracy only, not Vlad.
- Vlad practically handles:
  - SVG: integrity, XML/SVG parse, accessibility, geometry, duplicate fingerprint checks.
  - PNG/JPEG: raster sheet analysis, single-candidate detection, split crop proposal.
- DXF and ZIP are not currently supported end-to-end.

Key code paths inspected:

- Scott repo runner: `/data/symgov/scripts/run_scott_intake.py`
- Live Scott runner copy: `/data/.openclaw/workspaces/scott/run_scott_intake.py`
- Scott downstream enqueue: `/data/.openclaw/workspaces/scott/enqueue_scott_downstream.py`
- Vlad live runner: `/data/.openclaw/workspaces/vlad/run_vlad_validation.py`
- External submission service: `/data/symgov/backend/symgov_backend/services/external_submissions.py`

Note: if Scott/Vlad live worker files remain outside the repo, preserve any direct-worker changes back into the repo before closeout, following the existing Scott worker preservation pattern.

## Library/tool research summary

### Recommended primary DXF library: `ezdxf`

- Current PyPI version checked: `1.4.4`
- License classifier: MIT
- Python requirement: `>=3.10`
- Python-native, active, and suitable for headless/server usage.
- Can read/manipulate DXF and includes a drawing/export add-on.
- Relevant capabilities:
  - `ezdxf.recover.readfile(...)` for safer loading/recovery.
  - Audit/error reporting via the recovered document/auditor.
  - Rendering/export through drawing add-on backends including:
    - `SVGBackend`
    - `MatplotlibBackend`
    - `DXFBackend`
    - JSON/GeoJSON backends
- Documentation notes limitations common to the drawing add-on:
  - ACIS entities unsupported.
  - No true 3D rendering engine.
  - 3D content is projected to xy-plane/top view.
  - Rich MTEXT is close but not pixel-perfect.
  - Some infinite lines/OLE frames/vertical text/MTEXT columns have limited support.

Decision: use `ezdxf` first. It is the cleanest fit for Symgov because it can parse, inspect, validate, render, and export without a heavyweight CAD GUI dependency.

### Secondary/fallback tools considered

- `dxfgrabber`
  - MIT.
  - Older/read-focused. Keep as possible reference/fallback, not primary.
- `dxf2svg`
  - GPL.
  - Avoid as a server dependency unless Symgov explicitly accepts GPL implications.
- LibreCAD/QCAD/Inkscape
  - Useful as manual/operator fallback tools, but too heavy/quirky for first automated implementation.
- ODA/DWG tooling
  - Potentially useful for DWG, but proprietary/freeware complexity. Keep DWG out of first scope.

## Design principles

1. Treat ZIP as a container/package, not as a symbol format.
2. Scott owns intake, package expansion, normalisation, and routing.
3. Vlad should process actual member assets and DXF files, not raw ZIP packages.
4. Preserve original DXF/ZIP as provenance evidence.
5. Publish or review normalized derivatives, usually SVG and/or PNG previews.
6. Preserve duplicate filenames safely. Never key only by basename.
7. Keep JSON conservative at first: metadata/library manifest, not arbitrary geometry execution.
8. Keep rights/provenance flowing through Tracy for all original submitted source assets.

## Target end-to-end flow: direct DXF

1. External submission accepts `.dxf` and stores it as an attachment/object.
2. Scott:
   - recognises `.dxf` as supported format `dxf`;
   - validates normal intake fields;
   - accepts eligible DXF;
   - routes to Vlad + Tracy.
3. Tracy:
   - checks rights/provenance against the original DXF source.
4. Vlad:
   - parses/recover-loads DXF via `ezdxf`;
   - records metadata and audit warnings/errors;
   - produces normalized derivatives:
     - accessible SVG where possible;
     - PNG preview where useful;
     - JSON technical metadata artifact;
   - runs suitable validation on the derivative;
   - if single-symbol, creates a candidate artifact;
   - if sheet/library/multiple-candidate, proposes split/review artifacts.
5. Libby:
   - classifies from rendered derivative/thumbnail and technical metadata.
6. Daisy:
   - coordinates human review/split decisions as usual.
7. Rupert:
   - publishes normalized visual symbol asset, likely SVG;
   - keeps original DXF as source/provenance attachment;
   - duplicate gate runs on normalized/rendered visual asset.

## Target end-to-end flow: ZIP package

1. External submission accepts `.zip` and stores the original ZIP as an immutable source attachment/object.
2. Scott:
   - validates the ZIP as an untrusted archive;
   - safely extracts supported members into a controlled package workspace;
   - creates a package manifest;
   - creates child queue/intake work for each supported member.
3. Supported member extensions:
   - `.dxf`
   - `.svg`
   - `.json`
   - `.png`
   - `.jpg`
   - `.jpeg`
4. Unsupported members:
   - record in manifest with reason;
   - do not process;
   - do not fail the entire ZIP if at least one supported member is usable.
5. Each child member carries package provenance:
   - original ZIP attachment/object key/sha256;
   - package id;
   - member path inside ZIP;
   - member index/id;
   - member sha256;
   - source package queue/intake id.
6. Child routes then follow the direct file behaviour:
   - SVG -> Vlad + Tracy.
   - DXF -> Vlad + Tracy.
   - PNG/JPG/JPEG -> Vlad + Tracy.
   - JSON -> Scott/Tracy metadata/library-import handling; not Vlad initially unless a known symbol schema is implemented.

## ZIP safety requirements

Use Python stdlib `zipfile`, but treat every archive as hostile.

Initial controls:

- Reject path traversal (`../`, absolute paths, Windows drive paths).
- Reject symlinks and special files.
- Maximum member count, e.g. 200.
- Maximum single member uncompressed size, e.g. 25-50 MB.
- Maximum total uncompressed size, e.g. 100-250 MB.
- Reject suspicious compression ratios / zip bombs.
- Start with no nested ZIP support.
- Extract to a controlled package directory, never into source tree or runtime root directly.
- Preserve duplicate basenames by using member index/path/sha256 generated ids.

Suggested package/member manifest fields:

- `source_package_id`
- `source_package_attachment_id`
- `source_package_object_key`
- `source_package_sha256`
- `original_zip_filename`
- `members[]`:
  - `member_id`
  - `member_index`
  - `original_path`
  - `safe_stored_path`
  - `filename`
  - `extension`
  - `declared_format`
  - `sha256`
  - `compressed_size`
  - `uncompressed_size`
  - `status`: accepted/skipped/rejected
  - `reason_codes[]`
  - `downstream_queue_ids[]`

## DXF Vlad validation design

Add DXF-specific checks in Vlad rather than pretending DXF is SVG.

Proposed checks:

- Integrity:
  - asset exists;
  - non-empty;
  - sha256;
  - size limits.
- Parse/recover:
  - use `ezdxf.recover.readfile(path)`;
  - record auditor errors/warnings;
  - fail on severe structural corruption;
  - escalate on recoverable but risky warnings.
- Metadata extraction:
  - DXF version;
  - units;
  - modelspace entity counts;
  - paperspace/layout names;
  - layer names/counts;
  - block names/counts;
  - per-entity type counts: LINE, LWPOLYLINE, CIRCLE, ARC, TEXT, MTEXT, INSERT, HATCH, SPLINE, IMAGE, etc.;
  - extents/bounding box where available.
- Risk/safety flags:
  - XREF/external references;
  - IMAGE references;
  - proxy/unsupported entities;
  - 3D/ACIS entities;
  - very large entity counts.
- Derivative generation:
  - normalized SVG via `ezdxf.addons.drawing.svg.SVGBackend` where possible;
  - PNG preview via Matplotlib backend where useful;
  - JSON technical metadata artifact.
- SVG enrichment:
  - ensure generated SVG has `<title id="...">`;
  - ensure generated SVG has `<desc id="...">`;
  - add `role="img"`;
  - add `aria-labelledby="title-id desc-id"`;
  - ensure `viewBox` and dimensions are present.

## JSON handling inside ZIP

First-pass policy:

- Accept JSON as metadata or a known imported-library manifest only.
- Do not treat arbitrary JSON as executable/geometry input.
- If JSON references sibling ZIP members, use it to enrich child payloads:
  - names;
  - categories;
  - descriptions;
  - source refs;
  - standards refs.
- JSON containing inline SVG/vector geometry should be a later explicit feature.

## Implementation phases

### Phase 1: Direct DXF support

Files likely involved:

- `/data/symgov/backend/symgov_backend/services/external_submissions.py`
  - update `guess_declared_format()` for `.dxf`.
- `/data/symgov/scripts/run_scott_intake.py`
  - add `.dxf` to `SUPPORTED_FORMATS`.
  - accept and route `dxf` to Vlad + Tracy.
- `/data/.openclaw/workspaces/scott/enqueue_scott_downstream.py`
  - add `dxf` as Vlad-routed asset format.
  - set DXF-specific expected checks.
- `/data/.openclaw/workspaces/vlad/run_vlad_validation.py`
  - add `dxf` inference.
  - add DXF parse/metadata/derivative path.
  - store generated artifacts and derivative manifests.

Deliverables:

- DXF accepted from external submission.
- Scott routes DXF to Vlad + Tracy.
- Vlad parses DXF and creates normalized derivative artifacts.
- Existing downstream review/publication can proceed via normalized SVG/PNG artifacts.

### Phase 2: ZIP package expansion

Files likely involved:

- `/data/symgov/backend/symgov_backend/services/external_submissions.py`
  - update `guess_declared_format()` for `.zip`.
- `/data/symgov/scripts/run_scott_intake.py`
  - add `.zip` to supported formats.
  - implement or call safe package expansion.
- Scott downstream/package helper, new module if appropriate:
  - safe extraction;
  - manifest generation;
  - child queue item generation.
- Persistence/runtime bridge as needed for DB child queue rows and artifacts.

Deliverables:

- ZIP accepted as package.
- Supported members extracted safely.
- Child queue items created with package/member provenance.
- Unsupported members recorded but not processed.

### Phase 3: DXF library intelligence

Enhancements:

- Detect multiple DXF blocks/layers as candidate symbols.
- Generate per-block derivatives where practical.
- Improve sheet-vs-single-symbol heuristics.
- Use ZIP JSON manifests to enrich symbols.
- Provide clearer review evidence for operator decisions.

### Phase 4: UI/operator polish

Enhancements:

- Show package expansion status.
- Show ZIP -> member -> candidate trace.
- Show DXF derivative preview.
- Optionally allow reviewers/operators to download original DXF/ZIP provenance source.
- Keep canonical short symbol IDs primary in cards/tables after downstream creation.

## Test plan

### Scott tests

- `.dxf` accepted and routed to Vlad + Tracy.
- `.zip` accepted when it contains supported members.
- ZIP with only unsupported files rejects/escalates cleanly.
- ZIP with duplicate basenames in different folders preserves both.
- ZIP path traversal rejected.
- ZIP symlink/special file rejected.
- ZIP bomb/high compression ratio rejected.
- ZIP over max file count or size rejected.
- Mixed ZIP creates child queue items with stable member ids and provenance.

### Vlad tests

- Minimal DXF parses and produces SVG derivative.
- Corrupt DXF fails/escalates with meaningful defect code.
- DXF with unsupported/3D/ACIS/proxy entities escalates.
- DXF with multiple blocks creates candidates/split artifacts or escalates to review.
- Generated SVG satisfies existing accessibility checks.
- Generated PNG preview can enter raster sheet analysis.
- Derivative artifacts link back to original DXF.

### End-to-end smoke

Submit a ZIP containing:

- `a.dxf`
- `icons/pump.svg`
- `icons/pump.png`
- `metadata.json`
- `ignored/readme.txt`

Verify:

- Scott package manifest exists.
- Vlad queue items are created for DXF/SVG/PNG.
- Tracy receives provenance tasks.
- Unsupported readme is recorded but not processed.
- Review queue/card labels eventually use human-readable IDs.
- Original ZIP and member provenance are visible in audit/artifacts.

## Operational closeout requirements for future implementation sessions

When implementation begins or continues:

1. Check current live/repo runner divergence before editing.
2. Preserve any external live worker changes back into repo-managed scripts where applicable.
3. Run focused unit tests for Scott/Vlad changes.
4. Run a synthetic DXF smoke generated by `ezdxf`.
5. Run a mixed ZIP smoke.
6. If frontend/UI changes are included, rebuild and publish static assets following the Symgov frontend deploy procedure.
7. Restart/recreate the Hermes-native API container only when backend/runtime changes require it.
8. Finish with a restart-ready status note or update this plan with:
   - current status;
   - verification commands/results;
   - uncommitted state;
   - next actions;
   - copyable restart prompt.

## Copyable restart prompt

Continue the Symgov DXF/ZIP symbol intake implementation using the plan in `/data/symgov/docs/plans/2026-06-13-dxf-zip-symbol-intake-implementation-plan.md`. First inspect current repo/live runner divergence and current queue health. Then proceed with the next incomplete phase, preserving original DXF/ZIP provenance, using `ezdxf` for DXF parsing/derivative generation, using safe `zipfile` extraction for ZIPs, and keeping duplicate ZIP member filenames distinct via package/member ids. Verify with focused tests plus synthetic DXF and mixed ZIP smoke tests before reporting completion.

## Phase 1 implementation status update - 2026-06-13T17:00:19Z

Status: Phase 1 direct DXF support has been started and smoke-verified.

Implemented in repo-managed files:

- `/data/symgov/backend/requirements.txt`
  - Added `ezdxf>=1.4,<2` for backend/API image builds.
- `/data/symgov/backend/symgov_backend/services/external_submissions.py`
  - `guess_declared_format("*.dxf")` now returns `dxf`.
- `/data/symgov/scripts/run_scott_intake.py`
  - `.dxf` is now a supported Scott intake extension.
  - Accepted DXF intakes route to `vlad` + `tracy`.
  - Accepted DXF intakes add `dxf_validation_candidate` to eligibility flags.
- `/data/symgov/tests/test_dxf_phase1.py`
  - Added regression coverage for external submission format guessing, Scott DXF routing, downstream Vlad queue construction, and Vlad DXF validation/derivative generation.

Implemented in live external/direct-worker files outside the repo:

- `/data/.openclaw/workspaces/scott/run_scott_intake.py`
  - Synchronized from `/data/symgov/scripts/run_scott_intake.py`; verified with `cmp` result `0`.
- `/data/.openclaw/workspaces/scott/enqueue_scott_downstream.py`
  - Added `DXF_VLAD_CHECKS = ["integrity", "dxf_parse", "dxf_metadata", "dxf_derivative"]`.
  - Builds Vlad queue items for `asset_format=dxf` with those expected checks.
- `/data/.openclaw/workspaces/vlad/run_vlad_validation.py`
  - Imports `ezdxf`/`ezdxf.recover` when available.
  - Recognizes declared/inferred `dxf` asset format.
  - Adds DXF parse/recovery, metadata extraction, and accessible SVG derivative generation.
  - Emits `dxf_derivative_manifest` additional artifact and stores `dxf_metadata` / `dxf_derivative` in normalized technical metadata.
- `/data/.openclaw/workspace/symgov/backend/symgov_backend/services/external_submissions.py`
  - Patched compatibility backend `guess_declared_format("*.dxf")` to return `dxf` because some direct-runner imports still prepend this legacy backend path.

Verification completed:

- RED test run before implementation:
  - `python3 -m pytest tests/test_dxf_phase1.py -q`
  - Result before code changes: 4 failing tests, proving missing DXF behavior.
- Focused GREEN run:
  - `python3 -m pytest tests/test_dxf_phase1.py -q`
  - Result: `4 passed`.
- Focused regression run:
  - `python3 -m pytest tests/test_dxf_phase1.py tests/test_scott_auth_verification.py -q`
  - Result: `10 passed`.
- Syntax check:
  - `python3 -m py_compile /data/symgov/scripts/run_scott_intake.py /data/.openclaw/workspaces/scott/enqueue_scott_downstream.py /data/.openclaw/workspaces/vlad/run_vlad_validation.py /data/symgov/backend/symgov_backend/services/external_submissions.py`
  - Result: no errors.
- Full repo test run after installing local test dependencies:
  - `python3 -m pytest tests -q`
  - Result: `80 passed`.
- Live API image/runtime verification:
  - Rebuilt and recreated Hermes-native API container with `docker compose --project-directory /docker/symgov-hermes build symgov-api` and `docker compose --project-directory /docker/symgov-hermes up -d --no-deps --force-recreate symgov-api`.
  - `docker exec symgov-hermes-api python -c "import ezdxf; print('ezdxf', ezdxf.__version__)"` -> `ezdxf 1.4.4`.
  - Local API health: `{"ok":true,"service":"symgov-api"...}`.
  - Public API health: `{"ok":true,"service":"symgov-api"...}`.
  - Container external-submission guess check: `container_dxf_guess dxf`.
- Live in-container synthetic DXF smoke:
  - Scott accepted synthetic `.dxf` and routed `['vlad', 'tracy']`.
  - Vlad returned `decision pass`.
  - Vlad extracted `entity_counts {'CIRCLE': 1, 'LINE': 1}`.
  - Vlad generated an accessible SVG derivative and confirmed `svg_exists True`.

Current repo/uncommitted state after Phase 1 work:

- Modified tracked repo files:
  - `backend/requirements.txt`
  - `backend/symgov_backend/services/external_submissions.py`
  - `scripts/run_scott_intake.py`
- New untracked repo files:
  - `docs/plans/2026-06-13-dxf-zip-symbol-intake-implementation-plan.md`
  - `tests/test_dxf_phase1.py`
- External live files changed outside git:
  - `/data/.openclaw/workspaces/scott/run_scott_intake.py`
  - `/data/.openclaw/workspaces/scott/enqueue_scott_downstream.py`
  - `/data/.openclaw/workspaces/vlad/run_vlad_validation.py`
  - `/data/.openclaw/workspace/symgov/backend/symgov_backend/services/external_submissions.py`

Known limitations of this Phase 1 slice:

- Vlad's first DXF SVG derivative is a conservative accessible technical preview/manifest carrier, not a pixel-perfect CAD rendering yet.
- DXF-derived PNG preview generation is not implemented yet.
- DXF block/layer candidate splitting is not implemented yet.
- ZIP package support is not implemented yet.
- The Vlad runner still lives outside the repo, so its live changes should be intentionally preserved/ported into repo-managed worker packaging if/when that structure is formalized.

Next actions:

1. Commit/stage the repo-visible Phase 1 files if desired.
2. Decide how to preserve or repo-manage the external Vlad and Scott downstream helper changes; Scott main runner is already mirrored into repo script, but `enqueue_scott_downstream.py` and Vlad have no repo path found under `/data/symgov`.
3. Improve DXF derivative quality by using `ezdxf` drawing/export backends for richer SVG/PNG previews.
4. Add corrupt-DXF and risky-entity tests.
5. Start Phase 2 ZIP support only after the Phase 1 direct-DXF path is accepted.

Updated restart prompt:

Continue Symgov DXF/ZIP symbol intake from `/data/symgov/docs/plans/2026-06-13-dxf-zip-symbol-intake-implementation-plan.md`. Phase 1 direct DXF support has initial tests and live smoke verification. First inspect `git status --short --branch`, verify `/data/symgov/scripts/run_scott_intake.py` still matches `/data/.openclaw/workspaces/scott/run_scott_intake.py`, and inspect the external live worker changes in Scott downstream enqueue and Vlad. Then either harden Phase 1 DXF derivatives/tests or proceed to Phase 2 ZIP package expansion. Preserve original DXF/ZIP provenance, keep Tracy in the route, and verify with `python3 -m pytest tests -q`, API health checks, and synthetic DXF/ZIP smoke tests before closeout.

## Phase 2 ZIP package support status update - 2026-06-16T18:33:03Z

Status: Phase 2 ZIP package support has been implemented, deployed to the live Scott runner copy, and smoke-verified.

Implemented in repo-managed files:

- `/data/symgov/backend/symgov_backend/services/external_submissions.py`
  - `guess_declared_format("*.zip")` now returns `zip`, so external submissions can queue ZIP packages.
- `/data/symgov/scripts/run_scott_intake.py`
  - `.zip` is now a supported Scott intake extension.
  - Added hostile-archive controls using stdlib `zipfile`: path traversal/absolute path/Windows drive rejection, symlink and special-file rejection, member count limit, per-member and total uncompressed size limits, compression-ratio guard, and no nested ZIP support.
  - ZIP members are extracted only into a controlled package workspace.
  - Duplicate member basenames are preserved with package/member IDs and unique storage paths.
  - ZIP package artifact includes a package manifest and child Scott task payloads for supported members.
  - Raw ZIP packages do not route directly to Vlad/Tracy; extracted child members are queued back to Scott, and accepted child intakes then follow normal SVG/DXF/PNG/JPEG/JSON routing.
  - Child member payloads preserve `source_package_id`, source ZIP attachment/object key/sha256, member index/id/path/sha256, and package-member visual source asset metadata.
  - Repo runner now uses repo backend imports when executed from `/data/symgov`, and falls back to the legacy backend path only when copied into live OpenClaw workspace.
- `/data/symgov/tests/test_zip_phase2.py`
  - Added regression coverage for external ZIP format guessing, safe expansion, duplicate filenames, traversal rejection, child Scott queue creation, and downstream Vlad/Tracy provenance preservation.

Implemented in live/external files outside git:

- `/data/.openclaw/workspaces/scott/run_scott_intake.py`
  - Synchronized from `/data/symgov/scripts/run_scott_intake.py`; verified with `cmp` result `0`.
- `/data/.openclaw/workspaces/scott/enqueue_scott_downstream.py`
  - Vlad and Tracy queue payloads now include ZIP package/member provenance fields when processing child intake records.
- `/data/.openclaw/workspace/symgov/backend/symgov_backend/services/external_submissions.py`
  - Patched compatibility backend `guess_declared_format("*.zip")` to return `zip`.

Verification completed:

- RED test run before implementation:
  - `python3 -m pytest tests/test_zip_phase2.py -q`
  - Result before code changes: `4 failed`, proving ZIP support was missing.
- Downstream provenance RED test before helper patch:
  - `python3 -m pytest tests/test_zip_phase2.py::test_downstream_queue_items_preserve_zip_package_member_provenance -q`
  - Result before helper change: failed with missing `source_package_id`.
- Focused GREEN run:
  - `python3 -m pytest tests/test_zip_phase2.py -q`
  - Result: `5 passed`.
- Focused regression run:
  - `python3 -m pytest tests/test_zip_phase2.py tests/test_dxf_phase1.py tests/test_scott_auth_verification.py -q`
  - Result: `18 passed`.
- Full repo test run:
  - `python3 -m pytest tests -q`
  - Result: `114 passed`.
- Syntax/live sync verification:
  - `cmp -s /data/symgov/scripts/run_scott_intake.py /data/.openclaw/workspaces/scott/run_scott_intake.py; echo scott_cmp:$?`
  - Result: `scott_cmp:0`.
  - `python3 -m py_compile /data/symgov/scripts/run_scott_intake.py /data/.openclaw/workspaces/scott/run_scott_intake.py /data/.openclaw/workspaces/scott/enqueue_scott_downstream.py /data/symgov/backend/symgov_backend/services/external_submissions.py /data/.openclaw/workspace/symgov/backend/symgov_backend/services/external_submissions.py`
  - Result: no errors.
- Mixed ZIP smoke through live Scott runner:
  - ZIP contained `area-a/pump.svg`, `area-b/pump.svg`, `cad/valve.dxf`, `metadata/manifest.json`, and `ignored/readme.txt`.
  - Result: `decision accepted`, `eligibility_status eligible`, `route_to_agents []` for the raw ZIP package.
  - Accepted child formats: `svg`, `svg`, `dxf`, `json`.
  - Unsupported TXT member recorded as skipped with `unsupported_member_format`.
  - Duplicate `pump.svg` member filenames had distinct safe stored paths.
- Live API image/runtime verification:
  - Rebuilt/recreated `symgov-hermes-api` with `docker compose --project-directory /docker/symgov-hermes build symgov-api` and `docker compose --project-directory /docker/symgov-hermes up -d --no-deps --force-recreate symgov-api`.
  - Container check: `container_zip_guess zip`.
  - In-container API health: `{"ok":true,"service":"symgov-api","time":"2026-06-16T18:32:21Z"}`.

Current repo/uncommitted state after Phase 2 work:

- Modified tracked repo files:
  - `backend/symgov_backend/services/external_submissions.py`
  - `scripts/run_scott_intake.py`
  - `docs/plans/2026-06-13-dxf-zip-symbol-intake-implementation-plan.md`
- New untracked repo files:
  - `tests/test_zip_phase2.py`
- External live files changed outside git:
  - `/data/.openclaw/workspaces/scott/run_scott_intake.py`
  - `/data/.openclaw/workspaces/scott/enqueue_scott_downstream.py`
  - `/data/.openclaw/workspace/symgov/backend/symgov_backend/services/external_submissions.py`

Known limitations of this Phase 2 slice:

- JSON members are accepted as conservative metadata/library-manifest inputs, but ZIP JSON sibling enrichment is not implemented yet.
- ZIP child package-member object keys are provenance keys for downstream metadata; extracted member uploads as independent object-store attachments are not implemented in this slice.
- Package expansion status is not yet surfaced in frontend/operator UI.
- DXF derivative quality limitations from Phase 1 remain: conservative accessible technical SVG preview, no PNG preview, no DXF block/layer splitting yet.

Next actions:

1. Commit/stage the Phase 1/Phase 2 repo-visible files if desired.
2. Decide whether to formalize repo-managed locations for Scott downstream enqueue and Vlad live worker code, since Scott downstream enqueue is still outside git.
3. Add ZIP JSON manifest enrichment for sibling symbols.
4. Add UI/operator package trace display for ZIP -> member -> candidate.
5. Continue DXF Phase 3 library intelligence: block/layer candidate splitting and better derivatives.

Updated restart prompt:

Continue Symgov DXF/ZIP symbol intake from `/data/symgov/docs/plans/2026-06-13-dxf-zip-symbol-intake-implementation-plan.md`. Phase 1 direct DXF and Phase 2 ZIP package expansion are implemented and verified. First inspect `git status --short --branch`, verify `/data/symgov/scripts/run_scott_intake.py` still matches `/data/.openclaw/workspaces/scott/run_scott_intake.py`, and inspect external live changes in `/data/.openclaw/workspaces/scott/enqueue_scott_downstream.py` plus Vlad. Preserve original DXF/ZIP provenance and duplicate ZIP member filenames via package/member IDs. Next likely work is ZIP JSON sibling enrichment, package-trace UI polish, or DXF Phase 3 library intelligence. Re-run `python3 -m pytest tests -q`, a mixed ZIP smoke, and API health checks before closeout.

## Public ZIP submission fix - 2026-06-16T18:50Z

Reason for the failed fire-alarms ZIP upload:

- The public upload reached `applications-web` nginx, not the Symgov API.
- Nginx rejected the body before proxying it because the JSON/base64 request was `3,860,018` bytes and the default nginx `client_max_body_size` was still 1MB.
- Evidence from `docker logs --since=24h applications-web`:
  - `client intended to send too large body: 3860018 bytes`
  - `POST /api/v1/public/external-submissions HTTP/1.1" 413`
- No saved Scott upload/queue item exists for that attempt; the user needs to re-upload after the fix.

Implemented fixes:

- `/data/symgov/frontend/src/App.jsx`
  - Added `.zip` to the external submission file input `accept` list.
  - Updated visible accepted-format helper text to include `JPEG`, `DXF`, and `ZIP`.
- `/data/symgov/tests/test_submission_ui_zip_acceptance.py`
  - Added regression coverage that the submission UI advertises/selects `.zip` packages.
- `/docker/symgov-hermes/nginx.conf`
  - Added `client_max_body_size 64m;` under `location /api/`.
  - Recreated `applications-web` because the bind-mounted config file needed a container recreate before nginx saw the new inode.

Verification completed:

- RED UI regression before the UI change:
  - `python3 -m pytest tests/test_submission_ui_zip_acceptance.py -q`
  - Result: failed because `.zip` was absent.
- GREEN UI regression/build:
  - `python3 -m pytest tests/test_submission_ui_zip_acceptance.py -q`
  - Result: `1 passed`.
  - `npm run build`
  - Result: Vite build succeeded; live asset is `assets/index-CHUI0XS9.js`.
- Nginx config/deploy verification:
  - `docker compose -f /docker/symgov-hermes/docker-compose.yml up -d --no-deps --force-recreate applications-web`
  - In-container config now shows `client_max_body_size 64m;` inside `location /api/`.
- Public body-size probe:
  - A 3,860,301-byte wrapped invalid-PIN request now returns `http=400` with `Invalid submission PIN`, proving it reaches the API rather than failing at nginx with 413.
- Public frontend verification:
  - `curl https://apps.chrisbrighouse.com/` references `assets/index-CHUI0XS9.js`.
  - The live JS contains `.svg,.png,.jpg,.jpeg,.json,.dxf,.zip` and the updated accepted-format text.
- Focused ZIP/UI regression:
  - `python3 -m pytest tests/test_submission_ui_zip_acceptance.py tests/test_zip_phase2.py -q`
  - Result: `6 passed`.

Current uncommitted state after this update:

- Modified tracked repo files:
  - `backend/symgov_backend/services/external_submissions.py`
  - `docs/plans/2026-06-13-dxf-zip-symbol-intake-implementation-plan.md`
  - `frontend/src/App.jsx`
  - `scripts/run_scott_intake.py`
- New untracked repo files:
  - `tests/test_submission_ui_zip_acceptance.py`
  - `tests/test_zip_phase2.py`
- External live files changed outside git:
  - `/data/.openclaw/workspaces/scott/run_scott_intake.py`
  - `/data/.openclaw/workspaces/scott/enqueue_scott_downstream.py`
  - `/data/.openclaw/workspace/symgov/backend/symgov_backend/services/external_submissions.py`
  - `/docker/symgov-hermes/nginx.conf`

Updated restart prompt:

Continue Symgov DXF/ZIP symbol intake from `/data/symgov`. Phase 1 direct DXF, Phase 2 ZIP expansion, public ZIP file selection, and nginx upload-body sizing are implemented and live. First inspect `git status --short --branch`, verify `/data/symgov/scripts/run_scott_intake.py` still matches `/data/.openclaw/workspaces/scott/run_scott_intake.py`, and verify `docker exec applications-web grep -n "client_max_body_size" /etc/nginx/conf.d/default.conf`. If continuing ZIP intake, ask the user to re-upload the fire-alarms ZIP because the failed 413 attempt never reached the API. Re-run `python3 -m pytest tests/test_submission_ui_zip_acceptance.py tests/test_zip_phase2.py -q`, `npm run build`, and a public large-body invalid-PIN probe before closeout.

## Live fire-alarms ZIP submission monitoring and review split idempotency fix - 2026-06-16T19:05Z

Live batch monitored:

- Submission batch: `subext-20260616T185153Z`.
- Raw Scott queue item: `aqi-scott-ext-20260616T185153Z-01`.
- ZIP extraction succeeded into `/data/.openclaw/workspaces/scott/runtime/external_uploads/subext-20260616T185153Z/zip_packages/pkg-aqi-scott-ext-20260616t185153z-01-3f09d51b8eaa/`.
- Queue summary verified from Postgres:
  - Scott: `61 completed`.
  - Vlad: `33 completed`.
  - Vlad: `28 escalated` with `validation_requires_escalation`.
  - Tracy: `61 escalated` with `provenance_requires_escalation`.
- Interpretation: live ZIP expansion and Scott -> Vlad/Tracy routing did not stall. Tracy provenance escalation is expected for insufficient rights declaration. Vlad escalations are review/validation outcomes for JPEG/raster candidates, not ZIP processing failures.

Bug found and fixed:

- API logs showed intermittent `GET /api/v1/workspace/review-cases` failures from `ensure_split_items(...)` in `/data/symgov/backend/symgov_backend/routes/workspace.py`.
- Error: `sqlalchemy.exc.IntegrityError: duplicate key value violates unique constraint "pk_review_split_items"` during `session.flush()`.
- Root cause: `ensure_split_items` performs read-then-insert for deterministic split-item IDs. Concurrent review-cases requests can both observe a missing split item, both add the same deterministic ID, and the loser hits the primary-key race at flush.
- Fix: `ensure_split_items` now catches only duplicate-key `IntegrityError`s for `review_split_items` (`pk_review_split_items` / `uq_review_split_items_case_child`), rolls back the failed read transaction, reloads the already-created split items by deterministic ID, and returns them. Other integrity errors are still raised.
- Added regression test: `/data/symgov/tests/test_workspace_split_items.py` reproduces a concurrent insert between `session.get(...)` and `session.flush()` and proves `ensure_split_items` recovers idempotently.

Verification completed:

- RED regression before fix:
  - `python3 -m pytest tests/test_workspace_split_items.py::test_ensure_split_items_recovers_from_concurrent_duplicate_primary_key_insert -q`
  - Result: failed with `sqlalchemy.exc.IntegrityError` at `ensure_split_items -> session.flush()`.
- Focused GREEN run:
  - `python3 -m pytest tests/test_workspace_split_items.py tests/test_workspace_asset_preview.py -q`
  - Result: `8 passed`.
- ZIP/UI/review focused regression:
  - `python3 -m pytest tests/test_zip_phase2.py tests/test_submission_ui_zip_acceptance.py tests/test_workspace_split_items.py tests/test_workspace_asset_preview.py -q`
  - Result: `14 passed`.
- Full repo test run:
  - `python3 -m pytest -q`
  - Result: `116 passed`.
- Live API rebuild/recreate:
  - `docker compose --project-directory /docker/symgov-hermes build symgov-api`
  - `docker compose --project-directory /docker/symgov-hermes up -d --no-deps --force-recreate symgov-api`
  - Result: `symgov-hermes-api` recreated and started.
- Health and endpoint verification:
  - In-container health: `{"ok":true,"service":"symgov-api","time":"2026-06-16T19:04:44Z"}`.
  - Public health: `{"ok":true,"service":"symgov-api","time":"2026-06-16T19:04:45Z"}`.
  - Public review-cases: `OK (HTTP 200, bytes 564366)`, `review_cases_items=143`.
  - Concurrent public review-cases probe: `10` parallel-ish requests all returned `200`.
  - Recent API logs after restart contained no `pk_review_split_items` duplicate-key trace.
  - In-container import check confirmed active module path `/data/symgov/backend/symgov_backend/routes/workspace.py` includes `is_review_split_item_duplicate_integrity_error(...)`.

Current uncommitted state after this update:

- Modified tracked repo files:
  - `backend/symgov_backend/routes/workspace.py`
  - `backend/symgov_backend/services/external_submissions.py`
  - `docs/plans/2026-06-13-dxf-zip-symbol-intake-implementation-plan.md`
  - `frontend/src/App.jsx`
  - `scripts/run_scott_intake.py`
- New untracked repo files:
  - `tests/test_submission_ui_zip_acceptance.py`
  - `tests/test_workspace_split_items.py`
  - `tests/test_zip_phase2.py`
- External/live files changed outside git from earlier ZIP/upload work remain:
  - `/data/.openclaw/workspaces/scott/run_scott_intake.py`
  - `/data/.openclaw/workspaces/scott/enqueue_scott_downstream.py`
  - `/data/.openclaw/workspace/symgov/backend/symgov_backend/services/external_submissions.py`
  - `/docker/symgov-hermes/nginx.conf`

Updated restart prompt:

Continue Symgov DXF/ZIP symbol intake from `/data/symgov`. Phase 1 direct DXF, Phase 2 ZIP expansion, public ZIP file selection, nginx upload-body sizing, and the review-cases split-item idempotency race fix are implemented and live. First inspect `git status --short --branch`, verify `/data/symgov/scripts/run_scott_intake.py` still matches `/data/.openclaw/workspaces/scott/run_scott_intake.py`, verify `docker exec applications-web grep -n "client_max_body_size" /etc/nginx/conf.d/default.conf`, and re-check `curl -fsS https://apps.chrisbrighouse.com/api/v1/workspace/review-cases`. The live fire-alarms ZIP batch `subext-20260616T185153Z` processed through Scott/Vlad/Tracy; remaining escalations are review/provenance work rather than ZIP intake stalls. Before closing future work, re-run `python3 -m pytest -q`, API health checks, and recent API-log checks for `pk_review_split_items`.

## ZIP individual-symbol package pairing heuristic - 2026-06-16T19:20Z

Issue found:

- The fire-alarms ZIP is not a sheet-only package. Most entries are logical symbol pairs: one DXF plus one same-named JPG preview per symbol.
- Earlier Phase 2 expansion treated every supported ZIP member as an independent child task. Same-named JPG previews were therefore routed to Vlad as standalone raster inputs, where the raster sheet analyzer sometimes proposed split crops. That is wrong for individual-symbol ZIP libraries: the JPG is a companion/preview for the DXF symbol, not a sheet requiring subdivision.

Fix implemented:

- `/data/symgov/scripts/run_scott_intake.py` now groups ZIP members by conservative package-local key: same directory plus same filename stem.
- If a group contains exactly one DXF and exactly one raster preview (`jpeg` or `png`), Scott emits one downstream child task for the DXF primary and attaches the raster as a `package_member_companion` in `companion_files` and `visual_assets.source_assets`.
- The raster companion is preserved in the package manifest with `relationship=companion` and `downstream_role=companion_to_primary_symbol`, but it is not emitted as an independent Scott child task. This prevents Vlad from applying raster sheet splitting to a preview image that belongs to an individual symbol.
- Unpaired raster files are still emitted as standalone child tasks and remain eligible for Vlad raster sheet analysis. This preserves the sheet workflow for genuine sheets of symbols.
- `/data/.openclaw/workspaces/scott/enqueue_scott_downstream.py` now forwards `package_member_relationship`, `package_symbol_grouping`, `companion_files`, and `visual_assets` into Vlad/Tracy payloads so paired DXF/JPG lineage remains visible downstream.

Probe against the live fire-alarms ZIP using the patched Scott classifier:

- Source ZIP: `/data/.openclaw/workspaces/scott/runtime/external_uploads/subext-20260616T185153Z/01-Fire Alarms.zip`.
- Manifest members: `62`.
- Downstream child tasks after pairing: `31`.
- Paired DXF+raster symbol candidates: `30`.
- Standalone raster tasks remaining: `1` (`Elec_FireAlarms_Sounder_Beacon-alt.jpg`).
- Interpretation: the submitted ZIP is overwhelmingly an individual-symbol package, not a raster sheet package. Future submissions with the same structure should now keep each DXF/JPG symbol intact.

Verification completed:

- Focused ZIP regression:
  - `python3 -m pytest tests/test_zip_phase2.py -q`
  - Result: `6 passed`.
- ZIP + DXF focused regression:
  - `python3 -m pytest tests/test_zip_phase2.py tests/test_dxf_phase1.py -q`
  - Result: `13 passed`.
- Full repo tests and syntax check:
  - `python3 -m py_compile /data/.openclaw/workspaces/scott/enqueue_scott_downstream.py /data/symgov/scripts/run_scott_intake.py && python3 -m pytest -q`
  - Result: `117 passed`.
- Live API/runtime refresh:
  - `docker compose --project-directory /docker/symgov-hermes up -d --no-deps --force-recreate symgov-api`
  - Result: `symgov-hermes-api` recreated and started.
- Health and review endpoint checks:
  - In-container health: `{"ok":true,"service":"symgov-api","time":"2026-06-16T19:19:05Z"}`.
  - Public review-cases: `HTTP 200`, `564366` bytes, `review_cases_items=143`.
  - Recent API logs after restart contained no `Traceback`, `ERROR`, `IntegrityError`, or `pk_review_split_items` entries.

Current uncommitted state after this update:

- Modified tracked repo files include:
  - `backend/symgov_backend/routes/workspace.py` (previous review split idempotency fix).
  - `backend/symgov_backend/services/external_submissions.py` (pre-existing ZIP/upload work).
  - `docs/plans/2026-06-13-dxf-zip-symbol-intake-implementation-plan.md`.
  - `frontend/src/App.jsx` (pre-existing UI work).
  - `scripts/run_scott_intake.py` (this ZIP pairing change plus previous ZIP work).
- New/untracked repo tests include:
  - `tests/test_submission_ui_zip_acceptance.py`.
  - `tests/test_workspace_split_items.py`.
  - `tests/test_zip_phase2.py`.
- External/live file changed outside git:
  - `/data/.openclaw/workspaces/scott/enqueue_scott_downstream.py` (forward paired-symbol provenance to Vlad/Tracy).

Updated restart prompt:

Continue Symgov DXF/ZIP symbol intake from `/data/symgov`. Phase 1 direct DXF and Phase 2 ZIP expansion are live. The fire-alarms ZIP revealed that individual-symbol ZIP packages often contain same-stem DXF/JPG pairs that must stay intact rather than sending the JPG through Vlad sheet splitting. Scott now groups exact same-directory/same-stem DXF+raster pairs into one DXF primary child with the raster preserved as a companion; only unpaired rasters still go to Vlad raster sheet analysis. First inspect `git status --short --branch`, verify `/data/symgov/scripts/run_scott_intake.py` and `/data/.openclaw/workspaces/scott/enqueue_scott_downstream.py`, and rerun `python3 -m pytest tests/test_zip_phase2.py tests/test_dxf_phase1.py -q`. If testing another live ZIP, expect individual-symbol libraries to produce fewer downstream child tasks than raw member count, with paired previews in `companion_files` rather than standalone Vlad raster split review cases.
