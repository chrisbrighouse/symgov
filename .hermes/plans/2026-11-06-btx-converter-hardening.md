# BTX converter hardening implementation plan

> For Hermes: use subagent-driven-development with exact-current spec review followed by quality/security review.

Goal: repair confirmed BTX conversion correctness and safety defects without deploying or changing live records.
Architecture: retain the bounded BTX v1 stamp-snapshot converter and existing manifest/review contracts. Add structured failures and deterministic safe output allocation. Correct supported basic geometry; reject or warn on unsupported rendering rather than expanding claims to general PDF support.
Tech stack: Python, ElementTree, zlib, Pillow, pytest.

## Scope freeze
- Worktree /data/symgov-btx-hardening; branch fix/btx-converter-hardening-20261106.
- Immutable starting HEAD c50829377f0efc0b415a5fad4c8a8c97b9c51d25.
- Primary /data/symgov has unrelated changes (including raster segmentation changes in Vlad). They are protected and not copied into this clean-HEAD worktree.
- Allowed paths: backend/symgov_backend/services/btx_converter.py; scripts/run_vlad_validation.py; tests/test_btx_integration.py; new tests/test_btx_converter_hardening.py; new tests/test_btx_validation_outcomes.py; docs/btx-submission-workflow.md; this plan.
- No commits until parent chooses; no merge/cherry-pick/push, no deployment, restart, DB/storage writes, seeding, or live reprocess.
- All tests isolated with network disabled and only /root/.hermes/cache/scratch/btx-hardening-20261106 writable; no reading secrets.

## Task 1: truthful validation outcome (independent runner slice)
Files scripts/run_vlad_validation.py and tests/test_btx_validation_outcomes.py.
RED: mock convert_btx with manifest 0 successes/30 failures and assert fail, attributable defect, failed evidence, no downstream review/Libby children. Also empty library must not pass. Partial success must escalate with warning/defect evidence of failed entries and retain successful children.
GREEN: explicitly fail all-failed/empty conversion and label evidence truthfully; preserve successful review and source provenance.
Verify: isolated tests/test_btx_integration.py plus tests/test_btx_validation_outcomes.py, syntax and diff checks. Return test evidence; no commit.

## Task 2: bounded parser, filenames, geometry and faithful basic rendering (converter slice)
Files backend/symgov_backend/services/btx_converter.py and tests/test_btx_converter_hardening.py.
RED: adopt probes from /root/.hermes/cache/scratch/btx-review-20261106/test_review_probes.py; require A/A/A_2 global unique names across SVG/DXF/PNG; reject DTD/entities for UTF-8/UTF-16 input; cumulative per-symbol segment budget; finite/positive/bounded coordinates, transforms, image allocation; reject truncated zlib. Add tests for fill-only PNG, closed DXF shapes, disconnected subpaths, transformed v, n discard, s/b closepaths, supported styling or explicit warnings.
GREEN: structured expected errors and no ordinary exceptions for adversarial numeric geometry; reserve emitted stems globally case-insensitively; forbid DTD/entities through parser-level or encoding-aware validation; check decompressor EOF; cumulative segment counter; bounded PNG raster dimensions; split subpaths and honor closures; implement safe solid fills (including holes as supported) and strokes or explicitly reject unsupported cases; avoid silent omissions of clipping/dash/graphics state. Preserve metadata, millimetre DXF, exact appearance-resource matching, and fixture symbol counts. Do not add dependencies or claim new BTX types.
Verify: original BTX suite + hardening tests in sandbox, known Doors and existing local corpus where available, verify output contents not just file existence. Return red/green evidence; no commit.

## Review revision: cumulative resource and stream integrity gates
Snapshot 1 passed spec review but failed quality review. Before final acceptance add RED→GREEN tests and fixes for linear/bounded dash token parsing (no nested backtracking regex), dash count and cumulative style/serialization budgets, exact PDF compressed stream byte boundaries (never rstrip binary checksums), per-symbol/library cumulative expansion and resource-count bounds, and structured errors for unknown XML encodings. Include bounded token/operand/warning work where required by the same resource limits. Preserve valid CR/LF-ending zlib streams. Refresh docs with the chosen limits and rerun combined controller tests plus both exact-current review stages; snapshot-1 approval is stale after changes.

Snapshot 2 passed spec review but quality review found quadratic dictionary-array matching before graphics-token accounting. Require bounded/linear first-exact-key Rect/BBox/Matrix/AP lookup, malformed Matrix rejection rather than default, and adversarial public-converter regressions. Snapshot 3 must repeat both review gates after independent canonical tests; previous verdicts do not approve the revised files.

## Task 3: integration, docs and review gates (parent)
Review actual diff and run combined original/new BTX tests plus proportionate workspace/queue tests with no live access. Resolve interactions and current failures through new regressions. Update docs to accurately describe supported features, rejection/warning policy, limits and remaining unsupported semantics. Keep main dirty tree unchanged.
Capture per-file SHA-256 manifest plus immutable patch after changes. Spec reviewer must review the exact frozen snapshot against these tasks; after PASS, quality/security reviewer reviews the same snapshot. Fix important findings, rerun tests, capture new snapshot and repeat both stages. No approval claim for stale snapshots.
Final deliverable: local patch/worktree with executed test outcomes, exact-current review verdicts, preserved main state, and clearly deferred production E2E/deploy approval gate. Full production DB/storage/UI test is not authorized in this phase.
