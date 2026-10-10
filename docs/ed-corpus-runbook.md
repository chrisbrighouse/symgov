# Ed corpus draft build and verification runbook

## Status and authority boundary

This procedure prepares and verifies an **offline draft**. It does not approve, publish, promote, activate, deploy, or serve an Ed corpus. The CLI has `build`, `verify`, `prepare-receipt` and `verify-approval`, all of which write only new files or nothing. There is no `approve` command, rollback mutator, database access, provider call, network fetch, scheduler, runtime pointer update, or production write.

A successful build proves deterministic construction and internal/source-byte consistency. It does not prove the claims are factually correct, that their rights are sufficient, that a steward approved them, or that they are safe to activate. The synthetic Stage 4 query fixture proves retrieval mechanics only and must never be represented as approved knowledge.

If no separately approved, immutable, compatible snapshot exists, Ed must provide no corpus-derived answer. The existing no-answer/documentary-evidence-unavailable path remains the correct behavior. Do not activate the draft produced here.

## Preconditions

Use a clean, pinned repository checkout containing only sources already reviewed for exact path, byte hash, locator, topic, rights, and proposed claim text. Work without production database, provider, crawler, or external network access. Do not read profile files, credentials, `.env*`, `.claude/**`, customer material, logs, transcripts, backups, or live mounts.

The operator must have:

- a 40-character lowercase Git commit ID for `--source-commit`;
- an explicit UTC timestamp such as `2026-09-29T12:00:00Z` for `--extracted-at`;
- a strict inventory JSON file containing top-level `schemaVersion`, `inventory`, and `sourceApprovals` only;
- a strict claims JSON file containing top-level `schemaVersion`, `manifest`, and `claimMetadata` only; and
- a new, non-existing output path whose existing parent is outside the repository and outside live, current, approved, production, or release locations.

Both input files are bounded to 1,000,000 bytes. Unknown fields and unsupported rights dispositions fail closed. Source approval is represented only as reviewed build input; it is not a steward release receipt.

## Input contract

`inventory` uses the existing strict `SourceInventory` schema. Every source must be repository-relative, allowlisted, non-symlinked, UTF-8, no more than 262,144 bytes (decision 7.4; the cited range is separately capped at 16,384 bytes), and pinned as `sha256:<64 lowercase hex>`. Each `sourceApprovals` entry names its source ID, one supported rights disposition (`repository_owned` or `approved_open_data`), and the exact approved line range and/or symbol.

A source located only by a heading or symbol is resolved at build time to the exact line range of the unit it names; a name that matches more than one unit is refused. That resolved range is bounded by the same 16,384-byte unit limit as an explicit range, and it is what every chunk carries. Markdown headings are named by slug (`guide` for `# Guide`), so the slug need not appear verbatim in the file.

The credential check scans the whole source file, not only the approved range. It looks for credential material, not code that names a credential: a credential-named key assigned a quoted literal, a bare letters-and-digits value of 20 or more characters, a private-key header, or a known provider token prefix. `token = request.cookies.get(...)` passes; `password = "…"` does not.

`manifest` uses the existing strict `KnowledgeManifest` schema and remains draft-only. `claimMetadata` binds every claim ID to one fact key and the product scope. Claims are limited to 100 records and 1,000 characters each; reviewed units are limited to 16,384 bytes. Duplicate identities, broken or cyclic supersession, conflicting active fact keys, mismatched source identity, unsupported visibility, missing evidence, secret-like content, source drift, and unapproved rights fail closed. Diagnostics are bounded to 20 entries of 200 characters and do not echo source or claim content.

Do not infer factual approval from repository presence. Do not insert real tenant/project/user facts. No initial organization-scoped or internal claims are supported.

## Build a draft

Run from the pinned checkout, replacing the placeholders with reviewed offline paths:

    PYTHONPATH=backend uv run --isolated \
      --with-requirements backend/requirements.txt \
      python -m symgov_backend.ed_corpus_cli build \
      --repository /path/to/clean-checkout \
      --inventory /path/to/reviewed-inventory.json \
      --claims /path/to/reviewed-claims.json \
      --output /external/staging/new-draft-directory \
      --source-commit 0123456789abcdef0123456789abcdef01234567 \
      --extracted-at 2026-09-29T12:00:00Z

The builder constructs a private sibling temporary directory, writes each file with exclusive creation, flushes file and directory metadata, verifies the complete staged bundle, and then publishes the directory with Linux atomic no-replace semantics. It fails closed if that primitive is unavailable or if another process creates the target during the build. A failed build removes only its private partial directory. It never edits or removes another bundle or a runtime pointer.

The output contains exactly:

- `manifest.json` — draft status, pinned source identities, coverage/exclusions, per-file digests, and the deterministic index digest;
- `chunks.jsonl` — one canonical curated draft claim per line, carrying its resolved line range and its `supersededBy` links, and **no approval state**;
- `postings.json` — canonical bounded lexical postings derived exactly from the chunks; and
- `build-report.json` — incomplete topics, empty validation errors, manifest digest, index digest, and the explicit `not_run_synthetic_fixture_only` query-fixture status.

The index digest is SHA-256 over domain-separated file names, lengths, and exact `chunks.jsonl` plus `postings.json` bytes. It is the value a steward approves. Approval belongs to the whole bundle, never to a chunk: retrieval serves nothing unless it is given an approved index digest equal to the digest of the exact bytes in front of it, and a chunk that carries its own `approvalState` is malformed and refused by both retrieval and `verify`. The digests are unkeyed, so they prove integrity, not authenticity. Anyone who can rewrite a bundle can recompute them, and only the steward's signed receipt (below) can establish that a digest was approved. The manifest digest is SHA-256 over exact canonical `manifest.json` bytes and is recorded separately in the report, avoiding a self-referential manifest hash. With identical reviewed inputs, source bytes, source commit, and extracted timestamp, all four files are byte-identical.

## Verify a draft

Verification is read-only:

    PYTHONPATH=backend uv run --isolated \
      --with-requirements backend/requirements.txt \
      python -m symgov_backend.ed_corpus_cli verify \
      --bundle /external/staging/new-draft-directory \
      --repository /path/to/matching-release-tree

Verification requires exactly the four expected regular files, no symlinks or extras, canonical UTF-8 bytes, bounded sizes, a draft-only manifest/report contract, exact chunks-to-postings reconstruction, matching chunk/postings/index/manifest digests, consistent source identity, and current repository source bytes and locators. Missing, changed, oversized, non-UTF-8, out-of-range, symbol-missing, path-escaping, or symlinked sources fail closed.

Run `verify` against the exact source tree intended to accompany any later immutable release. Verification against a mutable developer checkout is not release evidence.

`--source-commit` is recorded provenance only. Neither command checks it against the repository's `HEAD` or checks that the checkout is clean, because a release tree need not be a Git checkout. The per-file SHA-256 pins are the binding check. The operator is responsible for building from the commit they name.

## Offline synthetic fixture check

The repository fixture at `backend/symgov_backend/data/ed_knowledge/query_fixtures.stage4.json` is deliberately marked `draft` and `synthetic retrieval mechanics only; not factual approval`. It contains one supported-mechanics query and one no-answer query for each of the ten topic enum values. Run the focused test gate to exercise it without live ingestion:

    PYTHONPATH=backend uv run --isolated \
      --with-requirements backend/requirements.txt \
      --with-requirements backend/requirements-test.txt \
      python -m pytest \
      tests/test_ed_knowledge.py \
      tests/test_ed_knowledge_sources.py \
      tests/test_ed_corpus.py \
      tests/test_ed_retrieval.py \
      tests/test_ed_corpus_cli.py \
      -q --tb=short

This check does not turn draft chunks into published claims and does not authorize runtime use.

## Steward approval by signed receipt (decision 7.2)

The Ed Knowledge Steward approves a bundle by signing a small receipt with an SSH key that is kept on the steward's own machine and never on this server. Agents on the server push to GitHub with the owner's credentials, so nothing an agent can write, a commit included, proves that a person approved. A signature from a key the agents do not hold does.

1. On the server, write the unsigned receipt for a verified bundle. This approves nothing:

       PYTHONPATH=backend uv run --isolated \
         --with-requirements backend/requirements.txt \
         python -m symgov_backend.ed_corpus_cli prepare-receipt \
         --bundle /external/staging/new-draft-directory \
         --repository /path/to/matching-release-tree \
         --steward "Ed Knowledge Steward" \
         --approved-at 2026-09-29T19:30:00Z \
         --output /external/receipts/approval.json

   The receipt is one line of canonical JSON: `schemaVersion`, `kind`, `decision` (`approved`), `indexDigest`, `manifestDigest`, `sourceCommit`, `steward` (a label only) and `approvedAt`.

2. On the steward's machine, read the receipt, check its digests against the recorded review, then sign it:

       ssh-keygen -Y sign -f ~/.ssh/id_ed25519 -n symgov-ed-knowledge approval.json

   This writes `approval.json.sig`. The namespace `symgov-ed-knowledge` is required. A signature made for any other purpose does not verify. Copy only the `.sig` file back; the private key never leaves that machine.

3. Anywhere, verify. This is read-only:

       PYTHONPATH=backend uv run --isolated \
         --with-requirements backend/requirements.txt \
         python -m symgov_backend.ed_corpus_cli verify-approval \
         --bundle /external/staging/new-draft-directory \
         --repository /path/to/matching-release-tree \
         --receipt /external/receipts/approval.json \
         --signature /external/receipts/approval.json.sig \
         --allowed-signers /path/outside/the/repository/allowed_signers

   The command first runs the full bundle `verify`. It then requires the receipt to be canonical, and the signature to come from a principal in the allowed-signers file under the Ed namespace. Finally the receipt must name exactly this bundle's index digest, manifest digest and source commit. It refuses an allowed-signers file inside the repository or the bundle. `ssh-keygen` output is never echoed.

The allowed-signers file is the real control. Each line is `principal namespaces="symgov-ed-knowledge" <public key>`. Keep it in server configuration outside the repository. Changing it adds or removes a steward, so treat any change as a live configuration change that needs the owner's approval; delegating the steward role is one such change. The CLI can check a signature against the file it is given; it cannot prove which file that should be. Nothing here loads a bundle into the running application. Serving is Slice D and remains separately gated.

## Serving an approved bundle (Slice D)

The Ed API serves product knowledge only when two settings name a bundle and a signer list:

- `SYMGOV_ED_KNOWLEDGE_BUNDLE` holds the bundle's directory name under `backend/symgov_backend/data/ed_knowledge/bundles/`, which is the first 12 hex of its index digest (for example `3755a0df9286`).
- `SYMGOV_ED_ALLOWED_SIGNERS` holds the path to the steward signer list, mounted read-only from server configuration.
- `SYMGOV_ED_PILOT_ORGANIZATION_CODES` lists the organization codes that may use Ed. `*` admits every organization, including ones created later, and personal sessions are never admitted. Empty means Ed is off for everyone.

On first use the API runs the full `verify-approval` check against its own release tree, so it needs `ssh-keygen` at `/usr/bin` in the image. The signer file must not be writable by group or others. A bundle that passes is verified again every ten minutes, so removing a steward key takes effect within that time. A failed load is logged by error code and retried after a minute. Ed serves no product knowledge in the meantime. A load never blocks other requests: while one runs, they use the previous good load, or no knowledge at all. Every query re-hashes the source file behind each scored passage. A passage whose source has drifted is left out and the rest are still served, so an edit to a cited file withholds only the claims that cite it until the steward re-signs a rebuilt bundle. The load check tolerates the same drift, logging `Ed knowledge bundle <name> loaded with stale sources: <paths>`; any other bundle fault, and the build and sign-off paths, still refuse. The `ed_chat` log line carries `reason=`, `retrieval=` and `dropped=` so a failed answer can be traced to its cause. Questions are matched on exact words plus plural, British-spelling and admin/administrator variants, and rarer words weigh more than words found in most claims. After approval, Ed shows an answer only if it names an approved passage or a live record that the server offered it. A reference the server did not offer is refused. Either setting empty means no product knowledge; Ed then answers only from live records that it names.

## Manual review checklist — not executed by this runbook

The following are required human/release controls, but this runbook does not perform them:

1. Review every exact source path, hash, range/symbol, topic, claim title/text, fact key, visibility, scope, supersession link, rights disposition, exclusion, incomplete topic, posting, and query-fixture outcome.
2. Independently recompute and record the source commit, manifest digest, and index digest.
3. Have the Ed Knowledge Steward sign the receipt for the exact reviewed hashes, and confirm it with `verify-approval` (see above).
4. Package the unchanged reviewed bytes as an immutable release artifact only through a separately authorized release procedure.
5. Prove runtime mount, approval receipt validation, application compatibility, no-answer fallback, activation control, restart, and smoke-test procedures before any activation.

A CLI string such as `--steward Chris` does not establish identity or authorization. The `--steward` value on `prepare-receipt` is only a label in the receipt; identity comes from the signing key.

## Compatible rollback — procedure definition only, not executed

Never overwrite an active bundle. A later, separately authorized rollback may reselect only a previously steward-approved, immutable bundle whose exact manifest/index hashes verify and whose source snapshot is compatible with the application release. Record the attempted build/release ID, source commit and hashes, steward decision, previous/selected pointer, compatibility evidence, and reason without recording questions, credentials, or corpus text.

If the previous bundle is missing, altered, unapproved, unverifiable, or incompatible with the application/source release, disable corpus retrieval and return no corpus answer. Do not silently serve stale bytes, rebuild them in place, fall back to the draft/example manifest, or use unversioned web content. No rollback or pointer operation has been implemented or executed in Slice C.

## Rights, ICS, and exclusions

ISO/IEC standards documents and their full text are excluded. Raw `backend/symgov_backend/data/ICS.csv`, archived import bytes, external standards pages, and semantic inferences from crosswalks are not corpus inputs. ISO Open Data code and label metadata may be used under decision 7.3 (2026-09-29), provided every answer that shows it carries the ISO attribution, the ODC-By licence and the codes-only clarification. The Ed API attaches these server-side. A corpus claim that conveys ICS labels must still go through claim review like any other. The browse taxonomy is not the standards text and does not establish semantic assignment.

Also excluded are credentials, customer/uploaded documents, external sources or crawls, raw security configuration, hidden prompts, private account/tenant records, unrestricted SQL, generated outputs not independently reviewed, and any material with unclear rights. Build success cannot waive these exclusions.
