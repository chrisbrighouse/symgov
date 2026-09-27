# Symgov Ed application-guru assistant — implementation plan

Status: approved for bounded implementation; commit, push, migration, deployment, restart and production activation require separate approval
Related specification: `docs/plans/2026-09-27-ed-application-guru-spec.md`

## Delivery principles

- Work in the git-managed Symgov repository, not the deprecated OpenClaw workspace copy.
- Keep Ed read-only until a separately approved action design exists.
- Reuse deterministic Symgov authorization and read services; never give an LLM direct SQL or unrestricted model access.
- Keep the existing Catalog Integration Ed contract intact unless a deliberate compatibility review approves a change.
- Use lean TDD, bounded work packages, independent verification and explicit approval before migrations, commits or deployment.
- Every answer claim must be traceable to an approved knowledge source or an authorization-enforced live-data result.

## Stage 0 — product decisions and baseline freeze

Goal: remove ambiguity before implementation.

Tasks:

1. Treat the seven resolved product decisions in specification section 10 as controlling; reopen one only if live repository evidence proves a genuine contradiction.
2. Freeze a baseline commit and record current repository status, especially any pre-existing dirty files.
3. Inventory existing Ed/Catalog routes, generic LLM routes, role dependencies, usage ledger fields and frontend navigation.
4. Confirm the canonical active application runtime and configured provider path without printing secrets.
5. Define data-retention, privacy and support policy for prompts, responses, citations and feedback.

Exit evidence:

- approved product decision record;
- baseline SHA and clean/dirty worktree record;
- route/service/role inventory;
- explicit provider and retention decisions.

## Stage 1 — knowledge model and source inventory

Goal: define Ed's internal knowledge representation without exposing a user guide.

Tasks:

1. Create a versioned internal knowledge manifest schema.
2. Define topic taxonomy: application, classifications, organizations, projects, symbol sets, symbols, security, user setup, known limitations and support.
3. Define source adapters for approved Markdown, Python, TypeScript/JavaScript, API schemas and migration metadata.
4. Define atomic claim format with source path, source version, line/range or symbol, approval state, extracted timestamp and supersession links.
5. Identify facts that must never enter the user-retrievable corpus, including secrets, credentials, private config, raw security values and unapproved internal notes.
6. Add a deterministic corpus validator for missing sources, broken references, duplicate IDs, unsupported visibility and contradictory claims.

Exit evidence:

- schema and manifest examples;
- source allowlist and exclusion list;
- validator tests;
- generated corpus report showing all requested Ed topics are covered or explicitly marked incomplete.

## Stage 2 — read-only context service

Goal: provide narrow, typed, authorization-enforced facts for current-state questions.

Tasks:

1. Trace existing organization, membership, role, project, symbol set, symbol and classification read services.
2. Extract or add service-layer read functions rather than embedding query logic in the LLM route.
3. Define typed tool contracts and maximum result sizes.
4. Enforce current session, active organization and active project context server-side.
5. Add tests for same-user access, cross-organization denial, unavailable project/set handling, closed records and ordinary-user versus admin visibility.
6. Return stable citation metadata for each live result, including as-of timestamp and source kind.

Exit evidence:

- tool schemas and authorization matrix;
- unit/API tests for each tool;
- negative tests proving prompt-supplied IDs cannot widen access;
- no direct SQL, model object dump or mutation path reachable from Ed.

## Stage 3 — Ed orchestration API

Goal: add a dedicated Ed API without weakening existing generic or Catalog endpoints.

Proposed surface (subject to route review):

- `POST /api/v1/ed/chat` — authenticated, read-only question/answer request;
- optional `GET /api/v1/ed/capabilities` — static UI capability metadata;
- optional `POST /api/v1/ed/feedback` — bounded feedback with privacy policy.

Tasks:

1. Add request/response schemas with strict bounds and structured citations.
2. Add authentication and context-resolution dependencies.
3. Add topic routing and retrieval orchestration.
4. Add allowlisted read-tool dispatch with maximum tool calls per request.
5. Call the existing server-side LLM router using a dedicated `ed_guru` feature key.
6. Add structured output validation and safe fallback responses for provider failure, missing evidence, ambiguity and out-of-scope mutation requests.
7. Add credential redaction, no-store response headers, timeout/cancellation and rate limits.
8. Add sanitized usage-ledger and audit telemetry.

Exit evidence:

- API contract tests;
- provider mocked tests for citations, refusal, timeout and malformed output;
- authorization tests through the real dependency path;
- usage attribution visible in the existing admin ledger without secrets.

## Stage 4 — internal corpus build and retrieval

Goal: make Ed knowledgeable about all requested Symgov areas.

Tasks:

1. Build the initial corpus from approved repository sources and product specs.
2. Include the classification scheme/ICS provenance and clearly distinguish taxonomy codes from copyrighted standards documents.
3. Add chunking/indexing strategy, preferably with lexical retrieval first and embeddings as a bounded enhancement.
4. Include source version in every retrieval result.
5. Add rebuild command and runbook; do not require production access for a local corpus build.
6. Decide whether indexing occurs at build/release time or as an explicit admin job.
7. Ensure generated knowledge changes are draft until reviewed and approved.

Exit evidence:

- corpus manifest and generated index;
- query fixtures for each topic;
- source citations that resolve to the correct repository/runtime source;
- stale-source and broken-source checks;
- documented rebuild and rollback procedure.

## Stage 5 — frontend chat surface

Goal: give users a focused chat prompt and response screen, not a user-visible manual.

Tasks:

1. Add the authenticated Ed route and navigation entry according to the approved role/entitlement decision.
2. Implement prompt input, submit/cancel, transcript, citations, warnings, context display and read-only status.
3. Implement empty, loading, timeout, model unavailable, forbidden and cannot-answer states.
4. Ensure keyboard accessibility, labels, focus handling, responsive layout and no secret-bearing output.
5. Keep conversation state browser-local unless server persistence is explicitly approved.
6. Add frontend component tests and an isolated production build.

Exit evidence:

- mounted UI tests for successful answer, citation display, refusal and error states;
- route/auth tests;
- accessibility checks;
- isolated frontend build with no unrelated changes.

## Stage 6 — evaluation and security review

Goal: prove Ed is useful without becoming a data or action bypass.

Tasks:

1. Create a versioned evaluation set for normal product questions and expected citations.
2. Add adversarial cases: prompt injection, cross-organization probing, credential requests, hidden prompt requests, role escalation and mutation requests.
3. Test live-data freshness and closed/missing context.
4. Measure latency, token usage, cost, rate-limit behaviour and provider failure modes.
5. Conduct an independent security/code-quality review.
6. Fix all actionable defects and rerun fresh verification.

Exit evidence:

- evaluation report with pass/fail and known gaps;
- security review result;
- explicit authorization and retention sign-off;
- no unresolved high-risk findings.

## Stage 7 — controlled activation

Goal: expose Ed safely to a limited audience.

Tasks:

1. Enable behind a feature flag or restricted entitlement.
2. Configure the approved `ed_guru` model server-side; never commit secrets.
3. Run smoke tests in a disposable or staging environment.
4. Verify real readback of citations, context isolation, usage ledger and audit events.
5. Monitor refusal, error, cost and feedback metrics.
6. Expand access only after a review of real usage and false-answer reports.

Exit evidence:

- activation checklist;
- staging smoke evidence;
- dashboard/alert definitions;
- rollback procedure tested;
- explicit approval before production enablement.

## Proposed data and API boundaries

Knowledge records are internal and should be separate from user transcript records. A possible future schema split is:

- `ed_knowledge_documents`: document/version/source metadata;
- `ed_knowledge_chunks`: approved atomic retrieval units;
- `ed_knowledge_builds`: build status, manifest hash and index version;
- `ed_feedback`: bounded user feedback and answer/citation references;
- existing LLM usage ledger: feature `ed_guru` and request metadata.

Do not add these tables until Stage 0 decisions and Stage 1 ownership are approved. A filesystem/index-only first implementation may be preferable if no durable user-facing knowledge administration is needed.

## Initial authorization matrix

| Capability | Ordinary authenticated user | Organization admin | Platform admin |
|---|---:|---:|---:|
| Ask general product questions | Yes | Yes | Yes |
| Ask about own active context | Yes | Yes | Yes |
| Ask about permitted organization/project/set records | Yes, scoped | Yes, scoped | Yes, scoped |
| Ask about another organization's records | No | No unless explicitly a member/authorized | Only through approved admin read policy |
| Ask generic security/user-setup explanation | Yes, bounded | Yes | Yes |
| Ask live user-management details | No | Only permitted admin scope | Only permitted platform scope |
| Change any Symgov state through Ed | No in phase 1 | No in phase 1 | No in phase 1 |
| Approve/publish/withdraw governance output | No in phase 1 | No in phase 1 | No in phase 1 |

This matrix is a starting point and must be aligned with the application's authoritative policy functions, not implemented as a second permission system.

## Future action phase — deliberately separate

Do not mix this into the read-only delivery. If actions are later approved, deliver them as separate work packages with:

- typed command schemas;
- deterministic authorization and validation;
- preview/dry-run;
- explicit confirmation;
- idempotency/concurrency controls;
- audit trail and rollback where possible;
- human approval for governance, publication, security and user administration;
- independent security review.

## First implementation package

Begin with Stage 1 as a bounded, repository-owned vertical slice: the versioned knowledge-manifest schema, topic and visibility enums, deterministic validator, representative manifest fixture and focused tests. Do not add database tables, model-provider calls, HTTP routes, frontend UI, generated embeddings or production activation in this package.

The writer must preserve the existing Catalog-specific Ed behavior, the generic LLM route and the unrelated `.claude/settings.local.json` modification. Work test-first, stop at a green focused checkpoint and hand the exact changed paths and test evidence back for independent controller verification. Do not commit, push, migrate, deploy, restart services or modify live data.
