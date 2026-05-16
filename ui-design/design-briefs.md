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

Use a browse/grid/detail home route on desktop:

- Left: published-record facets, omitting redundant status facets because Standards is published-only
- Center: approved-symbol grid with search, sortable columns, and column filters
- Right: latest-approved detail panel when a row is selected; it shares the grid height cap and scrolls internally so the close control stays reachable

Focused routes remain available for:

- full published symbol reading
- guided lookup conversations
- download and pack browsing
- clarification context bound to selected symbol or published page context

On mobile, keep facets and browse first, then stack the selected detail panel below it.

### UX rules

- The symbol thumbnail is the primary recognition element in browse results.
- Browse tables and lists label the compact symbol identifier as `ID`, while `Name` displays the published symbol name from the approved payload.
- The active detail panel always shows published-state, revision, pack, effective date, and page context.
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
- Jump from a successfully published Rupert item to the published Standards record.

### Current layout decision

Make the main Workspace route processing-first:

- Top: title, queue search, and a full-width single-line live status row so refresh and processing text does not compete with the search control
- Main: eight equal-height compact monitor lanes for Scott, Vlad, Tracy, Libby, Daisy, Human Review, Rupert, and Ed, each with its own scrollable card stack
- Cards: activity and source context first, optional Vlad process/tool summary where available, optional Standards target for published Rupert cards, then queue status on its own line for scanability

## Reviews View

### Purpose

Provide a user-friendly SME review surface for Daisy-coordinated review cases.

### Core user jobs

- See all Daisy-coordinated cases and open split-item reviews, not only cases ready for final decision.
- Review extracted child symbols as individual human-review items with source lineage and Daisy support.
- Edit reviewer-controlled symbol properties beside the source graphic before publication: `Name`, `Description`, `Category`, and `Discipline`, while confirming the read-only source `Format` as a compact file-format badge.
- Reuse previously entered Category and Discipline values from visible saved-value selectors while retaining free-text entry for new terms.
- For raster split reviews, process only the child symbols that have a decision and leave pending children in the source-sheet workbench.
- Draft review actions: approve, reject, request changes, request more evidence, rename/classify, mark duplicate, delete proposed child, or defer.
- Keep review notes tied to the case and source file.
- Understand routing after submit: approval goes to Rupert; every other outcome goes to Libby, with graphic-change work handled through Vlad and returned for Daisy re-review.

### Current layout decision

Make Reviews queue-first for SMEs:

- Left: Daisy-visible review queue with previous/next movement through filtered items
- Center: visual evidence, source context, classification facts, and guided child-symbol review
- Right: decision rail with case actions, reviewer identity, comments, decision note, latest decision, and Daisy coordination for non-split reviews; raster split reviews instead show child-symbol processing counts and a single process button for selected children.

Focused routes remain available for:

- governed record detail
- audit trail
- publish history and release flow

### UX rules

- Workspace uses denser admin language; Reviews uses reviewer-friendly language and support.
- Queue cards expose symbol ID, title, current stage, child-symbol count, source file, and priority.
- Review tools show source imagery where available, proposed child symbols, classification/source context, and Daisy recommendations.
- Symbol identifiers are labelled `ID`; symbol properties are kept near the graphic so reviewers can correct agent-seeded `Name`, `Description`, `Category`, and `Discipline` values without leaving the record. Name and description are stacked on the left, Category and Discipline are grouped on the right, and `Format` stays visible as a read-only badge under Description.
- Review controls are direct SME actions for this phase and remain visible as buttons, not buried in a dropdown.
- Raster split reviews use `Process Selected Symbols` instead of a whole-case submit button or case action panel; the control is disabled until at least one child has a decision, and processed children should disappear from the open child list after refresh.
- The decision rail should make the downstream handoff visible after submission: Rupert for approval, Libby for non-approval, Vlad only as a Libby-routed graphic-change step.
- Standards-originated clarifications should be visible in the same review context as the affected queue item.

### Success criteria

- A reviewer can sort and triage many records quickly from one queue surface.
- A reviewer can inspect source imagery and child-symbol previews without leaving queue context.
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
