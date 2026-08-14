# Symgov development guide

## Repository and scope

- Work from the repository root.
- The frontend source is in `frontend/`; the workspace-root static files are published output, not the primary source.
- The product has four main frontend surfaces: Submissions, Workspace, Reviews, and Standards View.
- `ui-design/` contains design reference artifacts, not a complete route inventory.

## UI work

- Inspect the existing route, components, tokens, and styles before changing composition.
- Preserve the product split: Workspace is operator/processing visibility, Reviews is SME review ergonomics, Standards is published-only consumption.
- Prefer the existing visual language and responsive behavior unless the task explicitly requests a redesign.
- Keep human-readable symbol IDs and operator-readable timestamps prominent; do not replace them with UUIDs in compact UI.
- Use accessible controls, visible focus states, semantic labels, and keyboard-friendly interactions.
- Do not invent production metrics, workflow states, or backend contracts. If a value is illustrative, label it as such.

## Validation

From the repository root, use the narrowest relevant checks first:

- `npm run build` — frontend build (when dependencies are installed at the expected location).
- `npm run test:frontend` — frontend tests.
- Backend test commands are documented in `backend/README.md` and the relevant backend package.

Do not run `npm run build:publish`, `npm run publish:static`, deployment commands, service restarts, migrations, or other live mutations unless Chris explicitly approves that operation.

## Git and delivery

- Do not push or deploy without explicit approval.
- Keep unrelated pre-existing changes and untracked files untouched.
- Before reporting completion, inspect `git status`, review the diff, and report tests actually run.
- Use concise conventional commit messages only if a commit is explicitly requested.
