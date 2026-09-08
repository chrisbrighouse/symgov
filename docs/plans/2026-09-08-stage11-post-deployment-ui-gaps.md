# Stage 11 post-deployment UI usability gaps

Recorded 2026-09-08, during the first live walkthrough of the Stage 11
release (`stage11-398d3be`, schema `20260908_0046`). Nothing here is a
backend or data defect: every route and rule below works correctly when
driven directly. These are all cases where a working capability has no
usable path through the UI.

Found by walking the WP11.8 step 6 smoke-test list as a real operator
rather than by reading code, which is why none of them were caught by the
test suites — every one of these paths is covered at the API level.

## 1. "Initial admin user ID" needs a user picker (raised by Chris)

`PlatformAdminPage.js` `CreateOrganizationForm` asks for the new
organization's first admin as a **raw UUID** in a free-text field, with
no picker, lookup, or validation feedback beyond a 400.

Observed: entering `ada@symgov.local` — the obvious thing to type into a
field asking for a user — returns `400 Invalid user ID` from
`POST /api/v1/platform/organizations`, because `initialAdminUserId` is
typed `uuid.UUID` and fails validation before any user lookup happens.
The operator has no way to obtain the UUID from anywhere in the UI, so
creating an organization currently requires a database query or an API
call first. Chris's assessment: "definitely not usable for a user."

**Wanted:** select the initial admin from a list of users who have the
admin role set, rather than typing an identifier.

**Decided by Chris, 2026-09-08:** list **all users, in alphabetical
order**, to select from — not just site-role admins. This matches what
`POST /platform/organizations` actually permits (any valid user can be
made an organization's first Organization Admin; they need not be a site
admin), and avoids a picker that silently narrows what the platform admin
can do.

One detail left to implementation: whether deactivated accounts appear in
the list. Assigning an inactive user as an organization's first admin
would create an organization nobody can administer, so exclude or clearly
mark them.

Accepting an email address as well as a UUID would fix the immediate
400, but it is a smaller fix than what was asked for and still requires
knowing the address; it is not the deliverable.

## 2. Symbol Set → Project availability has no UI at all

`PUT /api/v1/org/me/symbol-sets/{setId}/projects`
(`routes/symbol_sets.py:90`, `replace_projects`) has **no client method
in `frontend/src/api.js`** and no caller in any panel.

This blocks a step the operator is told to perform: "Set default" fails
with `409 Organization default Symbol Set must be available to an active
Project` (`symbol_set_service.py:538-543`), because the org default
requires an active `ProjectSymbolSet` link to an active Project — and
nothing in the UI can create that link. The Set default button is
therefore unreachable for **every** set, not just a newly created one.

Wanted: a Project picker per Symbol Set row (multi-select over active
Projects, plus a per-project default toggle), an `api.js` method, and
tests. Note `PUT` replaces the whole list rather than appending.

Sequence for context — creating a usable Symbol Set is
`create → activate → make available to a Project → set default`. The UI
shipped steps 1 and 4. Step 2 was fixed on 2026-09-08 (`398d3be`, the
Activate button); step 3 is this item.

## 3. The three admin rail links are visually identical

`App.jsx:781-791` renders Admin (`/workspace`), Organization
(`/organization/admin`) and Platform (`/platform/admin`) all with
`icon="admin"`. Only the label distinguishes them, so the Platform link
— the only route to creating an organization — reads as a repeat of a
link the operator already knows and was missed entirely on first use.
Agreed with Chris to fix later; it is discoverability, not access.

## 4. "No active Symbol Sets" renders above a populated list

`OrganizationSymbolSetsPanel.js:71,183`: the empty-state message keys off
active sets only, while the list below renders sets of every status. With
only draft sets present, the panel says there are none directly above a
list of them. Cosmetic, one line.

## Non-UI follow-up found the same day

The WP11.2 secret-scan gate (`scripts/secret_scan_added_lines.py`) only
sees tracked changes, so **untracked files are invisible to it**. A new
file scans clean before `git add` and can then fail immediately after
being committed — which is exactly what happened on 2026-09-07, where a
pre-push run passed and the same content flagged once staged. A gate
that skips brand-new files is weakest precisely where new secrets are
most likely to appear. Its findings on the two test files were false
positives (per-run disposable container passwords), and
`test_stage4_function_search_path_dump_restore_postgresql.py` now passes
credentials via `PGPASSWORD` rather than embedding them in a URL;
`test_two_role_privilege_model_postgresql.py` still uses the older
pattern.
