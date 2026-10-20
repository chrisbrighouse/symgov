# QElectroTech elements import (phase 1): runbook

`backend/symgov_backend/qet_ingest.py` imports the curated phase 1 package from
`/docker/openclaw-hz0t/data/symgov-qet-harvest-20261020/package`: 966 elements of
github.com/qelectrotech/qelectrotech-elements at commit `73cbffb`. That is every element of the EN 60617 set (912) plus
the 54 all-pole and single-pole elements whose own notes cite an EN 60617 clause. The plan is in
`services/qet_ingestion.py`; the .elmt → SVG converter is `services/qet_elmt_svg.py`; the acceptance checks are in
`qet_verify.py`.

## Decisions this import carries (product owner, 2026-10-20)

- Ranked sets only. The home-installation folders (394 elements) are dropped; the MIT-licensed
  `generic-hybrid-inverter.elmt` is held. Phase 1 is the EN 60617 set plus the clause-citing pole elements. The rest of
  the pole sets, pneumatic and hydraulic would be held at the gate (no standard link) and are left for later.
- Credit: each element's own `Author:` line; with none, a bare author handle if one is present; else
  "QElectroTech contributors". No phase 1 element has a bare handle.
- The "Form 1 / Forme 1" captions QElectroTech draws on 59 EN 60617 variants are removed in conversion. They name the
  variant on QET's sheet and are not part of the symbol. The removed text is recorded in the conversion step.
- The standard is **EN 60617**, issued by CENELEC, version "edition not stated". Each symbol has one verified
  `vendor_implementation` link at its clause (`clause_reference`, e.g. `07-21-01`).
- Every worksheet row was confirmed ("confirm all"). Categories are by EN 60617 part, with keyword rules for motors and
  lamps. EN 60617 elements with the same English name share one concept, and a pole element joins the concept of the
  EN 60617 element for its clause. A repeated name gets its clause appended.
- A concept whose name exactly matches an existing one is reused, and the dry run lists every reuse. This departs from
  the Commons rule, where such a key was a problem: the Catalog's electrical coverage is thin, and a concept with the
  same name is the same thing.

## QElectroTech's conditions (Laurent Trinques, 2026-10-19), and where each is met

| Condition | Where |
|---|---|
| Their attribution wording | `ATTRIBUTION_TEMPLATE`, stored on the rights record as `attribution_template`; the Details view fills `{creator}` from `payload.attribution.creator` (`published_symbol_details._rights`) |
| A clearly visible qelectrotech.org link on each symbol page | the record's `source_url`; Details shows it as a "Source" link in "Source and attribution" |
| Credit each element's own author | `payload.attribution.creator`, per element; a changed author makes a new revision |
| Never present QElectroTech as a partner or endorser | the wording; `verify`'s `no-endorsement` check |
| Ed may use names and descriptions, never the drawings | Ed only ever reads text; nothing here gives it the SVGs |

The machine-learning clause was still in `ELEMENTS.LICENSE` at `73cbffb`. Laurent's email is the written basis until
it is removed; the rights record says so.

## Rehearsal

Run 2026-10-20 on a disposable `postgres:16` restored from
`/data/symgov-backups/symgov-pre-8e82bbe-20261020T161421Z.dump` (schema `20261021_0075`, 960 symbols, 536 concepts),
with a local-folder bucket and a stand-in rights approver. The outputs are in the package's `../rehearsal/`.

- The dry run was not blocked: 966 to create; 839 concepts to create and none reused; no near misses; EN 60617 to
  register.
- `apply` took 35 s. A second `apply` left all 966 unchanged and created nothing.
- `upload`: 966 SVGs, 0.75 MB.
- `gate` refused all 966 for `rights_undecided` and nothing else, and `publish` published nothing.
- After the stand-in approval, `gate` permitted 966, all at T5. `publish` published 966 (S-939 to S-1904 in the
  rehearsal) and computed 966 facet rows: Electrical 966, with the categories exactly as curated.
- `verify`: 13 of 13 pass.

## Live run (production, by the product owner)

Prerequisite: a release containing this importer and the Details attribution change is deployed
(`scripts/deploy-release.sh`, which takes its own backup).

```
REL=<the deployed release directory name, e.g. stage11-xxxxxxx>
ACTOR=4d41c6b3-0cff-4cb2-a200-b616db6fa68b   # the product owner's user id
qig() { docker exec -w /data/symgov-releases/$REL/backend symgov-hermes-api sh -c \
  'SYMGOV_DATABASE_URL=$(python -c "from symgov_backend.db import get_database_url, normalize_database_url as n; print(n(get_database_url()))") exec python -m symgov_backend.qet_ingest "$@"' sh "$@"; }
```

1. Copy the package into the container:
   ```
   docker cp /docker/openclaw-hz0t/data/symgov-qet-harvest-20261020/package symgov-hermes-api:/tmp/qet-elements
   ```
2. Dry run (reads only; exit 2 if blocked). Expect 966 to create, no blocking reasons, and the concepts as rehearsed:
   ```
   qig apply --package /tmp/qet-elements --dry-run --output /tmp/qet-dry.json
   ```
3. Apply, then re-run the dry run (expect `unchanged: 966`):
   ```
   qig apply --package /tmp/qet-elements --actor-id $ACTOR --output /tmp/qet-apply.json
   ```
4. Upload the SVGs:
   ```
   qig upload --package /tmp/qet-elements --output /tmp/qet-upload.json
   ```
5. Gate. It **must refuse** all 966 with `rights_undecided`:
   ```
   qig gate --package /tmp/qet-elements --actor-id $ACTOR --output /tmp/qet-gate-before.json
   ```
6. **Approve the rights record** in Symgov: `#/semantic-review` → "Rights records" → `QELECTROTECH-ELEMENTS-20261020`.
   Nothing automates this.
7. Gate again (expect 966 `permitted`), then publish, dry run first:
   ```
   qig gate --package /tmp/qet-elements --actor-id $ACTOR --output /tmp/qet-gate.json
   qig publish --package /tmp/qet-elements --actor-id $ACTOR --dry-run
   qig publish --package /tmp/qet-elements --actor-id $ACTOR --output /tmp/qet-publish.json
   ```
8. Verify (expect every check to pass):
   ```
   qig verify --package /tmp/qet-elements --output /tmp/qet-verify.json
   ```
9. In the browser, after a hard refresh: open any QElectroTech symbol's Details and check "Source and attribution"
   names its author in QElectroTech's wording and shows the qelectrotech.org link.

`publish` is all or nothing: any gate refusal publishes none. No `--permitted-only` is needed, because every phase 1
symbol carries a verified standard link.

## Re-running

The key is the element's QElectroTech UUID (`qet-<uuid>`). Re-running `apply` changes nothing. A changed SVG, name,
concept or author makes a new revision (`r2`, ...), which `publish` publishes, retiring the previous page. Concepts
this import created are recognised on a re-run by their notes and reused. The converter is versioned
(`CONVERTER_VERSION`): a change to its output changes every SVG digest, and so makes a new revision of every symbol.
