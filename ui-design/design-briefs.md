# symgov design briefs

## Assumptions

- This pass stays lightweight and implementation-ready for plain HTML, CSS, and small JS enhancements.
- Standards View is publication-facing and only exposes currently published content.
- Published symbol pages resolve to the latest approved revision only.
- Workspace View is for standards owners, reviewers, and approvers handling many symbols in one session.
- Voting is explicitly out of scope for this phase.
- The current implementation target is a route-based prototype with primary browse/review surfaces plus focused supporting routes.

## Standards View

### Purpose

Help engineers and contractors find the correct published symbol quickly, confirm that it is current, and ask for clarification without losing the current published context.

### Core user jobs

- Browse approved symbols by category, discipline, pack, or search query.
- Keep one published record active while scanning adjacent records.
- Read the latest approved guidance, downloads, and page-level metadata.
- Ask a clarification question tied to the selected symbol or published page.

### Current layout decision

Use a browse/detail/clarification home route on desktop:

- Left: searchable approved browse list
- Center: latest-approved detail pane for the active symbol
- Right: clarification context bound to the active symbol and published page

Focused routes remain available for:

- full published symbol reading
- guided lookup conversations
- download and pack browsing

On mobile, keep browse first and stack detail then clarification below it.

### UX rules

- The symbol thumbnail is the primary recognition element in browse results.
- The active detail pane always shows published-state, revision, pack, effective date, and page context.
- Clarification context must clearly state which symbol and published page it is attached to.
- No draft, pending, or historical revision state is shown on Standards routes.
- Invalid Standards symbol routes must show a not-found state instead of silently falling back.

### Success criteria

- A user can identify the right published symbol within 10 to 20 seconds.
- A user can confirm they are looking at the latest approved revision without leaving the main Standards surface.
- A user can escalate a clarification with zero ambiguity about the symbol/page context.

## Workspace View

### Purpose

Provide a high-throughput management surface for triaging, comparing, and approving many symbols while keeping downstream impact visible.

### Core user jobs

- Triage a large change queue by urgency, owner, discipline, and approval state.
- Review the active change without losing queue context.
- Compare submitted changes against the current approved baseline.
- Approve, request changes, or reassign records with visible pack and page impact.

### Current layout decision

Make the main Workspace review route queue-first:

- Left: queue and bulk-selection tools
- Center: active compare and delta summary
- Right: approval rail with risk, impacted pages, and next actions
- Secondary strip below: linked clarifications and impacted published pages

Focused routes remain available for:

- governed record detail
- audit trail
- publish history and release flow

### UX rules

- The primary Workspace route emphasizes queue review before single-record editing.
- Queue cards expose symbol ID, change type, owner, due date, impacted page count, impacted pack count, and review status.
- Comparison tools show baseline and proposed content side-by-side.
- Approval controls are direct reviewer actions for this phase.
- Standards-originated clarifications should be visible in the same review context as the affected queue item.

### Success criteria

- A reviewer can sort and triage many records quickly from one queue surface.
- A reviewer can compare baseline and proposed content without leaving queue context.
- Approval decisions are supported by visible downstream impact and linked clarification context.

## Shared design direction

- Visual tone should stay operational, clear, and calm.
- Use broad desktop canvases and purposeful panes instead of narrow centered cards.
- Keep symbol rendering consistent across browse, detail, compare, and queue states.
- Reserve accent color for state and action priority, not decoration.
- Treat detail and compare SVG as accessible product content, not purely decorative artwork.

## Follow-ups

- Confirm whether Standards guided lookup remains AI-assisted only or needs a human-routed mode later.
- Confirm what publication guardrails should exist for high-impact queue items.
- Confirm whether clarifications should auto-create change requests or remain manually linked.
