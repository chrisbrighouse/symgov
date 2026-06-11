# Restart notes: Scott source recommendations

## Status
- Encoded Chris's Scott source recommendation into Symgov source-discovery seeds and source-search defaults.
- Standards/taxonomy backbone now prioritises IEC 60617, ISO 14617 / ISO 1101, ISA-5.1, and ASME Y14.5.
- Candidate/practical intake sources now include ProjectMaterials, Vista Projects, NECA 100, QElectroTech, readable GD&T references (Keyence, GD&T Basics), FreeCAD resources, and a representative manufacturer CAD-library aggregator.
- ProjectMaterials is marked as the immediate practical P&ID seed source, but explicitly as candidate/intake only; prompts and evidence require mapping back to ISA-5.1 / ISO 14617.
- Downloadable CAD/manufacturer libraries are marked candidate/reference only, not included in the next run by default, and explicitly require rights/reuse/provenance/standards checks.
- Wikimedia Commons was demoted from primary default seed to supplemental candidate source.
- Live Scott source memory was reseeded inside `symgov-hermes-api` and the API container was restarted; health returned `healthy`.
- Frontend was rebuilt and published to both `/data/symgov` and `/data/.openclaw/workspace/symgov`.

## Changed files
- `backend/symgov_backend/runtime.py`
- `backend/symgov_backend/routes/workspace.py`
- `backend/symgov_backend/workspace.py`
- `frontend/src/App.jsx`
- `tests/test_scott_source_recommendations.py`
- `README.md`
- `backend/README.md`
- generated/published frontend output under `dist/` and public roots was refreshed by `npm run publish:static`.

## Verification
- `uv run --with pytest --with-requirements backend/requirements.txt pytest tests/test_agent_queue_state_machine.py tests/test_hannah_queue_cards.py tests/test_published_symbol_feedback.py tests/test_published_symbol_review_workflow.py tests/test_scott_source_recommendations.py -q` passed: 29/29.
- `node --test frontend/src/timerControls.test.js tests/test_build_stamp.mjs` passed: 8/8.
- `npm run build` passed.
- `npm run publish:static` published to both static roots.
- `docker restart symgov-hermes-api` completed and health returned `healthy`.
- API check showed top Scott sources now include `projectmaterials.com`, `webstore.iec.ch`, `isa.org`, `iso.org`, `asme.org`, `vistaprojects.com`, and `qelectrotech.org`, with recommended first-pass sources marked `includeNextRun=True`.
- Live public frontend now references asset `index-DC-s8O7l.js` and build stamp `2026-06-11.01`.

## Additional fix: descriptive symbol names
- Fixed single-symbol review defaults so `review_symbol_properties.name` is no longer seeded from the short package id (`000A`, `000D`, etc.) when classification metadata or the origin filename can produce a descriptive name.
- Added `tests/test_review_symbol_name_defaults.py` covering classification symbol-key and filename fallback behavior.
- Backfilled existing package-id names in the live database to descriptive labels. Recent QElectroTech records now show names such as `Qet Hydraulic Fixed Displacement Pump`, `Qet Pressure Switch No`, and `Qet Solenoid Valve` while keeping the short id as `displayName`.
- Recreated `symgov-hermes-api`; local and public health endpoints both returned `{"ok":true,"service":"symgov-api"...}`.
- Full backend unittest discovery passed: `python -m unittest discover -s tests -p "test_*.py" -v` ran 65 tests OK.

## Uncommitted state
- Working tree has the Scott source recommendation changes above and this restart note.
- No commit or push has been performed for this change yet.

## Next actions
- If Chris wants, commit and push the Scott source recommendation update.
- Optionally start a Scott source search from the Sources tab; the frontend now sends the standards/practical-source query instead of the old Wikimedia-only seed.

## Restart prompt
Continue from `/data/symgov`. Review `docs/plans/2026-06-11-scott-source-recommendations-restart.md`, run `git status --short --branch`, optionally re-check Scott sources through `docker exec symgov-hermes-api curl http://127.0.0.1:8010/api/v1/workspace/scott/source-sites?...`, then commit/push if requested.
