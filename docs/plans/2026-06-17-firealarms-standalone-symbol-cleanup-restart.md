# FireAlarms ZIP standalone-symbol cleanup restart note

Date: 2026-06-17

## Status

Done.

The FireAlarms ZIP intake path now marks each ZIP member task as an already-isolated package symbol unless it is explicitly a same-stem DXF+raster companion pair. This prevents JPEG/PNG symbol files such as `Elec_FireAlarms_Detector_Heat_RateOfRise.jpg` from being treated as multi-symbol raster sheets and split into meaningless letter crops such as `H` and `R`.

Code changed in repo:
- `scripts/run_scott_intake.py`
  - Adds `standalone_package_symbol_file` / `standalone_symbol_file` markers to ZIP child tasks.
  - Existing DXF+raster companion pairing still overrides the standalone marker with `paired_dxf_raster_symbol` / `primary_with_companion`.
- `scripts/run_vlad_validation.py`
  - Honors standalone package symbol metadata and does not auto-add `raster_sheet_analysis` for those raster files.
  - Carries package member metadata into normalized Vlad output.
- `scripts/run_libby_classification.py`
  - Treats `standalone_package_symbol_file` as strong single-symbol evidence.
- `tests/test_zip_phase2.py`
  - Adds FireAlarms regression coverage for `Elec_FireAlarms_Detector_Heat_RateOfRise.jpg` and downstream Vlad expected checks.
- `tests/test_vlad_standalone_package_symbols.py`
  - Adds direct Vlad regression proving a standalone FireAlarms JPEG runs integrity-only and does not decode/split as a sheet.

External runtime copies synchronized:
- `/data/.openclaw/workspaces/scott/run_scott_intake.py` now matches repo `scripts/run_scott_intake.py`.
- `/data/.openclaw/workspaces/libby/run_libby_classification.py` now matches repo `scripts/run_libby_classification.py`.
- `/data/.openclaw/workspaces/scott/enqueue_scott_downstream.py` was updated in-place to emit `expected_checks: ["integrity"]` for standalone package JPEG/PNG symbols.

The API container was restarted and health checked:
- `docker restart symgov-hermes-api`
- Health status: `healthy`

## Fire Alarms cleanup performed

Before destructive cleanup, a database backup and file/object backups were saved under:
- `/data/symgov/docs/ops/backups/firealarms-cleanup-20260617T100340Z/`

Backup files:
- `symgov-data-before-firealarms-cleanup.sql.gz`
- `runtime-firealarms-files.tar.gz`
- `minio-firealarms-objects.tar.gz`

Deleted from Postgres:
- `review_symbol_properties`: 77
- `review_split_items`: 76
- `review_cases`: 29
- `provenance_assessments`: 92
- `validation_reports`: 92
- `intake_records`: 94
- `source_packages`: 94
- `audit_events`: 1
- `agent_output_artifacts`: 401
- `agent_runs`: 278
- `agent_queue_items`: 278
- `attachments`: 242

Deleted/moved from file-backed runtime after tar backup:
- Selected FireAlarms runtime paths: 2472
- Removed files: 2205
- Removed directories: 267

Deleted/moved from MinIO host storage after tar backup:
- Selected FireAlarms object directories: 148

Post-cleanup verification returned zero FireAlarms matches in:
- `source_packages`
- `intake_records`
- `agent_queue_items`
- `agent_output_artifacts`
- `validation_reports`
- `provenance_assessments`
- `review_split_items`
- `review_symbol_properties`
- `attachments`
- `/data/.openclaw/workspaces/{scott,vlad,tracy,libby}/runtime`
- `/docker/symgov-minio/data`

## Verification commands/results

Focused tests:

```bash
pytest -q tests/test_zip_phase2.py tests/test_vlad_standalone_package_symbols.py tests/test_libby_symbol_vision.py tests/test_dxf_phase1.py
```

Result:

```text
20 passed in 0.85s
```

Local smoke test for a one-file `FireAlarms.zip` containing `Elec_FireAlarms_Detector_Heat_RateOfRise.jpg`:

```text
scott_child_grouping= standalone_package_symbol_file
scott_child_relationship= standalone_symbol_file
vlad_expected_checks= ['integrity']
vlad_decision= pass
trace_checks= ['integrity']
```

Post-cleanup SQL verification:

```text
source_packages          0
intake_records           0
agent_queue_items        0
agent_output_artifacts   0
validation_reports       0
provenance_assessments   0
review_split_items       0
review_symbol_properties 0
attachments              0
```

Runtime and MinIO path verification:

```text
/data/.openclaw/workspaces/scott/runtime 0 []
/data/.openclaw/workspaces/vlad/runtime 0 []
/data/.openclaw/workspaces/tracy/runtime 0 []
/data/.openclaw/workspaces/libby/runtime 0 []
/docker/symgov-minio/data FireAlarms matches: 0
```

## Uncommitted state

This session's direct code/test changes are in:
- `scripts/run_scott_intake.py`
- `scripts/run_vlad_validation.py`
- `scripts/run_libby_classification.py`
- `tests/test_zip_phase2.py`
- `tests/test_vlad_standalone_package_symbols.py`
- `docs/plans/2026-06-17-firealarms-standalone-symbol-cleanup-restart.md`
- `docs/ops/backups/firealarms-cleanup-20260617T100340Z/`

There were already other uncommitted repo changes before this work began, including:
- `backend/symgov_backend/agent_queue_worker.py`
- `backend/symgov_backend/routes/workspace.py`
- `docs/plans/2026-06-13-dxf-zip-symbol-intake-implementation-plan.md`
- `frontend/src/App.jsx`
- `tests/test_hannah_worker_throttle.py`
- `tests/test_review_symbol_name_defaults.py`
- `.hermes/plans/2026-06-17_081344-libby-filename-classification-plan.md`
- `tests/test_review_symbol_property_seeding.py`
- `tests/test_submission_ui_zip_acceptance.py`
- `tests/test_workspace_split_items.py`

## Next actions

1. Re-submit `FireAlarms.zip` through the normal UI.
2. Confirm the Heat Rate Of Rise symbol appears as one review item and does not create H/R split children.
3. If the ZIP contains both DXF and JPG variants of the same symbol, confirm they are paired as one symbol candidate rather than producing duplicate review items.
4. Commit this cleanup/fix separately from the older unrelated uncommitted changes if you want a clean git history.

## Restart prompt

Continue from `/data/symgov/docs/plans/2026-06-17-firealarms-standalone-symbol-cleanup-restart.md`. Verify the FireAlarms ZIP standalone-symbol fix and cleanup. Start by checking `git status --short`, then run `pytest -q tests/test_zip_phase2.py tests/test_vlad_standalone_package_symbols.py tests/test_libby_symbol_vision.py tests/test_dxf_phase1.py`. If needed, re-check the Postgres FireAlarms cleanup counts and the runtime/MinIO path searches documented above.
