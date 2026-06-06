# Symgov architectural status and restart plan

Last updated: 2026-06-06T12:00:03Z  
Prepared by: Alfi / COO review  
Repository: `/data/symgov`  
Live API container: `symgov-hermes-api`  
Database container: `symgov-postgres`

## Executive summary

Symgov now has the right architectural backbone for building a governed engineering-symbol library: a PostgreSQL-backed system of record, separate Governance Workspace and Standards View concerns, durable agent queues, review split items, publication records, audit events, and specialist agents for intake, validation, provenance, classification, review coordination, publication, curation, market intelligence, and UX.

The main architectural risk is no longer whether the system can ingest and publish symbols. It is whether the operating model can keep state clear, catalogue quality high, duplicates out of Standards, and each agent improving from corrections. The immediate focus should therefore be:

1. **Stabilise lifecycle/state clarity** across review split items, agent queues, and publication handoff.
2. **Close duplicate and exception loops** now that Rupert can block graphical duplicates and Libby can triage them.
3. **Introduce catalogue quality scoring** so publication count does not hide weak or generic records.
4. **Give each agent measurable improvement goals** and feed human/automation corrections back into agent guidance.
5. **Add a Reggie-style control/audit layer** to detect stale queues, weak records, and DB/runtime divergence.

## Current live status snapshot

### API health

Verified at 2026-06-06T12:00Z:

```text
Internal API: {"ok":true,"service":"symgov-api","time":"2026-06-06T12:00:26Z"}
Public API:   HTTP 200 at https://apps.chrisbrighouse.com/api/v1/health
```

### Worker runtime configuration

Live container environment:

```text
SYMGOV_ENABLE_AGENT_WORKERS=1
SYMGOV_AGENT_WORKERS=scott,vlad,tracy,libby,daisy,rupert,ed,hannah
SYMGOV_AGENT_RUNTIME=direct
SYMGOV_AGENT_WORKER_DRAIN=1
```

This is the correct current direction: live workers run in `direct` mode inside `symgov-hermes-api`; do not rely on an unavailable in-container `hermes` CLI.

### Agent queue status

Live queue counts from `agent_queue_items`:

```text
slug     | status           | count
---------+------------------+------
daisy    | cancelled        | 3
daisy    | completed        | 9
hannah   | cancelled        | 3
hannah   | completed        | 54
hannah   | progress_saved   | 11
libby    | cancelled        | 1
libby    | completed        | 64
libby    | escalated        | 41
rupert   | cancelled        | 1
rupert   | completed        | 62
scott    | cancelled        | 1
scott    | completed        | 10
scott    | progress_saved   | 13
tracy    | escalated        | 8
vlad     | completed        | 5
vlad     | escalated        | 7
whitney  | cancelled        | 1
whitney  | signals_recorded | 2
```

Interpretation:

- There is no broad `queued` backlog in this snapshot.
- There are meaningful exception backlogs in Libby, Tracy, and Vlad.
- Hannah and Scott have `progress_saved` states that should be checked for terminal/follow-up semantics so they do not look active forever.
- Daisy has only completed/cancelled queue rows, but `review_split_items` still show Daisy-owned awaiting decisions and duplicate exceptions. Daisy's visible work is mostly in review split state, not queue rows.

### Review split item status

Live counts from `review_split_items`:

```text
status               | downstream_agent_slug | count
---------------------+-----------------------+------
awaiting_decision    | daisy                 | 18
awaiting_decision    |                       | 55
deleted              | libby                 | 2
deleted              |                       | 41
duplicate_exception  | daisy                 | 4
duplicate_resolved   |                       | 1
published            | rupert                | 59
queued_rupert        | rupert                | 2
rejected             |                       | 3
returned_for_review  |                       | 3
```

Interpretation:

- Review throughput is now the main visible bottleneck: 73 `awaiting_decision` items remain.
- There are 4 duplicate exceptions requiring Daisy/human review.
- 2 items remain `queued_rupert`; these should be reconciled as either truly pending publication, already published, or duplicate-pending.
- 59 split items are marked published, while a published-symbol query over current revisions returned 54. This mismatch should be reviewed during publication/status reconciliation.

### Duplicate-gate status

Rupert graphical duplicate detection and Libby duplicate triage are now implemented.

Live duplicate-resolution queue counts:

```text
Libby duplicate_resolution queue:
completed | 1
escalated | 4
```

Live split outcomes:

```text
duplicate_exception | daisy | 4
duplicate_resolved  |       | 1
```

Interpretation:

- The automated duplicate gate is working conservatively.
- Libby confirmed one strong duplicate automatically and escalated ambiguous cases.
- Daisy/human review now needs a clear duplicate-exception workflow so these do not stagnate.

### Published catalogue snapshot

A simple query over current published revisions returned:

```text
published_symbols = 54
current_revision payload category   = [blank] for 54
current_revision payload discipline = [blank] for 54
```

This does **not** necessarily mean the UI has no metadata, because category/discipline may also live in classification records or other structured outputs. It does mean the publication-quality model should explicitly define the canonical source of published taxonomy and ensure Standards payloads expose it consistently.

### Repository state

Current branch:

```text
main
```

Uncommitted source changes at time of documentation:

```text
M  backend/symgov_backend/agent_queue_worker.py
M  backend/symgov_backend/publication_handoff.py
M  backend/symgov_backend/routes/workspace.py
M  backend/symgov_backend/runtime.py
M  scripts/run_hannah_curation.py
?? tests/test_hannah_queue_cards.py
?? tests/test_hannah_worker_throttle.py
?? tests/test_libby_duplicate_triage.py
?? tests/test_publication_handoff_split_status.py
```

Diff stat:

```text
backend/symgov_backend/agent_queue_worker.py  |  41 +++++-
backend/symgov_backend/publication_handoff.py |  78 +++++++++++
backend/symgov_backend/routes/workspace.py    | 120 +++--------------
backend/symgov_backend/runtime.py             | 107 +++++++++++++++
scripts/run_hannah_curation.py                | 180 +++++++++++++++++++++++++-
5 files changed, 419 insertions(+), 107 deletions(-)
```

Last verified test status from the implementation session:

```text
Ran 30 tests
OK
```

Before committing, re-run the suite inside the live API container or development environment.

## Work completed in this session

### Hannah active curation hardening

Hannah is now treated as a bounded active worker rather than an uncontrolled broad search process.

Implemented/verified behaviours:

- The curation search endpoint seeds individual `published_symbol_photo_enrichment` queue cards rather than launching one large free-running search.
- Eligibility is per published symbol:
  - public published symbol;
  - usable name;
  - category or discipline present;
  - fewer than 2 attached photos;
  - not attempted within the last 7 days;
  - not blocked/unsuitable.
- Worker throttling: Hannah processes exactly one symbol card per tick even when global drain mode is enabled.
- Final queue statuses are constrained to:
  - `success`
  - `candidate`
  - `completed`
  - `blocked`
  - `retry_later`
- Tests added:
  - `tests/test_hannah_queue_cards.py`
  - `tests/test_hannah_worker_throttle.py`

### Rupert graphical duplicate gate

Rupert publication handoff now includes a pre-publication graphical duplicate safety check.

Implemented/verified behaviours:

- Before publication, candidate preview graphics are compared against already published symbol graphics.
- First stage: perceptual dHash distance.
- Second stage: normalized pixel-difference check to reduce false positives.
- Strong duplicate matches block publication.
- Blocked candidates are routed to Libby as `duplicate_resolution` work with evidence.
- Publication does not proceed for blocked duplicate candidates.
- Tests added:
  - `tests/test_publication_handoff_split_status.py`

### Libby duplicate-resolution triage

Libby now handles Rupert duplicate-gate follow-ups.

Implemented/verified behaviours:

- Handles payloads with:
  - `task_type='publication_duplicate_detected'`
  - `libby_follow_up_type='duplicate_resolution'`
- Strong duplicate evidence is automatically resolved:
  - queue item -> `completed`
  - split item -> `duplicate_resolved`
  - outcome -> `duplicate_confirmed` / `do_not_publish`
- Ambiguous duplicate evidence is escalated:
  - queue item -> `escalated`
  - split item -> `duplicate_exception`
  - downstream -> Daisy/human review
- Libby does **not** publish, delete, or overwrite published symbols during duplicate triage.
- Tests added:
  - `tests/test_libby_duplicate_triage.py`

### Runtime persistence and audit trail

`RuntimePersistenceBridge` now has duplicate-resolution persistence support.

Implemented/verified behaviours:

- Libby's duplicate decision is written back to the database.
- Review split status is updated to `duplicate_resolved` or `duplicate_exception`.
- Review-case actions/audit events capture the decision for operator traceability.

### Operational skill update

The `symgov-agent-operations` skill has been updated with:

- Hannah queue card operating rules.
- Hannah cleanup endpoint wrapper pitfall.
- Rupert duplicate-gate pattern.
- Libby duplicate-resolution triage rules.
- Inspection queries for duplicate queues and split outcomes.

## Architectural assessment

### What is strong

1. **Separation of concerns is sound**
   - Standards View is public/latest-approved.
   - Governance Workspace owns internal state, evidence, review, and exceptions.
   - Agent queues are separate from published catalogue state.

2. **Specialist-agent model matches the library goal**
   - Scott, Vlad, Tracy, Libby, Daisy, Rupert, Hannah, Whitney, and Ed each map to a real operating function.
   - This is the right approach for building a large governed library: no single agent should own intake, provenance, graphics, classification, publication, curation, and audit.

3. **Publication safety is improving**
   - Rupert now blocks likely graphical duplicates before Standards publication.
   - Libby now handles duplicate triage conservatively.

4. **Hannah is moving toward safe catalogue curation**
   - Bounded queue cards and cooldowns are safer than broad unattended searches.
   - This fits the post-publication enrichment role.

5. **The database is increasingly authoritative**
   - Agent outputs and queue state are being written through to PostgreSQL.
   - Runtime JSON is becoming evidence/operational substrate rather than the sole source of truth.

### Main weaknesses / risks

1. **Review split lifecycle is still too implicit**
   - `awaiting_decision`, `queued_rupert`, `duplicate_exception`, `returned_for_review`, etc. exist, but the transitions are not yet fully formalized as a state machine.
   - Operators need to know whether an item is active, terminal, blocked, or stale without reading implementation history.

2. **Queue statuses and business statuses are mixed**
   - Agent queue statuses (`completed`, `escalated`, `progress_saved`) and review/publication statuses (`awaiting_decision`, `published`, `duplicate_exception`) describe different layers.
   - A COO dashboard should present both, but not conflate them.

3. **Catalogue quality is not yet first-class**
   - A symbol can be published while still weak in taxonomy, name, aliases, provenance display, search terms, or real-world photo enrichment.
   - The snapshot showing blank category/discipline in current revision payloads is a warning that published taxonomy should be explicitly governed.

4. **Exception ownership needs tightening**
   - Libby has 41 escalated items, Tracy has 8, Vlad has 7, duplicate exceptions have 4.
   - Escalation is useful only if Daisy/Reggie/Alfi can see owner, age, reason, next action, and SLA.

5. **Reggie is still missing**
   - The system needs an audit/control agent that finds stale states, DB/runtime divergence, weak catalogue records, duplicate issues, and long-open reviews.

## Recommended implementation plan

### Phase 0 — Restart safety and commit hygiene

Goal: make the current work restartable and safe to continue.

Actions:

1. Re-run full tests in the live API/development container.
2. Review uncommitted diffs file-by-file.
3. Commit the coherent unit of work:
   - Hannah queue cards/throttle.
   - Rupert duplicate gate.
   - Libby duplicate triage.
   - Runtime persistence updates.
   - Tests.
   - This status document.
4. Confirm live API remains healthy after any rebuild/recreate.

Verification:

```bash
cd /data/symgov
git status --short
docker exec symgov-hermes-api python -m unittest discover -s tests
docker exec symgov-hermes-api sh -lc 'curl -fsS http://127.0.0.1:8010/api/v1/health'
curl -fsS -o /dev/null -w 'public_http=%{http_code}\n' https://apps.chrisbrighouse.com/api/v1/health
```

### Phase 1 — Duplicate exception workflow

Goal: close the new duplicate-safety loop without manual database spelunking.

Actions:

1. Add a Workspace/Daisy duplicate-exception view or lane.
2. Show evidence:
   - candidate preview;
   - matched published preview;
   - matched published slug/name;
   - dHash distance;
   - pixel difference;
   - Libby confidence/decision.
3. Add explicit human outcomes:
   - confirm duplicate -> keep `duplicate_resolved` / do not publish;
   - false duplicate -> return to Rupert publication queue;
   - needs metadata/title change -> route to Libby/Daisy as appropriate;
   - block/defer -> terminal blocked state with reason.
4. Implement the missing false-duplicate return-to-Rupert path.

Verification:

- The 4 current `duplicate_exception` items can be actioned from the UI/API.
- A false duplicate can publish only after Rupert preflight records a human override.
- A confirmed duplicate never appears in Standards as a new symbol.

### Phase 2 — Review split lifecycle hardening

Goal: make item state unambiguous and auditable.

Recommended canonical state groups:

- Intake/extraction: `extracted`, `needs_classification`, `needs_review`
- Review: `awaiting_decision`, `returned_for_review`, `review_decided`
- Publication: `approved_for_publication`, `publication_queued`, `published`
- Duplicate: `duplicate_pending`, `duplicate_exception`, `duplicate_resolved`
- Terminal non-publication: `deleted`, `rejected`, `blocked`, `deferred`

Actions:

1. Define allowed transitions in backend code and documentation.
2. Add transition helper(s) instead of direct status writes in scattered code.
3. Add reconciliation tests for:
   - approved -> queued Rupert -> published;
   - approved -> duplicate pending -> Libby -> duplicate resolved;
   - approved -> duplicate pending -> Libby -> duplicate exception -> Daisy decision;
   - delete/reject -> terminal state;
   - return for review -> Daisy queue/lane.
4. Reconcile the 2 current `queued_rupert` items.

Verification queries:

```sql
SELECT status, downstream_agent_slug, count(*)
FROM review_split_items
GROUP BY status, downstream_agent_slug
ORDER BY status, downstream_agent_slug;
```

### Phase 3 — Catalogue quality score

Goal: ensure Symgov grows a high-quality library, not just a large one.

Create a computed quality model for every published symbol.

Suggested dimensions:

1. Identity
   - meaningful canonical name;
   - stable slug;
   - reviewer-friendly source/package linkage.
2. Taxonomy
   - non-generic category;
   - discipline present;
   - mapped to shared taxonomy options.
3. Technical validity
   - valid preview;
   - symbol graphic lineage;
   - no unresolved Vlad defects.
4. Provenance/rights
   - source package known;
   - Tracy confidence/rights status;
   - publication-safe evidence.
5. Governance
   - human approval or explicit no-human-review evidence;
   - audit trail complete.
6. Duplicate safety
   - Rupert duplicate preflight passed or human override recorded.
7. Discoverability
   - aliases/search terms;
   - category/discipline facets;
   - description.
8. Curation
   - Hannah photo candidates or accepted real equipment photos where relevant.

Suggested bands:

- `A` — complete and well-curated.
- `B` — publishable and useful, minor enrichment gaps.
- `C` — acceptable but weak metadata/curation.
- `D` — published but needs remediation.
- `Blocked` — should not be public without correction.

Implementation options:

- Start with a SQL view or backend computed endpoint.
- Later persist snapshots for trend reporting.

Immediate remediation list:

- Published records with blank/generic category or discipline.
- Published records missing preview.
- Published records with duplicate warnings.
- Published records without source/provenance evidence.
- Published records with no aliases/search terms.
- Published records eligible for Hannah photo enrichment.

### Phase 4 — Agent feedback and self-improvement loops

Goal: turn human corrections and automated blocks into durable learning signals.

Add `agent_feedback_events` or equivalent fields containing:

- `agent_slug`
- `source_entity_type`
- `source_entity_id`
- `original_value`
- `corrected_value`
- `feedback_type`
- `reason`
- `reviewer_id` or `actor`
- `created_at`
- `applied_to_prompt_or_rules_at`

Feedback sources:

- Review screen property edits.
- Human duplicate override/confirmation.
- Deletion/rejection decisions.
- Rupert publication blocks.
- Hannah candidate rejections.
- Tracy rights/provenance corrections.
- Vlad split/crop deletions.

Agent goals:

- **Scott**: fewer orphan/stale intake rows; better source package identity.
- **Vlad**: fewer bad crops/deletions; stronger duplicate detection before review; meaningful `0003-12` style child labels.
- **Tracy**: fewer generic rights escalations; source-domain reliability memory.
- **Libby**: reduce generic taxonomy; use shared taxonomy options; explain confidence; triage duplicates safely.
- **Daisy**: own backlog/SLA health; make next action obvious; batch review intelligently.
- **Rupert**: publish only preflight-clean symbols; record duplicate checks and release manifests.
- **Hannah**: attach real equipment photos only; avoid manuals/covers/PDF thumbnails; maintain source usefulness memory.
- **Whitney**: identify catalogue gaps and high-value source acquisition targets.
- **Ed**: reduce review friction; improve Standards search/facets and evidence visibility.
- **Reggie**: detect stale queues, weak catalogue records, divergence, and compliance gaps.

### Phase 5 — Reggie control/audit agent

Goal: give Alfi/COO a control room.

Initial Reggie checks:

1. Queue rows older than threshold in active-looking states.
2. DB queue rows missing runtime JSON while dual-state remains.
3. Runtime JSON missing DB mirror.
4. Published symbols with weak taxonomy, missing preview, duplicate conflicts, or missing approval evidence.
5. Rupert queued items older than threshold.
6. Libby duplicate-resolution loops.
7. Hannah `progress_saved` or active cards with no clear terminal outcome.
8. Review cases/split items open beyond threshold.
9. Published count mismatches between split items and current revisions.

Outputs:

- `control_exceptions` table or equivalent.
- Weekly COO audit summary.
- Suggested remediation queue items.
- No destructive action without explicit operator approval.

### Phase 6 — COO dashboard

Goal: make the operating model visible.

Dashboard metrics:

- New submissions accepted.
- Symbols extracted per source package.
- Open split items by status and age.
- Agent escalations by owner and reason.
- Rupert queued/published/blocked.
- Published symbols by quality band.
- Published records with blank/generic taxonomy.
- Hannah candidates/photos attached.
- Duplicate blocks and outcomes.
- Median age by queue/status.
- Review decisions per week.

## Immediate next actions after restart

Recommended order:

1. Re-open this document.
2. Check `git status --short`.
3. Re-run tests.
4. Inspect current duplicate exceptions:

```sql
SELECT id, proposed_symbol_id, proposed_symbol_name, status, downstream_agent_slug, downstream_queue_item_id, updated_at
FROM review_split_items
WHERE status LIKE 'duplicate%'
ORDER BY updated_at DESC;
```

5. Reconcile the two `queued_rupert` rows:

```sql
SELECT id, proposed_symbol_id, proposed_symbol_name, status, downstream_agent_slug, downstream_queue_item_id, updated_at
FROM review_split_items
WHERE status='queued_rupert'
ORDER BY updated_at DESC;
```

6. Build the Daisy duplicate-exception resolution path.
7. Start the catalogue quality score as a computed backend endpoint or SQL view.

## Useful runbook commands

### Health

```bash
docker exec symgov-hermes-api sh -lc 'curl -fsS http://127.0.0.1:8010/api/v1/health'
curl -fsS -o /dev/null -w 'public_http=%{http_code}\n' https://apps.chrisbrighouse.com/api/v1/health
```

### Queue status

```bash
docker exec symgov-postgres psql -U symgov_app -d symgov -c "
SELECT ad.slug, aq.status, count(*)
FROM agent_queue_items aq
JOIN agent_definitions ad ON ad.id=aq.agent_id
GROUP BY ad.slug, aq.status
ORDER BY ad.slug, aq.status;"
```

### Review split status

```bash
docker exec symgov-postgres psql -U symgov_app -d symgov -c "
SELECT status, downstream_agent_slug, count(*)
FROM review_split_items
GROUP BY status, downstream_agent_slug
ORDER BY status, downstream_agent_slug;"
```

### Duplicate triage status

```bash
docker exec symgov-postgres psql -U symgov_app -d symgov -c "
SELECT aq.status, count(*)
FROM agent_queue_items aq
JOIN agent_definitions ad ON ad.id=aq.agent_id
WHERE ad.slug='libby'
  AND aq.payload_json->>'libby_follow_up_type'='duplicate_resolution'
GROUP BY aq.status
ORDER BY aq.status;

SELECT status, downstream_agent_slug, count(*)
FROM review_split_items
WHERE status LIKE 'duplicate%'
GROUP BY status, downstream_agent_slug
ORDER BY status, downstream_agent_slug;"
```

### API rebuild/recreate if backend code changes

```bash
cd /docker/symgov-hermes
docker compose build symgov-api
docker compose up -d --no-deps --force-recreate symgov-api
docker exec symgov-hermes-api sh -lc 'curl -fsS http://127.0.0.1:8010/api/v1/health'
```

## Restart prompt suggestion

Use this in the next session:

> Continue Symgov architectural implementation from `/data/symgov/docs/plans/2026-06-06-symgov-architecture-status-and-restart-plan.md`. Load the `symgov-agent-operations` and `symgov-architecture-improvement-roadmap` skills. First check git status, run/confirm tests, inspect duplicate exceptions and queued Rupert rows, then implement the Daisy duplicate-exception resolution path and start the catalogue quality score.
