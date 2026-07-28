# F0.4 — Review Requests Without Unpublication

> **For Hermes:** Use the durable serialized Symgov Kanban/Cody lane to implement this specification. Do not combine implementation, Stage 1 specification review, Stage 2 security/code-quality review, or final verification in one mutable review snapshot.

**Status:** SPECIFIED, NOT IMPLEMENTED

**Parent backlog:** `docs/plans/2026-07-26-symgov-trial-readiness-implementation-backlog.md`

**Baseline inspected:** clean `main` and `origin/main` at `45fc6e00b1372fce1e092ebe282f264ccd401cb3` on 2026-07-28. This is also the active F0.3 production release identity; see `docs/plans/2026-07-28-f0-3-deployment-addendum.md`.

**Goal:** Let an authenticated person or authorized Catalog integration submit feedback or request review of a published symbol without changing that symbol's public availability, while recording the real requester and opening durable Ed-managed review work exactly once at the supported transaction boundary.

**Architecture:** Keep publication state, feedback/review workflow state, and future withdrawal/replacement state independent. The published read model continues to be selected from `PublishedPage`/`PackEntry`/`PublicationPack` plus a `SymbolRevision.lifecycle_state == "published"` predicate. F0.4 removes review-request authority to mutate that predicate, derives requester attribution from the authenticated session or API-key dependency, and treats Ed only as workflow owner/executor. Existing clarification, review-case, action, queue and audit models are sufficient; no withdrawal endpoint or schema redesign is introduced.

**Tech stack:** FastAPI, Pydantic, SQLAlchemy/PostgreSQL, React/Vite, pytest, Node tests, Ed runtime queue.

---

## 1. Purpose and governance outcome

The current shared feedback service turns `send_for_review` into two different operations in one branch:

1. it creates durable clarification/review work; and
2. it locks the currently published `SymbolRevision` and changes `lifecycle_state` from `published` to `review`.

`PUBLISHED_SYMBOLS_SQL` requires `sr.lifecycle_state = 'published'`. The second operation therefore hides the symbol from the public catalogue even though no authenticated human made a withdrawal decision. The browser path also substitutes Ed's service user for the real authenticated requester.

F0.4 establishes these invariants:

1. Feedback and review requests create durable clarification/review work only.
2. The exact revision, page and pack that were publicly available before the request remain publicly available after it.
3. No feedback/review-request route may set a revision to `review`, `deprecated`, or any other non-published state; change a page's current revision; remove a pack entry; or change pack audience/status.
4. Ed owns and executes the workflow but is not the requester of a human request.
5. An API key is recorded as the requester when the Catalog integration route is used; it is not converted into a human or Ed actor.
6. F0.4 creates no withdrawal capability. The separately specified F2.3 goal must introduce an authenticated, authorized human withdrawal/replacement decision before public availability can be altered through that mechanism.

---

## 2. Current-state evidence and classification

### 2.1 Capability matrix

| Behaviour | Verdict | Evidence and correction |
|---|---|---|
| Published read model is explicit and revision-gated | implemented | `backend/symgov_backend/published_catalog.py:6-40` joins pages, packs, entries and revisions and requires published pack/public audience/published revision. |
| Feedback is persisted durably | implemented | `backend/symgov_backend/services/published_feedback.py:165-181`; `ClarificationRecord` has mutually exclusive human/external/API-key requester columns in `backend/symgov_backend/models/schema.py:459-483`. |
| Review request creates/reuses an open review case | implemented, partial under concurrency | `published_feedback.py:198-218` reuses an open `published_symbol` case, but no uniqueness or serialization prevents concurrent duplicate open cases. |
| Review request creates an Ed action and DB/runtime queue item | implemented, retry-unsafe | `published_feedback.py:220-270` creates a new action, DB queue row and runtime JSON on every call. `tests/test_catalog_feedback.py:443-479` proves two calls create two actions, queues and files, but the API has no request-id contract to distinguish two intentional requests from one transport retry. |
| Review request preserves the published revision | incorrect | `published_feedback.py:194-196` locks the revision and sets `lifecycle_state = "review"`. Existing tests deliberately preserve this defect at `tests/test_published_feedback_service.py:225-252` and `tests/test_published_symbol_review_workflow.py:29-136`. |
| Browser requester is session-authoritative | incorrect | The router is authenticated at mount time by `backend/symgov_backend/app.py:75,90`, but `run_published_symbol_command()` does not inject the dependency return value. At `routes/published.py:422-443` it creates/uses Ed and persists Ed as `submitted_by`, audit actor and workflow owner. |
| Catalog requester is API-key-authoritative | implemented | `routes/catalog.py:704-754` derives `IntegrationAuthContext` with `catalog.feedback.write`; `catalog_api_auth.py:75-140` authenticates and scope-checks the key; the service records `catalog_api_key_id`. |
| Invalid Catalog request is side-effect free | incorrect | `catalog_api_auth.py:96-129` updates `last_used_at` and commits inside the authentication dependency before body validation or symbol lookup. Existing `tests/test_catalog_feedback.py:432-440` expects an `auth` commit even for a missing symbol. F0.4 must move this bookkeeping into the accepted authoritative transaction for the feedback route so invalid requests commit nothing. |
| Human/API-key requests distinguish requester from Ed executor | partial/incorrect | `ClarificationRecord` distinguishes API keys, but browser records and audits say Ed. Action `created_by_type="system"`/`created_by_id=Ed` and payloads only expose `managed_by: ed` (`published_feedback.py:220-241,272-288`). |
| Request payload can select actor identity | absent as an advertised field, but validation is permissive on browser route | Browser normalization reads expected values but does not reject unknown keys (`routes/published.py:168-183,392-403`). Catalog strictly rejects unknown keys (`routes/catalog.py:711-713`). F0.4 must make both direct and wrapped browser bodies fail closed for actor-like or other unknown fields. |
| Explicit authenticated withdrawal service | absent | No backend withdrawal/unpublish service or route exists. Searches find publication writes in `runtime.py:2139-2234` and the erroneous feedback transition, but no governed withdrawal path. This remains F2.3, not F0.4. |
| Publication, review workflow and withdrawal states are separate | partial | Publication has pack/page/entry/revision state; review has clarification/case/action/queue state; explicit withdrawal/replacement has no domain record or service. The current feedback branch incorrectly couples the first two. |
| Transaction rollback covers DB state | implemented at route level | Browser and Catalog adapters commit once and roll back on service/commit exceptions (`routes/published.py:405-460`, `routes/catalog.py:736-758`); tests cover these paths. |
| DB/runtime queue creation is atomic | absent | Runtime JSON is written before the surrounding DB commit at `published_feedback.py:126-130`; an ordinary rollback or process crash can leave a runtime orphan. Reconciliation reports but does not automatically repair missing/orphan runtime rows (`agent_queue_reconciliation.py:220-357`). |
| Symbol-level feedback has an unambiguous publication target | incorrect | `PUBLISHED_SYMBOLS_SQL` can return several page/pack rows for one symbol/revision, and the schema permits multiple placements. Browser routing silently keeps the first row per symbol (`routes/published.py:407-430`), while `ClarificationRecord.published_page_id` is a single **non-nullable** page foreign key. F0.4 must define a deterministic canonical page as the compatibility anchor, preserve the complete placement snapshot, and reject multiple simultaneously published revisions rather than choosing a revision by row order. |
| Request-level idempotency exists | absent | Neither browser command payload nor Catalog feedback headers carry a stable request ID. Random clarification/action/queue UUIDs mean a transport retry creates duplicate intake and work. F0.4 must make a caller-stable key mandatory and derive deterministic per-principal/per-symbol identities without adding an actor field. |
| Frontend describes review as non-withdrawal | absent | `frontend/src/App.jsx:2037-2072` says only “Send for Review”; `frontend/src/api.js:1244-1253` sends a wrapped command and has no explicit actor field or lifecycle notice. |
| Focused baseline is green | implemented | On the inspected `45fc6e0` snapshot, the five focused files listed in section 11 produced `93 passed` in 2.66 seconds; their assertions encode the current unpublication defect and must be inverted, not preserved. |

### 2.2 Publication lifecycle evidence

- `SymbolRevision.lifecycle_state` permits `draft`, `review`, `approved`, `published`, and `deprecated`: `backend/symgov_backend/models/schema.py:271-289`.
- `PublishedPage.current_symbol_revision_id` and `PackEntry.symbol_revision_id` bind the public page/pack to a revision: `models/schema.py:372-395,433-445`.
- Rupert publication persistence creates/updates packs/pages/entries and sets the revision to `published`: `backend/symgov_backend/runtime.py:2139-2234`.
- Public reads require all four publication predicates: pack `published`, audience `public`, matching page/entry revision, and revision `published`: `published_catalog.py:30-40`.

Changing only the revision to `review` is therefore an effective unpublication even though the page and entry remain.

### 2.3 Feedback/review workflow evidence

- Durable feedback: `ClarificationRecord` (`models/schema.py:459-483`).
- Durable coordination: `ReviewCase` (`models/schema.py:834-844`).
- Durable work item: `ReviewCaseAction` (`models/schema.py:866-886`).
- Durable queue mirror: `AgentQueueItem` (`models/schema.py:509-524`).
- Runtime consumer: Ed is registered in `backend/symgov_backend/agent_queue_worker.py:62-67`; runtime JSON is consumed from the Ed queue directory by `queued_item_paths()` at `agent_queue_worker.py:145-171`.
- Existing external-workspace tests prove Ed can coordinate the handoff: `tests/test_published_symbol_review_workflow.py:155-204`.

### 2.4 Authorization and attribution evidence

- Browser published routes are mounted with `require_user`: `backend/symgov_backend/app.py:75,90`.
- `require_user` accepts any authenticated active session identity, independent of reviewer/admin role: `backend/symgov_backend/dependencies.py:139-150`.
- Catalog feedback requires an active API key with `catalog.feedback.write`: `routes/catalog.py:704-708`; `catalog_api_auth.py:75-140`.
- Catalog request bodies already reject unknown fields and are limited to `kind`, `message`, and `context`: `routes/catalog.py:711-731`.
- Browser command bodies accept direct or `{payload: ...}` shapes, but no route parameter receives `AuthenticatedUser`: `routes/published.py:390-443`.

---

## 3. Explicit lifecycle semantics

### 3.1 Publication lifecycle

Publication availability is the conjunction represented by `PUBLISHED_SYMBOLS_SQL`:

- `PublicationPack.status == "published"`;
- `PublicationPack.audience == "public"`;
- `PackEntry` points to the page and same revision;
- `PublishedPage.current_symbol_revision_id` points to the revision;
- `SymbolRevision.lifecycle_state == "published"`.

A feedback or review request must not modify any of these fields or delete any participating row. The requested revision remains `published` even while work is open against it.

Because the API is symbol-level, normalize all public rows for each resolved governed symbol before any write:

- if all rows identify one distinct published revision, the request is valid even when that revision appears on several pages or packs;
- store a stable, sorted `publication_snapshot` in clarification context, audit and Ed payload containing every page ID, pack code, pack-entry ordering identity and the one revision ID/label;
- choose one **canonical page compatibility anchor** from the normalized rows by ascending `(pack_code.casefold(), sort_order, page_id)`, with null `sort_order` normalized to integer sentinel `2147483647`; set the existing non-nullable `ClarificationRecord.published_page_id` to that page. The anchor satisfies the existing schema only: review scope remains the governed symbol/revision and the complete `publication_snapshot`, not merely that page;
- if rows contain more than one distinct published revision for the symbol, return 409 `ambiguous_published_revision` before any write. F0.4 must not invent a canonical revision or review only one placement;
- after accepted feedback, re-reading the public catalogue must return the same normalized publication snapshot.

### 3.2 Feedback/review workflow

A request creates a `ClarificationRecord(status="open")` for the exact symbol, canonical page compatibility anchor and requester. For `send_for_review`, it also opens or reuses an Ed-owned `ReviewCase`, records a queued `ReviewCaseAction`, and creates or reuses the intended Ed `AgentQueueItem`/runtime handoff.

Workflow state is represented only by:

- clarification `kind`/`status`;
- review-case `current_stage`/`closed_at`;
- action `action_code`/`action_status`;
- queue `status`.

No workflow state is inferred from or encoded by changing the published revision.

### 3.3 Explicit withdrawal or replacement

F0.4 provides no withdrawal, retirement, deprecation, supersession or replacement endpoint. No caller—human, reviewer, administrator, API key or agent—can use the feedback routes to perform one.

A future F2.3 withdrawal/replacement service must require a separately authenticated and authorized human decision, reason, governed target revision/page/pack scope, audit event, and reversible replacement/republish rules. API keys and agents must never be withdrawal actors. Until F2.3 exists, a production withdrawal requires a separately authorized operational procedure and must not be simulated through feedback state.

---

## 4. Authorization matrix

| Caller | Comment | Request review | Withdraw/retire/replace through these routes |
|---|---:|---:|---:|
| Authenticated active Free/Plus user | allowed | allowed | forbidden / no route |
| Authenticated reviewer | allowed; no special attribution | allowed; requester is the reviewer, Ed remains executor | forbidden / no route |
| Authenticated administrator | allowed; no special attribution | allowed; requester is the administrator, Ed remains executor | forbidden / no route; admin status alone must not turn review into withdrawal |
| Active Catalog API key with `catalog.feedback.write` | allowed | allowed | forbidden |
| Catalog API key without scope | 403, no side effects | 403, no side effects | forbidden |
| Missing/invalid session or API key | 401, no side effects | 401, no side effects | forbidden |
| Anonymous caller | 401, no side effects | 401, no side effects | forbidden |
| Ed or another agent acting without a human/API-key request | not via these public routes | not via these public routes | forbidden |

F0.4 does not add reviewer-discipline authorization. Review-request intake is not a governance decision and remains available to any authenticated user and the scoped Catalog integration. Later review decisions remain governed by F0.2/F0.3 and later F1.3 scope controls.

---

## 5. Authoritative requester and executor attribution

### 5.1 Browser/session path

`run_published_symbol_command()` must inject `current_user: AuthenticatedUser = Depends(require_user)` even though the router also retains its mount-level guard. Derive the requester UUID from `current_user.id` once before any write.

For every selected symbol:

- `ClarificationRecord.submitted_by = session user UUID`;
- `external_submitter_id` and `catalog_api_key_id` are null;
- feedback audit `actor_id = session user UUID`;
- audit payload contains a safe requester snapshot: type `user`, UUID and display name; roles may be included as a sorted explanatory snapshot but do not grant withdrawal authority;
- Ed remains `ReviewCase.owner_id`, `ReviewCaseAction.assigned_to`, and `target_agent_slug`;
- requester fields must never be populated from request JSON.

### 5.2 Catalog/API-key path

The existing `IntegrationAuthContext` remains authoritative:

- `ClarificationRecord.catalog_api_key_id = authenticated key UUID`;
- human submitter fields are null;
- `AuditEvent.actor_id` remains null because the key is not a `User`;
- audit and queue payloads carry only the safe key ID/prefix/customer/integration snapshots already produced by `CatalogAuditAttribution`; raw token/hash must never be stored or returned;
- API-key requester identity comes only from the dependency, never body/header labels other than the authenticated credential itself.

For this mutating feedback route, successful authentication must not commit `CatalogApiKey.last_used_at` before request validation and symbol resolution. Refactor the Catalog authentication dependency/API narrowly so the route can authenticate and scope-check without an early commit, then persist `last_used_at` in the same authoritative transaction as the accepted clarification/workflow. Missing/invalid credentials, missing scope, malformed/unknown payloads, credential-bearing messages and unknown symbols must leave `last_used_at` unchanged. Preserve the existing committed-auth behavior for unrelated Catalog read routes unless a separately reviewed generalization is proven safe.

### 5.3 Requester, workflow executor and later withdrawal actor

Use distinct payload objects:

```json
{
  "requester": {"type": "user|catalog_api_key", "id": "...", "display_name": "safe optional snapshot"},
  "workflow_executor": {"type": "agent", "slug": "ed", "user_id": "..."},
  "withdrawal_actor": null
}
```

For `ReviewCaseAction`:

- human request: `created_by_type="human"`, `created_by_id=<session UUID>`;
- API-key request: `created_by_type="catalog_api_key"`, `created_by_id=<API-key UUID>` (the column is a generic UUID, not a user foreign key);
- `assigned_to` remains Ed's service-user UUID.

Do not attribute the request, feedback audit or action creation to Ed. Ed is the work owner/executor only. A later withdrawal actor, if F2.3 creates one, must be a separate authenticated human and must not overwrite the original requester.

### 5.4 Spoof rejection

The browser direct and wrapped command shapes must accept only:

- `command`;
- `symbolIds` (or one canonical server-supported spelling chosen during implementation; do not retain multiple undocumented aliases if tests prove they are unused);
- `comment`;
- `requestId` as a UUID idempotency key, never as identity.

Reject unknown keys with 422 before governed-symbol lookup, Ed-user resolution, authoritative DB mutation, queue creation or filesystem write. Session/API-key authentication may perform read-only credential lookup first but must not commit or flush. Tests must independently include `actorId`, `submittedBy`, `requester`, `requestedBy`, `createdBy`, `deciderName`, `deciderRole`, `updatedBy`, `managedBy`, and nested equivalents in both direct and wrapped bodies. Catalog's existing unknown-field rejection remains 400 and must receive the same no-side-effect assertions.

---

## 6. Transaction, retry and idempotency rules

### 6.1 Database transaction

A browser multi-symbol command is one all-or-nothing database transaction, as today. A Catalog request is one symbol/one authoritative transaction. Before the first write, every requested symbol must resolve through `PUBLISHED_SYMBOLS_SQL`; missing/duplicate/invalid selection fails the whole request.

For accepted Catalog feedback, the authenticated key's `last_used_at` update participates in that same authoritative transaction. There is no preliminary authentication commit on this route. Validation, scope failure or symbol lookup failure rolls back/closes without any committed key-usage update.

Within the transaction, each symbol produces exactly one new clarification record for a new idempotency key. A new `send_for_review` request produces one action/queue pair and reuses or creates the symbol's open review case.

### 6.2 Request-level idempotency and case serialization

Every browser and Catalog feedback mutation requires one caller-stable UUID idempotency key:

- browser direct/wrapped bodies use `requestId`; the frontend generates it once for the first network attempt, retains it across transport retries, and replaces it only when the user deliberately starts or edits a new submission;
- Catalog uses required `Idempotency-Key: <UUID>` and includes it in OpenAPI/examples;
- the key is request correlation only, never requester/actor identity;
- missing or malformed browser `requestId` returns 422; missing or malformed Catalog `Idempotency-Key` returns 400; both fail before authority-data lookup or writes.

Define these stable constants and canonical forms rather than leaving them to implementation choice:

- `PUBLISHED_FEEDBACK_IDEMPOTENCY_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "symgov/published-feedback-idempotency/v1")`;
- canonical route family `browser_published_feedback` for **both** v1 and legacy browser aliases, and `catalog_published_feedback` for Catalog;
- canonical browser target is the sorted unique lower-case UUID string set; canonical Catalog target is the trimmed, case-folded route `symbol_ref` string;
- canonical request object is `{route_family, target, command_or_kind, normalized_message, normalized_bounded_context, principal_type, principal_id}`;
- serialize it with `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")` and store the lower-case SHA-256 hex digest as the request fingerprint.

Create one deterministic **request anchor** independent of the resolved symbol set: `request_anchor_id = uuid.uuid5(PUBLISHED_FEEDBACK_IDEMPOTENCY_NAMESPACE, f"{principal_type}:{principal_id}:{request_key}:request")`. Persist it as an `AuditEvent` with `id == entity_id == request_anchor_id`, `entity_type="published_feedback_request"`, `action="published_feedback_request_accepted"`, and a safe payload containing the key, fingerprint, canonical route family, resolved sorted governed-symbol UUIDs and deterministic result IDs. For browser requests set `actor_id` to the authoritative session-user UUID and include the section 5.1 safe user snapshot. For Catalog set `actor_id = null` and include the section 5.2 safe API-key ID/prefix/customer/integration attribution. The anchor must never attribute the request to Ed. This is the indexed durable principal/key lookup; do not scan JSON.

Acquire a transaction-scoped request advisory lock derived from the request-anchor UUID, then load that `AuditEvent` by primary key **before any mutation**. Derive each signed PostgreSQL advisory-lock bigint exactly as `int.from_bytes(hashlib.sha256(label.encode("ascii") + uuid_value.bytes).digest()[:8], "big", signed=True)`, using label `published-feedback-request:` for request anchors and `published-feedback-symbol:` for governed symbols. Acquire the request lock first, then symbol locks in ascending UUID-string order; never acquire them in another order.

- same principal/key/fingerprint is a replay: reconstruct the authoritative result from the anchor payload and referenced rows, create no new intake/workflow/per-symbol-audit rows, and attempt only any permitted missing-runtime repair;
- same principal/key with a different fingerprint returns 409 `idempotency_conflict` before writes, including when browser symbol sets or the Catalog route target are completely disjoint;
- absent anchor proceeds through authentication, authorization, symbol/publication/Ed/case validation, then writes the request anchor in the same transaction as all per-symbol records.

Derive every per-symbol UUID with canonical lower-case UUID strings and this exact name form: `uuid.uuid5(PUBLISHED_FEEDBACK_IDEMPOTENCY_NAMESPACE, f"{request_anchor_id}:{symbol_id}:{purpose}")`. Stable purpose labels are:

- `clarification` for `ClarificationRecord.id`;
- `symbol-audit` for the per-symbol `AuditEvent.id` (and use the governed-symbol UUID, not this event ID, as that event's `entity_id`);
- `review-action` for `ReviewCaseAction.id`;
- `agent-queue` for `AgentQueueItem.id`;
- one runtime path from the deterministic queue UUID.

After the request lock, acquire one governed-symbol advisory lock for each normalized resolved symbol UUID in sorted order. The symbol locks prevent different keys from concurrently creating duplicate open cases. Under each symbol lock, load the one open `ReviewCase(source_entity_type="published_symbol", source_entity_id=<symbol>)`; fail closed if more than one exists. If absent, create it with Ed as owner and `current_stage="ux_feedback_coordination"`. If present, require Ed already owns it and preserve its current stage unchanged; a non-Ed owner returns 409 `review_case_owner_conflict` before writes. Never rewind an active case merely because new feedback arrived. Store `ed_queue_item_id` in the action payload and `review_action_id` in the queue payload so the pair is explicit.

Two different request keys are two intentional intake events and create two clarification records and, for review requests, two actions/queues while reusing the valid open case. Replaying the same key creates exactly zero additional governance intake/workflow/per-symbol-audit records. Catalog usage telemetry remains the separately defined exception in section 8.2. Do not infer retry equivalence from message text, open/terminal queue status or timing.

### 6.3 Database-first runtime queue mirror

No Ed-consumable runtime file may exist before the database transaction commits. The service constructs and flushes clarification/case/action/queue/audit rows plus an immutable runtime-envelope value but performs no filesystem write. The route commits that authoritative transaction first and only then materializes the runtime file.

Keep one runtime JSON path per durable Ed queue UUID. Materialize it with a temporary file plus atomic `os.replace`, never by partially overwriting the final file. Re-materializing the same queued DB item is idempotent and must produce the same semantic payload and path. A replay must attempt to materialize its existing queued item when the runtime file is missing; it must not create a second queue row.

If database commit fails, no runtime file has been exposed and the adapter rolls back all database work. If post-commit runtime materialization fails, do **not** claim rollback and do not delete the now-authoritative DB workflow: return a bounded 202 `accepted_pending_delivery` response, leave the queue row queued, emit/log an operationally visible missing-runtime condition, and permit safe retry with the same idempotency key and queue UUID. No Ed process can act on uncommitted work under this ordering.

A process/power failure after commit but before materialization can still leave a durable queued row with no runtime mirror. Existing reconciliation can detect this; same-key retry can repair it. Automatic transactional-outbox recovery remains F2.4. The F0.4 completion gate must not claim crash-proof exactly-once agent execution or guaranteed automatic delivery.

### 6.4 Request retry contract

- Same principal + same idempotency key + same fingerprint returns the original authoritative IDs/lifecycle result and may safely repair a missing runtime mirror; it creates no new clarification, governance audit, action or queue. Delivery status reflects the current mirror after that attempt, so a prior 202 becomes browser HTTP 200 or Catalog HTTP 201 once repair succeeds.
- Same principal + same key + different fingerprint returns 409 before writes.
- Different key means a distinct intentional comment/review request, even when text is identical.
- Repeating runtime materialization for the same queue ID is safe.
- Concurrent same-key submissions converge on one deterministic record set; primary-key conflict handling must re-read and verify the fingerprint rather than returning an unhandled 500.
- Completed/failed workflow processing remains historical work. A same-key retry returns that same work; a new key creates new intake. F0.4 does not resurrect or overwrite terminal queue state.

---

## 7. Audit and fail-before-write requirements

Every newly accepted feedback request writes one deterministic request-anchor `AuditEvent` defined in section 6.2 plus one deterministic per-symbol `AuditEvent`. A same-key replay writes neither again. Each per-symbol event contains:

- entity type/id for the governed published symbol;
- action distinguishing comment versus review request and browser versus Catalog route where existing conventions require;
- human actor UUID for session requests, null actor plus safe key attribution for Catalog requests;
- request-anchor ID, clarification ID, idempotency key and canonical request fingerprint (none is an authentication secret);
- exact published revision, canonical page anchor and every page/pack placement observed at intake;
- the complete normalized publication snapshot when the symbol appears in more than one page/pack;
- review-case/action/queue IDs when review was requested;
- `publication_transition: null` and `published_availability_changed: false`;
- requester and workflow-executor objects from section 5.

Validation, authentication, authorization and authority-data checks must occur before:

- `session.add`, `flush`, `execute` mutation or lifecycle assignment;
- Ed service-user creation/update;
- review-case/action/queue creation;
- audit creation;
- runtime directory/file creation.

For the Catalog route, “no side effect” includes no committed `CatalogApiKey.last_used_at` update. A valid key presented with an invalid request is authenticated but has not successfully used the feedback operation.

Use real FastAPI dependency guards and recording sessions/filesystems in tests. After every 400/401/403/404/422 and injected pre-write failure, assert no clarification, case, action, queue, audit, revision/page/pack mutation, commit, or runtime file delta.

Do not add an audit event claiming withdrawal, unpublication, retirement, deprecation, replacement or supersession.

---

## 8. API, data, worker and UI contracts

### 8.1 Browser API

Keep both mounted paths and their current authentication policy:

- `POST /api/v1/published/symbols/commands`;
- legacy `POST /api/published/symbols/commands`.

Both must have identical request, requester-attribution, lifecycle-preservation and no-side-effect behavior. Continue accepting the frontend's wrapped `{ "payload": {...} }` shape and the direct object shape only if both are covered explicitly.

Both aliases return HTTP 200 for a fully accepted/materialized request. The exact review-request body is:

```json
{
  "status": "completed",
  "command": "send_for_review",
  "managedBy": "ed",
  "publishedAvailabilityChanged": false,
  "items": [{
    "symbolId": "...",
    "commentId": "...",
    "reviewCaseId": "...",
    "edQueueItemId": "...",
    "remainsPublished": true,
    "requestReplayed": false,
    "workflowDeliveryState": "materialized"
  }]
}
```

The exact successful comment body is also HTTP 200:

```json
{
  "status": "completed",
  "command": "comment",
  "managedBy": null,
  "publishedAvailabilityChanged": false,
  "items": [{
    "symbolId": "...",
    "commentId": "...",
    "reviewCaseId": null,
    "edQueueItemId": null,
    "remainsPublished": true,
    "requestReplayed": false,
    "workflowDeliveryState": "not_applicable"
  }]
}
```

A same-key replay returns HTTP 200 with the same authoritative IDs and `requestReplayed: true` without additional governance writes. Comments never return pending delivery because they create no Ed runtime handoff.

If the authoritative review-request DB transaction commits but one or more runtime mirrors cannot be materialized, both aliases return HTTP 202 with the review body shape, `status: "accepted_pending_delivery"`, and `publishedAvailabilityChanged: false`. Every item reports its own `workflowDeliveryState`: `materialized` when its final mirror exists, otherwise `pending`; every item still reports `remainsPublished: true`. A same-key replay attempts only missing mirrors, never rewrites a materialized/terminal item, and returns the newly observed per-item states. The top-level status remains pending while any item is pending. Do not return 5xx language implying the database work rolled back.

### 8.2 Catalog API

Keep `POST /api/v1/catalog/symbols/{symbol_ref}/feedback` and `catalog.feedback.write` authorization. Preserve the bounded public response (no private review/action/queue IDs). Change:

- require `Idempotency-Key` as a UUID and document 400/409 replay-conflict behavior;
- `mutatesPublishedState` to `false` for every kind, including `send_for_review`;
- retain `reviewRequested: true` for `send_for_review`;
- require `remainsPublished: true` and a bounded `requestReplayed` boolean in the response.

The exact Catalog 201 body is:

```json
{
  "status": "recorded",
  "feedbackId": "...",
  "kind": "send_for_review",
  "symbol": {"displayId": "...", "symbolId": "..."},
  "reviewRequested": true,
  "mutatesPublishedState": false,
  "remainsPublished": true,
  "requestReplayed": false,
  "workflowDeliveryState": "materialized"
}
```

For a comment, `reviewRequested` is false and `workflowDeliveryState` is `not_applicable`. For a post-commit review-delivery failure, return HTTP 202 with the same bounded shape, `status: "accepted_pending_delivery"`, and `workflowDeliveryState: "pending"`; do not expose case/action/queue IDs. A repaired same-key replay returns 201, the original `feedbackId`, `requestReplayed: true`, and `workflowDeliveryState: "materialized"`.

Catalog usage telemetry is deliberately per successful HTTP attempt rather than per governance intake: each 201 or 202, including a same-key replay, may create one best-effort `CatalogApiUsageEvent` after authoritative handling, with its actual 201/202 status code. It does not create a new request anchor, clarification, per-symbol audit, action or queue. A 400/401/403/404/409/422 path creates no usage row and no `last_used_at` update.

Update `backend/symgov_backend/catalog_developer.py` generated request/response schema and prose so no integration documentation says review mutates publication. The generated operation must declare required `Idempotency-Key` UUID header plus separate 201 and 202 responses referencing the bounded Catalog feedback response schema, the exact 503 pause response, and 400/409 errors; tests must validate all status descriptions/examples.

### 8.3 Data contract

No new table or column is required. Continue using:

- `ClarificationRecord.submitted_by` for human sessions;
- `ClarificationRecord.catalog_api_key_id` for Catalog integrations;
- non-nullable `ClarificationRecord.published_page_id` for the deterministic canonical-page compatibility anchor, with complete placement scope in `context_json`;
- `ReviewCase.owner_id` for Ed ownership;
- `ReviewCaseAction.created_by_type/created_by_id` for requester provenance;
- `assigned_to`/`target_agent_slug` for executor routing;
- `AuditEvent.id/entity_id` for the indexed deterministic request anchor and `actor_id` plus safe payload snapshots.

Store the UUID idempotency key and canonical fingerprint in the deterministic request-anchor audit payload and reference that anchor from existing JSON payload/context fields. Load the anchor by `AuditEvent.id`; do not scan unindexed JSON to establish uniqueness. The PostgreSQL advisory lock plus deterministic primary keys supplies the concurrency boundary; a same-key primary-key conflict is resolved by re-reading the anchor and verifying its fingerprint.

Do not overload `SymbolRevision.lifecycle_state` with review-work state.

`CatalogApiKey.last_used_at` already exists. F0.4 changes only when the feedback route commits it; no new persistence field is needed.

### 8.4 Worker contract

Ed receives requester provenance and the exact published revision/page identity as context. Ed may coordinate clarification/classification/re-review work. Ed must not change publication lifecycle and must not claim to be the requester. Existing `published_symbol_review_request` task type may remain; update its payload contract and external-workspace fixture tests.

F0.4 does not change Ed's authority into publication/withdrawal authority.

### 8.5 Frontend contract

- `submitPublishedSymbolCommand()` sends no actor/requester identity fields; it sends one UUID `requestId` retained across retries of the same submission and regenerated for a deliberate new submission.
- The modal explicitly says that requesting review opens review work and the current published revision remains available unless an authorized human later withdraws it.
- Success copy says “Review requested; the published symbol remains available.”
- The UI derives that copy from the command plus required structured lifecycle fields; it must not render arbitrary `result.message` as the lifecycle assurance. A contradictory/missing lifecycle contract fails closed with a generic non-success notice and must not claim publication was preserved.
- HTTP 202 displays “Review recorded; Ed delivery is pending. The published symbol remains available.” without automatically resubmitting a new clarification.
- Add a Node/source-contract test for the exact payload and lifecycle copy. Existing frontend suites have no direct test for this API helper.

### 8.6 Fail-closed activation and Ed-claim gate

Add repository-owned `backend/symgov_backend/published_feedback_gate.py`. It reads, on every check, the absolute marker path from `SYMGOV_PUBLISHED_FEEDBACK_PAUSE_FILE`, defaulting to `/data/symgov-runtime/maintenance/published-feedback.pause`; a regular file at that path means paused. The production API and Ed worker must see the same mounted path. Do not cache marker state across requests or worker iterations.

After read-only authentication/scope validation but before body processing that can mutate, API-key `last_used_at`, usage telemetry, authority-data writes or runtime work, both browser aliases and the Catalog feedback route check the gate. While paused they return HTTP 503, header `Retry-After: 60`, and this exact secret-safe body:

```json
{
  "error": "published_feedback_paused",
  "detail": "Published feedback and review requests are temporarily unavailable.",
  "retryable": true
}
```

The gate applies to `comment` and `send_for_review`; read-only published/catalogue routes remain available. A paused request creates no `last_used_at`, usage, clarification, request/per-symbol audit, case, action, queue or runtime side effect.

Immediately before changing a queued `AgentQueueItem` for task type `published_symbol_review_request` to a claimed/running state, `agent_queue_worker.py` checks the same marker. While paused it leaves the DB row and runtime file queued/unchanged and skips that item. The authenticated workspace-operator response at `GET /api/v1/workspace/agent-worker-health` must expose field `publishedFeedbackClaimsPaused: true|false`; do not expose the filesystem path publicly. After creating the marker, activation waits for any already-running published-symbol request to reach terminal state and verifies no new such claim starts. Other task types continue normally.

Add focused tests for both aliases, Catalog, zero side effects, live marker create/remove without process restart, matching-worker claim suppression, unrelated queue processing and the health flag.

---

## 9. Migration and historical-data implications

**Conclusion: no database migration is justified for F0.4.** Existing nullable requester columns, non-nullable canonical page association, generic action creator UUID/type, indexed audit primary key, audit payload JSON and queue payload JSON can express the required separation. Multi-placement scope is carried in the immutable snapshot while the required page column receives the deterministic compatibility anchor; the implementation must not attempt to persist null there.

Historical treatment:

- do not rewrite F0.3 documents that correctly recorded “not deployed” at their creation time;
- do not change historical clarification records attributed to Ed, because the real browser requester cannot be inferred reliably;
- do not restore revisions historically moved to `review` automatically—some may have since been republished, replaced or intentionally altered;
- do not rewrite historical review actions, queue payloads or audit actors;
- new writes after F0.4 must satisfy the authoritative contract;
- existing open workflows remain readable. If implementation encounters duplicated/corrupt open workflow rows, fail closed and report them rather than guessing a canonical row.

A pre-deployment read-only query/report should count currently published pages whose referenced revision is non-published and open published-symbol review cases with duplicate active action/queue pairs. Any repair is a separate, explicitly authorized data decision, not an automatic F0.4 migration.

---

## 10. Rollback and deployment/runtime implications

### 10.1 Local implementation boundary

Implementation may change backend, frontend and Ed queue payload construction. No migration is expected. The implementation session must not push, deploy, restart, mutate production, publish/withdraw symbols or send messages unless separately authorized.

### 10.2 Later release

A later authorized release must use one reviewed immutable commit for backend and frontend. Because F0.4 changes the Ed queue payload contract and runtime-file creation behavior, the deployed backend and active Ed worker/runner compatibility must be verified before resuming requests. If Ed's external runner needs a contract update, include and independently review that exact external-workspace path or use an accept-compatible additive payload.

Executable activation sequence, under separate production-change authorization:

1. record current release identity/health and inspect active published-symbol requests plus runtime orphans/missing mirrors read-only;
2. **stop the pre-F0.4 API/worker before claiming any pause is active**. On the current deployment, separately authorize and run `docker stop symgov-hermes-api`; because API and repository-owned workers share that process, do not proceed until the container is stopped, its worker loop has exited, no external Ed child process from that container remains, and all three mutation probes are externally non-successful/unreachable. Record DB publication/workflow/audit counts after shutdown;
3. account for in-flight old work: verify no old API transaction remains, no `published_symbol_review_request` queue item is claimed/running, and the recorded publication/workflow/audit counts stay stable across the drain check. If a request completed during shutdown or a worker remains active, halt activation for explicit review; do not guess, delete work or repair publication automatically;
4. ensure the persistent shared marker parent exists with operator-only write permissions and atomically create `/data/symgov-runtime/maintenance/published-feedback.pause` while the old API remains stopped;
5. activate the reviewed backend and repository-owned Ed worker first, with `SYMGOV_PUBLISHED_FEEDBACK_PAUSE_FILE` pointing to that existing marker; keep the old frontend for now. Verify authenticated requests to `/api/v1/published/symbols/commands`, `/api/published/symbols/commands` and `/api/v1/catalog/symbols/{known_ref}/feedback` each return the exact 503/header/body above, with no DB/runtime delta; verify reads return normally;
6. verify worker health says `publishedFeedbackClaimsPaused: true`, enqueue/use only an authorized synthetic queued fixture if needed, and prove no `published_symbol_review_request` claim starts while unrelated tasks remain processable;
7. activate the frontend from the same immutable reviewed commit; verify backend/frontend commit identity, Catalog generated OpenAPI 201/202/503 contracts, no migration (`alembic current == heads`) and normal public read health while intake remains paused;
8. run anonymous/authenticated/API-key, spoof, read-model preservation, requester-attribution, idempotency and queue-reuse smoke probes only where they can remain side-effect-free under the gate. Do not remove the marker merely to run mutation smoke without separate authorization for synthetic writes and cleanup;
9. after all compatibility checks pass, atomically remove the marker under explicit authorization, verify all three mutation paths no longer return `published_feedback_paused`, run one separately authorized synthetic comment/review smoke if approved, remove that synthetic data through an authorized procedure, and verify absence;
10. resume normal operation only after worker health reports `publishedFeedbackClaimsPaused: false`, no missing runtime mirror exists for the smoke request, public read identity is unchanged and all evidence is recorded.

No Alembic migration or database backup is required if the implementation remains migration-free, but verify production Alembic `current == heads`.

### 10.3 Rollback

Code rollback would reintroduce silent unpublication and false Ed attribution. Preferred recovery is roll-forward while the new backend/worker gate remains active; frontend rollback is permitted while the corrected backend stays paused. Before any rollback, create the marker on the still-correct backend, verify exact 503 responses on all three mutation paths, verify Ed claim pause and wait for active claims to drain.

Do **not** expose a pre-F0.4 backend after rollback because it cannot honor the marker. If backend rollback is unavoidable, separately authorize and stop the public API before replacing it (current service/container `symgov-hermes-api`), verify externally that neither browser alias nor Catalog feedback is reachable, and leave it stopped or behind a separately reviewed gateway deny rule until a corrected gate-capable backend is restored. Merely leaving the marker beside an old backend is not a rollback control. A rollback must not change revision states or delete review work automatically.

---

## 11. Acceptance tests and exact completion gate

### 11.1 New focused acceptance file

Create `tests/test_f0_4_review_without_unpublication.py` and exercise the real service plus both real FastAPI route surfaces. Cover at least:

1. Browser comment by ordinary authenticated user:
   - clarification requester is session user;
   - no Ed workflow;
   - revision/page/entry/pack snapshot unchanged;
   - request-anchor and per-symbol audit human actor are the session user;
   - both aliases return the exact HTTP 200 comment body with null workflow IDs and `workflowDeliveryState: "not_applicable"`.
2. Browser review request by ordinary user, reviewer and admin:
   - same publication snapshot remains queryable before/after;
   - clarification requester is each session user;
   - Ed is owner/assignee/executor only;
   - one case/action/queue/runtime file;
   - no lifecycle assignment;
   - both aliases return exact HTTP 200 materialized or HTTP 202 per-item pending bodies.
3. Catalog comment and `send_for_review` with valid scoped key:
   - key requester is durable;
   - audit human actor is null with safe key attribution;
   - response says `mutatesPublishedState: false`;
   - publication remains queryable.
4. Repeated/concurrent review requests:
   - same principal/key/fingerprint, sequentially or concurrently, yields one deterministic request anchor plus one clarification/action/queue/runtime path per symbol and stable authoritative IDs; only pending-versus-materialized delivery status may advance;
   - same principal/key with changed command, symbols (including a completely disjoint set), Catalog route target, message or context returns 409 before writes;
   - different keys create distinct clarification/action/queue rows while reusing the one open case;
   - a reused Ed-owned case preserves its current stage, while duplicate cases or a non-Ed owner fail closed before writes.
5. Direct and wrapped spoof matrix for every actor-like key in section 5.4:
   - correct 422 browser contract;
   - after read-only authentication, no governed-symbol/Ed/workflow query and no add/flush/commit/file side effect.
6. Catalog unknown/spoof fields:
   - 400 and no key `last_used_at`, authoritative/usage/workflow/file side effect.
7. Anonymous/missing session on v1 and legacy browser routes:
   - 401 and no side effect.
8. Missing/invalid API key and missing scope:
   - 401/403 and no key `last_used_at`, feedback/workflow/audit/runtime side effect.
9. Missing/malformed idempotency key, missing symbol, duplicate browser selection, too many symbols, malformed body and empty comment:
   - browser missing/malformed `requestId` is 422, Catalog missing/malformed header is 400, and every case fails before writes and leaves public state unchanged.
10. Injected failures at Ed lookup, case/action/queue/audit flush and database commit:
    - no accepted/success response;
    - DB rollback;
    - no runtime file was ever exposed.
11. Injected post-commit runtime materialization failure:
    - one authoritative clarification/workflow/audit transaction remains committed;
    - no partial final runtime file exists;
    - browser and Catalog responses use their exact bounded 202 `accepted_pending_delivery` contracts rather than a false rollback/success claim;
    - a multi-symbol browser batch reports `materialized` or `pending` per item;
    - same-key retry attempts only pending mirrors, reuses the same clarification/case/action/queue IDs and materializes the same path without duplicate work.
12. Multi-placement publication:
    - several pages/packs for one published revision produce one clarification/workflow, a non-null deterministic canonical page anchor and a complete stable publication snapshot independent of SQL row order;
    - several simultaneously published revisions for one symbol produce 409 before any write;
    - no route chooses whichever SQL row arrived first.
13. Deterministic idempotency identity:
    - expected request-anchor, clarification, per-symbol audit, action and queue UUIDv5 values are asserted for browser and Catalog principals using the exact namespace/name/purpose-label contract;
    - request-anchor actor/safe-attribution fields are session user for browser, null actor plus safe key attribution for Catalog, and never Ed;
    - v1/legacy browser aliases share one route family and replay anchor;
    - direct primary-key anchor lookup rejects same-key disjoint targets without JSON scans;
    - action and queue payloads cross-reference each other;
    - primary-key race/conflict handling re-reads and verifies the stored fingerprint;
    - no retry behavior depends on mutable queue status or message/timing heuristics.
14. Catalog public and telemetry contract:
    - exact secret-safe 201/202 bodies and generated OpenAPI responses are asserted;
    - each accepted/replayed 201/202 attempt may add one usage telemetry row with the actual status, while governance intake/audit/workflow rows remain exactly once;
    - 400/401/403/404/409/422 adds no usage event or `last_used_at` mutation.
15. No implicit transition:
    - no `lifecycle_state` assignment in the feedback service path;
    - no page, entry or pack mutation;
    - no withdrawal/deprecation/supersession audit.
16. Activation gate:
    - live marker create/remove takes effect without restart for both browser aliases, Catalog and matching Ed claims;
    - paused mutations return exact 503/`Retry-After` and create no side effect, while reads and unrelated queue tasks continue;
    - worker health reports pause state and an already queued published-symbol item remains unchanged;
    - activation/rollback tests and release-procedure checks prove a pre-F0.4 API/worker is stopped and drained before marker protection is relied upon; a pre-F0.4 backend can never be treated as marker-protected.

### 11.2 Existing tests to invert/update

- `tests/test_published_feedback_service.py`
  - replace the assertion that revision becomes `review` with exact unchanged-publication and requester/executor assertions;
  - add canonical-page multi-placement normalization, request-anchor replay/disjoint-conflict, different-key work creation, open-case owner/stage preservation, explicit queue/action pairing and database-first runtime-envelope cases.
- `tests/test_published_symbol_review_workflow.py`
  - rename the unpublication test and assert `published` plus preserved read-model identity;
  - retain external Ed handoff compatibility tests.
- `tests/test_published_symbol_feedback.py`
  - add session attribution, required `requestId`, strict direct/wrapped validation, v1/legacy parity and response-contract cases.
- `tests/test_catalog_feedback.py`
  - invert `mutatesPublishedState` and revision assertions;
  - retain two distinct-key requests as two work items, and add same-key replay/concurrency as exactly one deterministic work item;
  - preserve safe response, auth and transaction partitions; assert per-attempt 201/202 usage telemetry versus exactly-once governance records and no telemetry on error paths.
- `tests/test_catalog_developer_openapi.py`, `tests/test_catalog_developer_routes.py` and the generated-artifact/source-contract partition
  - prove every documented feedback example and response says review does not mutate publication, 202 pending-delivery remains secret-safe, and the required idempotency header plus exact 503 pause contract appear in generated OpenAPI.
- `tests/test_agent_queue_state_machine.py`
  - extend the existing reconciliation/state-machine coverage to prove that a post-commit missing runtime mirror is detectable, deterministic re-materialization does not corrupt reconciliation, and the shared pause marker suppresses matching Ed claims without blocking unrelated tasks. Do not reference a nonexistent separate reconciliation test file.
- `tests/test_catalog_api_auth.py`
  - preserve secret-safe authentication and unrelated read-route behavior;
  - add the non-committing feedback-auth path and prove `last_used_at` commits only with accepted feedback.
- `tests/test_catalog_feedback_model.py`
  - retain exactly-one requester persistence coverage.
- `tests/test_route_auth_enforcement.py`
  - include v1/legacy published command anonymous and authenticated outcomes if not already exhaustive.
- `frontend/src/publishedFeedbackApi.test.js` (new) or an equivalently narrow existing Node test
  - prove wrapped payload contains no actor fields, one UUID request ID is retained across retry and regenerated for deliberate new submission, UI/source contract states publication remains available, 202 copy is bounded, and arbitrary server `message` cannot override lifecycle assurance.

### 11.3 Goal-local commands

From repository root:

```bash
python3 -m py_compile \
  backend/symgov_backend/services/published_feedback.py \
  backend/symgov_backend/published_feedback_gate.py \
  backend/symgov_backend/agent_queue_worker.py \
  backend/symgov_backend/routes/published.py \
  backend/symgov_backend/routes/catalog.py \
  backend/symgov_backend/routes/workspace.py \
  backend/symgov_backend/catalog_api_auth.py \
  backend/symgov_backend/catalog_developer.py \
  backend/symgov_backend/schemas.py \
  tests/test_f0_4_review_without_unpublication.py \
  tests/test_published_feedback_service.py \
  tests/test_published_symbol_feedback.py \
  tests/test_published_symbol_review_workflow.py \
  tests/test_catalog_feedback.py \
  tests/test_catalog_feedback_model.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend uv run --isolated \
  --with-requirements backend/requirements.txt \
  --with-requirements backend/requirements-test.txt \
  python -m pytest -q -p no:cacheprovider \
  tests/test_f0_4_review_without_unpublication.py \
  tests/test_published_feedback_service.py \
  tests/test_published_symbol_feedback.py \
  tests/test_published_symbol_review_workflow.py \
  tests/test_catalog_feedback.py \
  tests/test_catalog_feedback_model.py \
  tests/test_catalog_developer_openapi.py \
  tests/test_catalog_developer_routes.py \
  tests/test_catalog_api_auth.py \
  tests/test_route_auth_enforcement.py \
  tests/test_agent_queue_state_machine.py

./scripts/test-backend.sh
./scripts/test-backend.sh --external
./scripts/test-frontend.sh
./scripts/build-frontend-isolated.sh
./scripts/test-verification-scripts.sh
git diff --check
```

If the actual changed Python path set differs, compile every changed/untracked Python file rather than relying on the provisional list. The external partition is mandatory if the Ed external-workspace runner or fixture contract changes. Langfuse is not goal-local unless implementation touches its paths; retain it for the later broader release gate.

### 11.4 Exact completion gate

F0.4 is complete only when all of the following are true on one frozen snapshot:

1. Every feedback/review-request path preserves the exact published read-model identity and makes no publication lifecycle transition.
2. Each accepted request records the session or API-key requester authoritatively; human requests are not attributed to Ed.
3. Requester, Ed executor and any future withdrawal actor are distinct in durable data/payload semantics.
4. Same-key request processing is serialized through one indexed deterministic request anchor and converges on one clarification/action/queue/runtime handoff per symbol, even for alias routes and disjoint-target conflicts; a different key creates distinct work while a reused Ed-owned open case preserves its stage.
5. Direct/wrapped/browser and Catalog spoof/unknown fields fail before writes.
6. Anonymous, invalid, missing-scope, missing-symbol and invalid requests leave no API-key `last_used_at`, usage-event, feedback/workflow/audit/runtime side effects; accepted/replayed Catalog attempts retain the explicitly separate telemetry contract.
7. No runtime file is exposed before DB commit; commit failure leaves no file, while post-commit materialization failure leaves durable queued workflows and returns exact per-item browser/Catalog pending-delivery semantics that retry repairs idempotently.
8. Browser 200/202 and Catalog 201/202 responses, generated OpenAPI and UI say review does not alter published availability and expose no forbidden private IDs; browser comments have the exact no-workflow body.
9. Multi-placement requests use the schema-valid non-null canonical page anchor plus complete immutable placement snapshot; no migration or historical backfill is introduced unless the specification is reopened and freshly reviewed.
10. All goal-local commands pass with exact counts/durations recorded.
11. Fresh Stage 1 returns PASS and fresh Stage 2 returns APPROVED on unchanged per-path and patch hashes.
12. Final verification repeats the approved gate and creates at most one separately authorized local checkpoint commit. Push/deployment remain separate authority decisions.
13. The repository-owned pause marker returns exact 503 responses on both browser aliases and Catalog, suppresses matching Ed claims without blocking reads/unrelated tasks, and has an executable backend-first activation plus fail-closed rollback procedure.

---

## 12. Broader regression partitions

The implementation review must partition evidence rather than claim one guessed full suite:

- focused F0.4 publication/feedback/queue/auth tests;
- portable backend wrapper;
- external Ed workspace partition if its contract is exercised or changed;
- frontend Node tests;
- isolated frontend build outside repository;
- verification-wrapper contracts;
- whitespace/syntax checks;
- later pre-production full backend, Langfuse and live smoke gates before release.

Particular regressions to preserve:

- F0.2 route authorization matrix;
- F0.3 session-authoritative review/publication attribution;
- Catalog API-key auth/scope and secret-safe responses;
- published favourites/read/preview/download behavior;
- agent queue state/reconciliation behavior;
- Ed external review coordination;
- Rupert publication persistence and read-model creation.

---

## 13. Serialized implementation and independent-review chain

Use the shared clean repository with one serialized chain:

1. **Cody implementation**
   - implement only F0.4;
   - first record RED evidence for unpublication, false Ed/request-anchor attribution, same-key transport-retry duplication, pre-commit runtime exposure, ambiguous publication-row selection, invalid-request auth commits, spoof acceptance, mixed-version browser response ambiguity and absent activation/Ed-claim pause enforcement;
   - implement the minimum migration-free correction;
   - run focused and goal-local gates;
   - do not commit.
2. **Fresh Stage 1 specification review**
   - independent, read-only review against this spec, the master backlog and live code;
   - freeze HEAD, branch/status, staged/unstaged/HEAD-to-worktree patch hashes and SHA-256 of every changed/untracked path;
   - require exact lifecycle, requester, authorization, idempotency, no-side-effect, API/UI and historical-data coverage.
3. **Fresh Stage 2 security/code-quality review**
   - only after Stage 1 PASS on the same frozen snapshot;
   - emphasize request spoofing, role/scope boundaries, fail-before-write order, concurrency, DB/filesystem compensation, audit semantics, secret handling and runtime compatibility.
4. **Final verification/local checkpoint**
   - only after Stage 2 APPROVED;
   - repeat all goal-local commands against unchanged hashes;
   - update completion evidence and concrete handoff;
   - create one local conventional commit only if separately authorized by the implementation card.

Any implementation/test edit invalidates downstream approvals. Preserve a failed review, create a correction card, and run fresh replacement Stage 1 → Stage 2 → final verification. Do not reuse stale approvals.

### Planning-stage independent review record

The first independent planning review examined specification SHA-256 `773b6a1039757dc16739fe83b2296beccaa321e5395aea5b9b181de4a61f46fe` against live code and returned **FAIL**. Its blocking findings were preserved rather than hidden:

1. runtime JSON could become Ed-executable before the database commit;
2. symbol-level APIs could silently choose the first of multiple page/pack/revision rows;
3. retry identity/workflow semantics were not implementable safely from mutable action/queue status, and Catalog authentication committed `last_used_at` before request validity was known.

The corrected specification requires database-first runtime exposure, complete publication-placement normalization with ambiguous-revision rejection, caller-stable deterministic request idempotency, separate request/symbol concurrency locks, and accepted-transaction-only Catalog key usage.

A second independent review observed the specification changing during review and therefore correctly returned **FAIL** rather than attaching approval to a moving hash. On its final observed SHA-256 `1826595cd58bff987980ab44a5f6608fb97f6105e3b963d62f6796ac5f5d05ec`, it also found four substantive blockers: null multi-page persistence contradicted the non-null schema/no-migration decision; per-symbol deterministic IDs could not detect same-key disjoint targets; Catalog lacked an exact 202/OpenAPI contract; and replay telemetry/case-stage/partial-delivery semantics were incomplete. This revision resolves those with a deterministic non-null canonical page anchor, indexed request-level audit anchor, exact Catalog 201/202/OpenAPI contract, explicit usage telemetry partition, preserved Ed-owned case stage and per-item delivery state.

A third independent review held SHA-256 `4a14666d4085ab5c8a2267bf8f76ac718f332a550d73ff4f37f3cb13969fe529` frozen from opening through closing and returned **FAIL**. It confirmed every earlier blocker resolved, then found that activation/rollback required an undefined pause mechanism, browser normal/comment status bodies were incomplete, request-anchor attribution was not explicit, and per-symbol UUID purpose serialization was not reproducible. This revision resolves those findings with the repository-owned shared marker/API-and-Ed-claim gate and executable backend-first activation/rollback rules, exact browser 200/202 comment/review bodies, explicit anchor actor/key attribution, and exact per-symbol UUIDv5 name/purpose constants.

A fourth independent review held SHA-256 `dcf3ecdc21000d44f87aad9f9caf2810b486ebbf6b88c82fea1733be2250a7c7` frozen and returned **FAIL** with one blocker and no additional important findings. It confirmed the marker implementation, browser/Catalog contracts, anchor attribution, UUID derivation and prior corrections, but correctly found that activation created a marker the old API/worker could not honor while leaving that old process live. This revision now requires separately authorized shutdown of the co-located pre-F0.4 API/worker, verification that mutation routes are unreachable, drainage/accounting for in-flight API and Ed work, marker creation while stopped, and only then corrected backend-first activation.

A fifth fresh independent review of one now-frozen corrected hash is mandatory before this planning package is considered ready; none of the four earlier FAIL decisions is an approval.

---

## 14. Explicit exclusions and residual risks

F0.4 does not:

- add a withdrawal, replacement, retirement or republish service (F2.3);
- centralize all revision/review-case transitions (F2.2a/F2.2b);
- make all agent queues crash-proof transactional outboxes (F2.4);
- add reviewer discipline eligibility (F1.1-F1.3);
- backfill historical requester or actor data;
- repair historical publication state automatically;
- grant agents or API keys publication/withdrawal authority;
- redesign general support intake (F4.6);
- push, deploy, migrate, restart, publish, withdraw or message externally in the implementation session without separate authorization.

Residual after completion: the database-first DB/runtime-file handoff still has a hard-crash boundary after authoritative commit and before runtime materialization, explicitly bounded above and scheduled for the broader F2.4 reliable outbox design. It cannot expose uncommitted work, remains detectable, and is safely repairable by replaying the same idempotency key to re-materialize the same queue UUID. This does not permit F0.4 to claim automatic recovery or leave same-key duplication untested.

---

## 15. Concrete restart handoff for implementation

```text
Continue the Symgov Trial Readiness programme in /data/symgov.

Read first:
- docs/plans/2026-07-26-symgov-trial-readiness-implementation-backlog.md
- docs/plans/2026-07-28-f0-4-review-without-unpublication-spec.md
- docs/plans/2026-07-28-f0-3-deployment-addendum.md
- docs/plans/2026-07-27-f0-3-session-authoritative-attribution-spec.md

Verify branch, status, HEAD, origin/main and the production backend/frontend release
identity before editing. The planning baseline was clean main/origin/main at
45fc6e00b1372fce1e092ebe282f264ccd401cb3, deployed from the immutable
/data/symgov-releases/f0.3-45fc6e0 worktree. Treat that as point-in-time evidence
and stop to reconcile any difference.

Implement F0.4 only through the durable serialized Symgov Kanban/Cody lane:
implementation -> fresh immutable Stage 1 specification review -> fresh immutable
Stage 2 security/code-quality review -> final verification/local checkpoint.

Required outcome: browser-session and scoped API-key feedback/review requests create
properly attributed durable clarification/Ed work without changing revision, page,
entry or pack publication state. Ed is executor, never the human requester. Reject
spoofed actor fields before writes; preserve v1/legacy browser parity; keep Catalog
scope enforcement; normalize every public placement and reject ambiguous concurrent
published revisions; require caller-stable UUID idempotency keys, one indexed request anchor and
exact deterministic per-symbol clarification/audit/action/queue identities; expose runtime work
only after DB commit; implement the shared API/Ed-claim pause marker and exact browser 200/202 plus
Catalog 201/202 contracts; test pending-delivery retry and state the hard-crash boundary honestly.
No migration or historical backfill is expected.

Run every command and acceptance partition in sections 11-12. If a review finds an
actionable issue, preserve it and create a correction plus fresh replacement reviews.
Do not implement F0.5 or F2.3/F2.4.

Do not push, deploy, migrate, restart services/gateways, publish/withdraw symbols,
send external messages, or clean/reset/stash unrelated work without separate
authorization. Do not expose credentials.
```
