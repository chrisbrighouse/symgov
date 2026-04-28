# symgov design briefs

## Assumptions

- This pass stays lightweight and implementation-ready for plain HTML, CSS, and small JS enhancements.
- Standards View is publication-facing and only exposes currently published content.
- Published symbol pages resolve to the latest approved revision only.
- Workspace View is admin/operator-facing processing visibility for agent activity.
- Reviews View is for SMEs handling Daisy-coordinated human review cases.
- Voting is explicitly out of scope for this phase.
- The current implementation target is a route-based prototype with primary browse/review surfaces plus focused supporting routes.
- The shared top banner exposes the three main user areas as text buttons: Submissions, Reviews, and Standards. Workspace remains available through the cog icon because it is the internal operator surface.

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

Provide an admin-only processing surface for seeing what Scott, Vlad, Tracy, Libby, Daisy, Human Review, Rupert, and Ed are doing.

### Core user jobs

- Monitor submitted batches and source files through the processing pipeline.
- See which agent is queued, running, completed, waiting, or escalated.
- Inspect processing summaries, artifact counts, and exception states.
- Jump from a processing flow to a Daisy-coordinated review case.

### Current layout decision

Make the main Workspace route processing-first:

- Left: processing flows and batch/file queue
- Center: eight compact monitor lanes for Scott, Vlad, Tracy, Libby, Daisy, Human Review, Rupert, and Ed
- Right: operator context, status counts, and review handoff

## Reviews View

### Purpose

Provide a user-friendly SME review surface for Daisy-coordinated review cases.

### Core user jobs

- See all Daisy-coordinated cases, not only cases ready for final decision.
- Review extracted child symbols with source lineage and Daisy support.
- Draft review actions: approve, reject, request changes, request more evidence, rename/classify, mark duplicate, delete proposed child, or defer.
- Keep review notes tied to the case and source file.

### Current layout decision

Make Reviews queue-first for SMEs:

- Left: Daisy-visible review cases
- Center: guided child-symbol review
- Right: source context, Libby metadata, Daisy coordination, and case notes

Focused routes remain available for:

- governed record detail
- audit trail
- publish history and release flow

### UX rules

- Workspace uses denser admin language; Reviews uses reviewer-friendly language and support.
- Queue cards expose symbol ID, change type, owner, due date, impacted page count, impacted pack count, and review status.
- Review tools show proposed child symbols, source context, and Daisy recommendations.
- Review controls are direct SME draft actions for this phase.
- Standards-originated clarifications should be visible in the same review context as the affected queue item.

### Success criteria

- A reviewer can sort and triage many records quickly from one queue surface.
- A reviewer can compare baseline and proposed content without leaving queue context.
- Approval decisions are supported by visible downstream impact and linked clarification context.

## Shared design direction

- Visual tone should stay operational, clear, and calm.
- The shared banner should stay light, full-width, and direct, with the engineering-symbol mark as the logo and version/date metadata in the right rail.
- Use broad desktop canvases and purposeful panes instead of narrow centered cards.
- Keep symbol rendering consistent across browse, detail, compare, and queue states.
- Reserve accent color for state and action priority, not decoration.
- Treat detail and compare SVG as accessible product content, not purely decorative artwork.

## Follow-ups

- Confirm whether Standards guided lookup remains AI-assisted only or needs a human-routed mode later.
- Confirm what publication guardrails should exist for high-impact queue items.
- Confirm whether clarifications should auto-create change requests or remain manually linked.
