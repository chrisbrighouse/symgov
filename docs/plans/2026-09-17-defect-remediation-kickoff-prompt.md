# Defect remediation kickoff prompt (2026-09-17)

Copy the "Prompt" section at the end of this file verbatim into a new Claude
Code session started in `/docker/openclaw-hz0t/data/symgov`.

This pack differs from the SM-P1-01 packs before it: it is **not a work
package**. There is no controlling plan and no acceptance criteria to meet.
It is a restart pack for working the open-defect list in
`docs/handover/2026-09-14-symgov-state-of-play.md` §4, which is the
authoritative inventory and is kept annotated as items close.

---

## 0. State at handover

`main` is at **`e1873fd`**, clean apart from a long-standing modification to
`.claude/settings.local.json` that predates all of this work and must stay
untouched. **`main` is level with `origin/main` — everything is pushed.**

Four commits landed on 2026-09-16/17, in order:

| SHA | What |
| --- | --- |
| `3e1776b` | Split child's classification resolved by manifest ordinal (§4.1 item 2) |
| `19bc1e5` | Merge of PR #2, the ISO ICS taxonomy branch |
| `c76c315` | Catalogue facets match named payload fields (§4.2 item 3) |
| `e1873fd` | Catalogue free-text `q` filter, same fix |

Gates actually run, not inferred: `scripts/test-backend.sh` at **4119 passed,
3 skipped, 3 deselected** (24:34) on `e1873fd`, real PostgreSQL included;
**363 frontend** on `c76c315`. Single alembic head `20260916_0059`.

**Read `full_backend_sweep_hangs_on_dxf_module` in memory before trusting any
full-suite number.** `scripts/test-backend.sh` is the supported runner; its
portable default timeout is 1800s against a ~24:30 actual, which is thin
headroom. It takes no subcommand — `sh scripts/test-backend.sh` runs the
portable set, `--external` and `--full` are the only arguments.

## 1. Decisions already taken — do not reopen

**D1 (Chris, 2026-09-16): a catalogue facet matches named payload fields.**
Not the serialized document, and not "prefer the assignment, fall back to
text". Applied to all five payload-matching facets and then to `q`. The
root cause worth carrying: `CAST(payload_json AS TEXT)` renders JSON **keys**
beside values, so `Equipment` matched the key `parent_equipment_class` and
`Process` matched `process_category` in every payload — all 84 published
symbols. **If you add a payload-reading filter, name the field.**

**D2 (Chris, 2026-09-16): the child-index defect was the first item taken**,
ahead of activating the SM-P1-01 flag, because it was live and reachable.

**D3 (Chris, 2026-09-16): PR #2 was merged with `--merge`**, not squashed, so
`e6cd860` and its three siblings keep the hashes the ICS runbook cites.

## 2. Open, and genuinely Chris's to answer

1. **ICS source licensing.** `backend/symgov_backend/data/ICS.csv` is now on
   public `main`, 1382 lines, merged before the licensing question was
   settled. Raised three times and never answered. If it turns out to be
   wrong, removing it is a history rewrite on a public repo, not a revert.
2. **`source_file` searchability.** The one recall `e1873fd` deliberately
   drops from `q`. Restore it if operators search by contributor filename.
3. **`classification_records.industry`** — §4.2 item 4. Open and unscoped:
   whether `industry` is an axis at all, or should be retired in favour of
   the `ENGINEERING-DISCIPLINE` scheme it duplicates. Logged at Chris's
   instruction in `docs/plans/2026-09-11-classification-industry-field-defect.md`.
4. **Activating SM-P1-01.** Complete in the repository since 2026-09-14 and
   never switched on, so no reviewer has ever used the review surface. §5 of
   the state-of-play warns against planning the ingestion connector before it
   is in use, or the first ingest is all-waiver or all-blocked. This is the
   gate on the whole P1/P2 line.

## 3. What is left on the §4 list

Closed: §4.1 item 2, §4.2 item 3, and the `q` filter that item 3's fix
exposed. Still open, in the doc's own numbering:

- §4.1 item 1 — `propose_external_mapping` returns 500 on a duplicate active
  mapping instead of 409/422. Not reachable from any UI, so low urgency.
- §4.2 item 4 — `industry`, above.
- §4.3 items 5–9 — the structural gaps: three seeded schemes with no writer
  or reader; §7.3 `SemanticConceptRelationship` has no table and is in no
  work package; `publication_gate_evaluations` is write-only;
  the publication gate's scope is unreachable in production;
  `ConceptClassificationAssignment` has a queue read and no decision route.
- §4.4 item 10 — nobody has written down what database role the deployment
  actually runs as. Three production failures on 2026-09-07 came out of that
  blind spot.

`has_preview` is **not** a defect: it matches `'%preview%'` against the
document on purpose, because it is looking for the `preview_object_key` key.

## 4. Verification still outstanding

**Neither catalogue fix has been measured against production.** The 84-symbol
figure that defined the defect was measured on 2026-09-11 against production,
and the standing advice in the `catalogue_facet_overmatching_defect` memory is
to measure facets both ways before and after. Production is still on the
earlier release, so nothing there has changed yet — the measurement is only
possible after a deploy, and a deploy needs Chris's explicit approval.

## 5. Environment traps

- **The Bash sandbox does not start.** Every sandboxed command dies with
  `bwrap: Can't create file at <path>: Permission denied` on a protected
  deny-path that does not exist; creating one just advances it to the next
  (`.mcp.json` → `.git/config.lock` → `.gitconfig`). `sandbox.enabled: true`
  comes from Idox's remote-managed settings and `/sandbox` refuses to
  override it. Everything runs with `dangerouslyDisableSandbox: true`.
- **`$TMPDIR` is unset when the sandbox is off.** `cd "$TMPDIR"` silently
  becomes `cd ""` and writes into the repo root. Write scratch paths in full.
- **`gh` is installed** at `/usr/local/bin/gh`, authenticated as
  `chrisbrighouse`. `gh pr merge` is refused by the permission classifier as
  "Merge Without Review" — ask Chris to merge rather than working around it.
- **The HTTP client is `httpx2`, never `httpx`.**

---

## Prompt

> Work from `/docker/openclaw-hz0t/data/symgov` on the Symgov repository.
>
> Read `docs/plans/2026-09-17-defect-remediation-kickoff-prompt.md` first —
> it is the restart pack for this session and records the state, the
> decisions already taken, and the environment traps. Then read §4 of
> `docs/handover/2026-09-14-symgov-state-of-play.md`, which is the
> authoritative open-defect inventory and is annotated as items close.
>
> `main` is at `e1873fd` and level with `origin/main`. The working tree is
> clean apart from a pre-existing `.claude/settings.local.json` change that
> must stay untouched.
>
> Do not push, deploy, run migrations, or apply anything to a live database
> without Chris's explicit approval for that specific operation. Do not open
> or merge a pull request without being asked.
>
> Before starting work, tell me which §4 item you propose to take and why,
> and put any decision that is genuinely mine to me before you write code —
> §2 of the pack lists the four that are already waiting on me. Where an item
> needs a product decision, ask; where it is self-contained, recommend and
> proceed.
>
> Gate any change with `sh scripts/test-backend.sh` (it takes no subcommand;
> ~24:30, run it in the background) and `npm run test:frontend` where the
> frontend is touched. Report the numbers you actually ran, and inspect
> `git status` and the diff before reporting completion.
