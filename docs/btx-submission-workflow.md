# Bluebeam BTX submission workflow

## Scope and source of truth

This is the operating reference for Bluebeam Revu `.btx` Tool Set submissions. The supported format and fixture-derived evidence are in:

- `integrations/btx/SymGov_BTX_Integration_Handoff/`
- `integrations/btx/SymGov_BTX_Integration_Handoff/reference/BTX_FORMAT_ANALYSIS.md`
- fixture: `integrations/btx/SymGov_BTX_Integration_Handoff/fixtures/Doors.btx`

The implementation supports the verified Bluebeam Tool Set XML Version 1 stamp-snapshot structure. It does not claim universal support for arbitrary BTX versions, annotation types, images, text, nested Form XObjects, transparency, patterns, masks, or embedded fonts.

Terminology: output is SVG, DXF, and PNG. Do not call SVG output “SVF”; Autodesk SVF/SVF2 is unrelated.

## End-to-end path

1. The Submissions UI accepts `.btx` alongside SVG, PNG/JPEG, JSON, DXF, and ZIP.
2. `ExternalSubmissionService` stores the original BTX unchanged as an attachment and object-storage source asset, then creates the Scott intake item with `declared_format: btx`.
3. Scott verifies the submitted file and contributor declaration, marks it as `btx_library_expansion_candidate`, and routes accepted intake to Vlad and Tracy.
4. The tracked queue bridge creates a Vlad job with `expected_checks: [integrity, btx_library_expansion]` and a Tracy job whose provenance scope covers both the original library and extracted symbols.
5. Vlad invokes `symgov_backend.services.btx_converter.convert_btx` and writes an isolated working directory under `<vlad-runtime>/btx_derivatives/<queue-id>/`.
6. For each successfully converted BTX entry, Vlad produces:
   - SVG: browser-ready vector/download asset;
   - DXF: CAD download asset using millimetres by default;
   - PNG: stable browser preview;
   - manifest metadata retaining source SHA-256, ordinal, subject, dimensions, warnings, and generated asset checksums.
7. When run with `--persist-db` and a storage environment, Vlad creates attachment rows and uploads every generated asset before persisting the validation report. The report's `derivative_manifest` uses the PNG asset for each review child and retains SVG/DXF/PNG assets for downloads.
8. Vlad deliberately escalates a successful BTX library into the existing `raster_split_review` child-review stage, creating a human-review case with one materialized `review_split_item` per extracted symbol. A conversion is not an automatic publication decision.
9. The Workspace/Reviews preview route resolves each child PNG by its stored object key. SVG and DXF remain associated derived assets. Tracy and then Libby/Daisy continue normal provenance, classification, and review governance.

## Converter boundaries and safety limits

The server-side converter is `backend/symgov_backend/services/btx_converter.py`. It must remain server-side: untrusted BTX parsing and decompression never run in the browser.

Current limits:

- direct input or BTX ZIP member: 50 MiB;
- expanded compressed payload: 16 MiB;
- symbols: 500;
- path segments per symbol: 10,000;
- ZIP input: exactly one unencrypted, safe `.btx` member;
- XML DTD/entity declarations are rejected.

For the proven structure, the converter follows this chain:

```text
BTX XML -> hex decode -> zlib inflate -> PDF annotation dictionary
      -> exact /AP /N appearance-resource match -> Form XObject
      -> FlateDecode stream -> PDF paths/transforms -> SVG, DXF, PNG
```

The `/AP /N` pointer is authoritative; do not substitute the first Form resource. Per-symbol conversion errors are recorded in the manifest so successful symbols can still be reviewed.

## Review-visible asset contract

Use the canonical `visual_assets` keys:

```json
{
  "preview": {"object_key": "...png", "format": "png"},
  "source_assets": [{"object_key": "...Doors.btx", "format": "btx"}],
  "derivatives": [
    {"object_key": "...svg", "format": "svg"},
    {"object_key": "...dxf", "format": "dxf"},
    {"object_key": "...png", "format": "png"}
  ]
}
```

`derived_assets` is not a recognized asset-manifest key and must not be used. Review children must expose a persisted PNG `attachment_object_key`; a local filesystem path alone cannot be displayed by the browser. Persist generated assets before writing the durable validation report so the report, review API, and storage all refer to the same object keys.

## Operations and troubleshooting

Focused regression coverage:

```bash
cd /data/symgov
PYTHONPATH=backend pytest -q tests/test_btx_integration.py
```

A production-style Vlad job requires persistence and storage configuration:

```bash
PYTHONPATH=backend python scripts/run_vlad_validation.py \
  --queue-item <vlad-queue-item.json> \
  --runtime-root /data/.openclaw/workspaces/vlad/runtime \
  --persist-db \
  --db-env-file /data/.openclaw/workspace/symgov/.env.backend.database \
  --storage-env-file /data/.openclaw/workspace/symgov/.env.backend.storage
```

When symbols are missing or blank in review, trace these boundaries in order:

1. Vlad validation report: `btx_library.successful_symbol_count` and `derivative_manifest.children` should both reflect successful extracted symbols.
2. Each child must have a PNG `attachment_object_key`, and its asset must show an upload result in the persisted report.
3. Confirm object storage contains that key and that `GET /api/v1/workspace/review-cases/<case>/children/preview?object_key=<key>` returns the PNG.
4. Confirm the browser console/network requests the child preview URL successfully.
5. If a symbol is converted but unsupported features were omitted, inspect symbol and manifest warnings rather than silently treating the output as faithful.

## Change checklist

Any BTX change must preserve or extend tests for:

- direct `Doors.btx` and ZIP-with-one-BTX conversion;
- exact appearance-pointer/resource matching;
- unsafe XML and ZIP rejection;
- Scott/Vlad/Tracy queue contracts;
- per-symbol SVG, DXF, and PNG output;
- persisted generated assets and PNG review-child preview keys;
- review escalation and materialized child visibility.

Use a multi-version/tool-type BTX corpus before extending format claims beyond the supplied stamp-snapshot fixture.
