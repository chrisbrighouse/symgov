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
