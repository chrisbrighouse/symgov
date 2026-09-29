# Ed Stage 6 — evaluation and security review report

Date: 2026-09-29. Authorised by Chris as step 4 of decision §7.5.

**Status: evaluation and review complete; sign-off not yet given.** Every defect the review found has been fixed or is listed below with its disposition. Activation remains a separate, held step. The controlling documents are the Ed spec (`2026-09-27-ed-application-guru-spec.md`), the implementation plan, and the Stage 4 plan §9.

## 1. What was evaluated

- **Code.** `main` from `e07ffca` (the Stage 3 API) through the Stage 6 fixes. That covers the pilot gate, the Stage 4 corpus, retrieval and signed approval, the ICS attribution, Slice D, and the Stage 5 chat screen at `/ed`.
- **Knowledge.** The committed bundle `3755a0df9286`: 29 steward-reviewed claims across all ten topics. It is unsigned, so it is not yet served.
- **Not evaluated here.** How a real model behaves. No provider credential is available in this environment, so every provider response in the tests is scripted. That evaluation belongs to activation (§6).

## 2. Retrieval evaluation — real questions, real bundle

The fixture is `backend/symgov_backend/data/ed_knowledge/evaluation.stage6.json`, and the test is `tests/test_ed_evaluation.py`. The test verifies the committed bundle first.

| Measure | Result |
|---|---|
| Realistic questions, one or more per approved claim | 30 |
| Supporting claim within its expected rank | **30 / 30** |
| Supporting claim ranked first | 27 / 30 (the other three rank second or within the top four) |
| Off-topic questions correctly answered with nothing | **4 / 4** |
| Every approved claim is some question's expected answer | yes (enforced by the test) |
| Retrieval time per question | 3–10 ms |
| Bundle verification plus signature check at load | about 7 ms plus about 12 ms, once per process, then every 10 minutes |

The evaluation found one defect, now fixed. "What's the weather in Paris?" matched passages, because "what's" leaves a lone "s" token. Single-letter tokens are now ignored, which changed the index. The bundle was rebuilt with identical claims.

**Coverage gaps.** No approved source answers these questions, so Ed correctly has no approved answer to them. Each needs a further steward-approved source:

- "How do I switch organization?"
- "Why can't I edit this symbol?"

## 3. Adversarial and control cases

Each control below is deterministic and server-side. The tests script the model, so they prove what the server does whatever a model returns.

| Category | Control | Tests |
|---|---|---|
| Change requests | Keyword gate before the model. The model's output is checked for change claims, and no mutation tool exists. | `test_ed_gates.py` (97 cases, including every approved title and text), `test_ed_orchestration_api.py` operation lists |
| Claims of having changed something | Past-tense and progressive claims are refused. Present-tense descriptions ("is approved") are allowed. | `test_ed_gates.py`, provider claim lists |
| Uncited or invented answers | An answer must name an offered approved passage or an offered live record. Unknown references are refused. Model-written refusals are replaced by a server template. | orchestration: fabricated knowledge and live refs, unnamed live record, oversized result, refusal template |
| Prompt injection | Passages and tool results are labelled as data. Instruction-shaped record text is withheld. Only named evidence is shown. | `test_live_records_reach_the_model_as_data_without_instructions_or_email`, retrieval unsafe-content tests |
| Cross-organization probing | Scope comes from the session, never the request. Tool arguments carry no scope. Every query is filtered by organization. | `test_ed_read_tools.py`, cross-tenant orchestration test |
| Pilot bypass | Route-level 404 before the body, the rate limiter and the model. Personal and credential-change sessions are excluded. | `test_ed_pilot_gate.py` |
| Credential and PII exposure | Redaction of assignment-shaped values and values containing digits. Email is never sent to the provider. The audit line never contains the question. | gates redaction cases, orchestration audit and email tests |
| Forged knowledge approval | SSH signature over a canonical receipt, in its own namespace. The signer list lives outside the repository and is owner-writable only. Digests must match. The bundle name must match its digest. | `test_ed_corpus_cli.py`, `test_ed_knowledge_runtime.py` |
| Stale knowledge | Source bytes are re-hashed on every query. The committed-bundle test fails on any source or tokenizer change. | runtime drift test, `test_ed_evaluation.py` |
| ICS attribution | Attribution is attached from stored provenance on any ICS lookup, question or cited passage, with the shipped ISO record as fallback. There are no labels without a stored import. | read-tools and orchestration ICS tests |
| Resource bounds | 3 tool calls, 4 rounds, a 45-second request deadline, per-user and per-organization rate limits, bounded retrieval, and non-blocking knowledge loads. | deadline, rate-limit and loader tests |

## 4. Independent review — findings and disposition

Two reviewers with no prior context reviewed the code read-only: one for security, one for contract correctness. They found nothing critical. All findings:

**Security**

| # | Finding | Disposition |
|---|---|---|
| M1 | Any live citation satisfied the evidence rule. Refusal text was shown verbatim. Citations survived oversized results. | **Fixed.** A live record counts only if the answer names it (`live_refs`) and its data reached the model. Refusals use a server template. |
| M2 | Records other members can write reached the model unlabelled. | **Fixed.** Tool results are labelled as data, and instruction-shaped strings are withheld. *Residual:* the filter is narrow, and a model could still paraphrase injected text into an answer that cites the record. |
| M3 | Share locks were held across provider rounds, blocking logout and revocation for up to about 90 seconds. | **Fixed.** The read-only transaction is rolled back after each tool call. |
| L1 | Two different "versions" were shown for one bundle. | **Fixed.** `knowledgeVersion` is the index digest. |
| L2 | Email, and memberships of organizations outside the pilot, went to the provider. | **Email fixed.** The membership list is the user's own data and is kept. |
| L3 | `ssh-keygen` was taken from PATH, and the signer file could be group- or world-writable. | **Fixed.** *Residual:* see §5. |
| L4 | A knowledge load blocked every Ed request. | **Fixed.** Loads never block; other requests use the last good load or no knowledge. |
| L5 | No audit trail. | **Fixed.** One log line per request: status, mode, tools, evidence counts and version. It never contains the question or answer. |

**Contract**

| # | Finding | Disposition |
|---|---|---|
| 1 | The topic gate refused plurals, British spellings and five approved claim titles. | **Fixed.** Tested against every title. |
| 2 | The change-claim check hid four approved passages. | **Fixed.** Present-tense descriptions are allowed; tested against every approved text. |
| 3 | The model was never told the tool names or arguments. | **Fixed.** The tool catalogue is in the system prompt, tested against every tool name. |
| 4 | Redaction mangled "PIN after signing in". | **Fixed.** |
| 5 | An ICS answer could omit attribution when it didn't say "ICS". | **Fixed.** |
| 6 | Many change requests reached the model ("Close project…", "Ed: delete…"). | **Fixed.** |
| 7 | Ordinary questions were refused as change requests ("Who can approve…?"). | **Fixed.** A question put to Ed that asks it to act is still refused. |
| 8 | A top-level ICS lookup exceeded the tool window. | **Fixed.** Nodes are compacted for the model. |
| 9 | The version mismatch (the same issue as L1). | **Fixed.** |
| 10 | A signer revocation needed a restart. | **Fixed.** Re-verified every 10 minutes. |
| 11 | No overall deadline, so the proxy could time out first. | **Fixed.** 45-second deadline; the UI names a 504. |
| 12 | Clear kept the old context. The project showed by name only. A no-answer response still listed sources. | **Fixed.** |
| 13 | `suggestedFollowups` is never filled, and knowledge citations carry no per-source version. | **Deferred.** `knowledgeVersion` identifies the bundle. Follow-ups are optional in the spec. |
| 14 | The drift check was skipped without `ssh-keygen`, and the fixtures were synthetic only. | **Fixed.** The real-question evaluation was added. |

The review also caught stale documents. They are corrected in the runbook and in Stage 4 plan §9, except spec §10.5's per-request trace ID, which is deferred: the audit line and per-citation references cover it for the pilot.

The full backend sweep also found one defect of this work's own. The repository's secret scan flagged three credential-shaped test fixtures, which are now assembled from fragments.

## 5. Residual risks for the activation decision

1. **The host does not separate agents from server configuration.** Agents on this server run as root. So the planned signer-file location, and the verifier code itself, can be written by the same processes that write the repository. The signature proves the steward signed; the system cannot prove the signer list it checks against is the steward's own. Options:
   - run agents as a non-root user;
   - have the deploy preflight compare the signer file with a fingerprint the steward supplies;
   - accept the risk for a limited pilot.
2. **Prompt injection through live records is reduced, not eliminated** (M2 residual).
3. **Rate limits are held in process memory.** They reset on restart and are per worker.
4. **ICS lookups return a scheme's top level or a node's children**, not a single node by code.

## 6. Not done here, and required before or at activation

- **A live-model evaluation.** Run the §2 questions, the spec §3.3 examples and the §3 adversarial prompts against the configured model in the pilot environment. Record answer correctness, citation use, refusal behaviour, latency and token cost. This environment has no provider credential, so none of that has been measured.
- **Security and retention sign-off by Chris** on this report, and a decision on §5.1.
- **The steward's signature** on `backend/symgov_backend/data/ed_knowledge/bundles/3755a0df9286/approval.json`.
- **Activation changes, each a live change needing approval:**
  - `openssh-client` in `/docker/symgov-hermes/backend.Dockerfile`;
  - the signer file at `/docker/symgov-hermes/ed/allowed_signers` (mode 0644 or stricter), mounted read-only;
  - `SYMGOV_ED_ALLOWED_SIGNERS`, `SYMGOV_ED_KNOWLEDGE_BUNDLE=3755a0df9286` and `SYMGOV_ED_PILOT_ORGANIZATION_CODES`;
  - the deploy itself.
