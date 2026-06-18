# Vlad hardening closeout and restart note

Updated: 2026-06-18T08:24:54Z

## Purpose

This note closes out the Vlad hardening pass requested by Chris: remove legacy runner drift carefully, keep Vlad's runtime/data requirements intact, prefer Gemini over Gemma/Gemma4 when available, and record the remaining design-review work for the next continuation.

## Current live status

- Live API container: `symgov-hermes-api` is healthy.
- Public API health endpoint is healthy.
- Canonical Vlad runner is now repo-managed at:
  - `/data/symgov/scripts/run_vlad_validation.py`
- Legacy workspace runner has been removed:
  - `/data/.openclaw/workspaces/vlad/run_vlad_validation.py` -> absent
- Vlad runtime/history workspace is preserved:
  - `/data/.openclaw/workspaces/vlad/runtime`
- Live `agent_definitions` row for Vlad resolves to:
  - `vlad | technical validation and graphic-quality agent | gemini/gemini-2.5-flash`
- Container-side model resolution confirms:
  - `resolve_vlad_agent_model()` -> `gemini/gemini-2.5-flash`
- Container-side path check confirms:
  - repo runner exists: `True`
  - legacy runner exists: `False`

## Completed changes

1. Removed the legacy/stale Vlad runner from the workspace path so operators and workers do not accidentally run divergent code.
2. Preserved the Vlad workspace runtime directory; only stale logic was removed.
3. Refactored backend agent seed resolution so Vlad's model is resolved dynamically.
4. Added runtime model/key helpers in Vlad:
   - `get_gemini_api_key()`
   - `resolve_vlad_model()`
5. Gemini preference order:
   - explicit `SYMGOV_VLAD_MODEL`, if set;
   - `gemini/gemini-2.5-flash` when `SYMGOV_GEMINI_API_KEY` or `GEMINI_API_KEY` exists;
   - fallback `ollama/gemma4:e4b` when Gemini is unavailable.
6. Updated Vlad's Gemini image-edit helper to use the same Gemini key lookup path.
7. Added regression coverage in:
   - `/data/symgov/tests/test_vlad_hardening.py`
8. Updated documentation that previously pointed at the legacy Vlad runner:
   - `/data/symgov/backend/README.md`
   - `/data/symgov/symgov-agent-architecture.md`
9. Recreated the Hermes-native API container so the live API picked up the updated code/env.
10. Reseeded live agent definitions so Vlad's DB model field reflects Gemini availability.

## Verification performed

### Focused regression tests

Command:

```bash
cd /data/symgov
pytest tests/test_vlad_hardening.py tests/test_dxf_phase1.py tests/test_zip_phase2.py -q
```

Result:

```text
20 passed in 0.94s
```

### Full backend test suite

Command:

```bash
cd /data/symgov
pytest tests -q
```

Result:

```text
140 passed in 2.00s
```

### Host Vlad smoke test

A minimal accessible SVG was sent through the repo-managed Vlad runner.

Result:

```text
decision pass
agent vlad
checks integrity,svg_parse,accessibility,geometry
artifact_count 0
```

### Container Vlad smoke test

A minimal accessible SVG was sent through the repo-managed Vlad runner inside `symgov-hermes-api`.

Result:

```text
decision pass
agent vlad
checks integrity,svg_parse,accessibility,geometry
artifact_count 0
```

### Live health checks

Command:

```bash
docker exec symgov-hermes-api sh -lc 'curl -fsS http://127.0.0.1:8010/api/v1/health && echo'
curl -fsS https://apps.chrisbrighouse.com/api/v1/health
```

Result:

```text
{"ok":true,"service":"symgov-api","time":"2026-06-18T08:24:09Z"}
{"ok":true,"service":"symgov-api","time":"2026-06-18T08:24:09Z"}
```

### Live model/path checks

Command:

```bash
docker exec symgov-hermes-api python -c "from symgov_backend.runtime import resolve_vlad_agent_model; from pathlib import Path; print('vlad_model', resolve_vlad_agent_model()); print('repo_runner_exists', Path('/data/symgov/scripts/run_vlad_validation.py').exists()); print('legacy_runner_exists', Path('/data/.openclaw/workspaces/vlad/run_vlad_validation.py').exists())"
```

Result:

```text
vlad_model gemini/gemini-2.5-flash
repo_runner_exists True
legacy_runner_exists False
```

### Live DB agent definition check

Command:

```bash
docker exec symgov-postgres psql -U symgov_app -d symgov -Atc "SELECT slug, role, model FROM agent_definitions WHERE slug='vlad';"
```

Result:

```text
vlad|technical validation and graphic-quality agent|gemini/gemini-2.5-flash
```

## Repo / uncommitted state at closeout

Branch state before this note was written:

```text
## main...origin/main [ahead 3]
 M backend/README.md
 M backend/symgov_backend/runtime.py
 M scripts/run_vlad_validation.py
 M symgov-agent-architecture.md
?? tests/test_vlad_hardening.py
```

Diff summary before this note was written:

```text
backend/README.md                 |   3 +-
backend/symgov_backend/runtime.py | 188 +++++++++++++++++++++-----------------
scripts/run_vlad_validation.py    |  24 ++++-
symgov-agent-architecture.md      |   2 +-
4 files changed, 128 insertions(+), 89 deletions(-)
```

This closeout note itself is also an intended new repo file:

```text
docs/plans/2026-06-18-vlad-hardening-closeout.md
```

No commit or push was performed in this closeout step.

## External/non-repo state changed

- Removed stale legacy runner:
  - `/data/.openclaw/workspaces/vlad/run_vlad_validation.py`
- Preserved runtime data:
  - `/data/.openclaw/workspaces/vlad/runtime`
- Updated secret-bearing Gemini env material for the API container without printing the secret:
  - `/docker/openclaw-hz0t/symgov-gemini.env`
- Recreated live API container:
  - `symgov-hermes-api`
- Reseeded agent definitions in live DB.

## Design position after hardening

Vlad is now cleaner operationally:

- There is one canonical code path for Vlad validation logic.
- Vlad can use Gemini where available without becoming dependent on it.
- Deterministic engineering checks remain first-class:
  - SVG integrity/XML/accessibility/geometry checks;
  - raster analysis/sheet splitting;
  - DXF parsing/auditing and preview derivative handling;
  - structured evidence traces.
- LLM capability is treated as an enhancement layer, not the source of approval.
- Vlad still does not publish and should not silently approve; he passes, fails, or escalates for review.

## Remaining hardening priorities

1. DXF preview/rendering quality
   - Confirm DXF derivatives are true `ezdxf`-rendered technical previews rather than placeholders.
   - Preserve companion JPG/PNG preview precedence for same-stem `DXF + JPG/PNG` symbol candidates.
   - Ensure review workspace surfaces available formats cleanly: DXF / JPG / SVG where applicable.

2. Raster split confidence
   - Strengthen thresholds that decide whether a raster is a multi-symbol sheet or standalone symbol.
   - Add regression cases for standalone ZIP raster companions so Vlad does not over-split them into letter/fragments.
   - Keep `raster_sheet_analysis` off companion images that are only visual previews for paired DXF symbols.

3. Canonical IDs in all outputs
   - Ensure every Vlad output and downstream queue/review card carries package/symbol IDs like `0003-12` when available.
   - Avoid generic Vlad crop/region placeholder names becoming operator-facing labels.

4. Graphic-change scope and review gates
   - Review exactly which automatic graphic changes Vlad is allowed to make.
   - Keep any destructive/significant edits behind explicit review gates.
   - Prefer deterministic normalisation and derivative generation over LLM-driven visual edits unless the task is explicitly review-bound.

5. Operational runbook cleanup
   - Remove remaining stale references to the old workspace runner if any are found outside the files already updated.
   - Document the canonical runner, runtime workspace boundary, model fallback behavior, and smoke-test commands.

## Suggested next restart prompt

```text
Continue the Vlad design review and hardening from /data/symgov/docs/plans/2026-06-18-vlad-hardening-closeout.md.

Start by reading the closeout note and verifying current state:
- git status --short --branch
- pytest tests/test_vlad_hardening.py tests/test_dxf_phase1.py tests/test_zip_phase2.py -q
- docker exec symgov-hermes-api python -c "from symgov_backend.runtime import resolve_vlad_agent_model; from pathlib import Path; print('vlad_model', resolve_vlad_agent_model()); print('repo_runner_exists', Path('/data/symgov/scripts/run_vlad_validation.py').exists()); print('legacy_runner_exists', Path('/data/.openclaw/workspaces/vlad/run_vlad_validation.py').exists())"

Then continue with the remaining Vlad hardening priorities in this order:
1. DXF preview/rendering quality and companion preview precedence.
2. Raster split confidence thresholds and ZIP companion-image regression tests.
3. Canonical readable IDs in all Vlad outputs and downstream cards.
4. Graphic-change permissions and review gates.

Keep Vlad deterministic-first; Gemini may enhance quality/consistency when available, but must not replace deterministic checks or silently approve/publish anything.
```
