# SymGov BTX → SVG/DXF Integration Task for Codex

## Purpose

Integrate a production-ready **Bluebeam Revu BTX converter** into the existing SymGov SaaS application.

The converter must accept a Bluebeam `.btx` Tool Set file, or a `.zip` containing one `.btx`, extract each symbol, and produce:

- one **SVG** per symbol for browser preview and vector download;
- one **DXF** per symbol for CAD download;
- a machine-readable manifest;
- a ZIP containing all selected outputs.

> **Terminology:** the proven output from the reverse-engineering work is **SVG**, not Autodesk SVF/SVF2. The original request used “SVF”, but the converter implemented and validated here is BTX → SVG/DXF. Do not label SVG files as SVF. If the SymGov codebase genuinely contains an Autodesk SVF/SVF2 workflow, treat that as a separate feature requiring Autodesk Platform Services.

This document is intended to be supplied directly to Codex together with the reference files in this handoff package.

---

# Instructions to Codex

1. Inspect the entire SymGov repository before editing.
2. Identify:
   - frontend framework and routing;
   - backend/runtime language;
   - current API conventions;
   - authentication and tenant isolation;
   - file upload and object-storage abstractions;
   - background-job conventions, if any;
   - logging, metrics, error handling and testing conventions;
   - deployment model: VPS/container, serverless, or other.
3. Integrate the converter into the existing architecture and design system.
4. Do **not** create a separate demo application, parallel authentication system, or unrelated framework.
5. Prefer existing libraries and abstractions already used by SymGov.
6. Keep the conversion engine isolated behind a small service interface so it can be expanded as more BTX variants are discovered.
7. Add automated tests using the supplied `doors.zip` fixture.
8. Do not claim universal BTX support. Report unsupported content at file and symbol level.
9. Preserve unrelated behaviour and avoid broad refactors.
10. At completion, report:
    - files added or changed;
    - architecture chosen and why;
    - commands needed for local use;
    - tests run and their results;
    - known limitations;
    - any deployment/runtime changes required.

---

# Required user experience

Add the utility in the most appropriate existing SymGov tools or symbol-management area.

The workflow should be:

1. User opens **Bluebeam BTX Converter**.
2. User uploads:
   - a `.btx`; or
   - a `.zip` containing exactly one `.btx` for the MVP.
3. User chooses:
   - output: SVG, DXF, or both;
   - DXF units: millimetres by default, with inches and PDF points available if consistent with the application;
   - optional diagnostics, disabled by default.
4. The application validates and converts the file.
5. Results show:
   - Tool Set title;
   - number of symbols;
   - warnings;
   - SVG preview card for every successfully converted symbol;
   - symbol name and stored dimensions;
   - per-symbol SVG/DXF download buttons;
   - **Download all** ZIP.
6. Failed symbols should not necessarily invalidate successful symbols. Return partial results where safe.
7. The user must see a clear explanation when unsupported BTX content is encountered.

Use the existing SymGov visual language, components, notifications, accessibility and responsive behaviour.

---

# Confirmed BTX structure from the supplied fixture

The supplied Bluebeam Tool Set is an XML document with this shape:

```xml
<BluebeamRevuToolSet Version="1">
  <Title>HEX_ZLIB_DATA</Title>

  <ToolChestItem>
    <Resources>
      <ID>HEX_ZLIB_DATA</ID>
      <Data>HEX_ZLIB_DATA</Data>
    </Resources>

    <Name>INTERNAL_NAME</Name>
    <Type>Bluebeam.PDF.Annotations.AnnotationBRXStamp</Type>
    <Raw>HEX_ZLIB_DATA</Raw>
    <X>...</X>
    <Y>...</Y>
    <Index>...</Index>
    <Mode>drawing</Mode>
  </ToolChestItem>
</BluebeamRevuToolSet>
```

The encoded values use:

1. hexadecimal text;
2. converted to bytes;
3. zlib decompression.

For each symbol, the decoded `Raw` field is a PDF annotation dictionary. Example:

```text
<</TmpScaleX 1/TmpScaleY 1/IT/StampSnapshot
/AP<</N/BBObjPtr_MULLBWVKNYJMKQAK>>
/Rotation 0/Subj(Single Door)/Subtype/Stamp
/Rect[0 0 75.60001 90]/F 4/C[1 0 0]
/BS<</W 1/S/S/Type/Border>>>>
```

Important fields:

- `/Subj(...)` — human-readable symbol name;
- `/Rect[x0 y0 x1 y1]` — stored appearance rectangle in PDF points;
- `/AP << /N /BBObjPtr_IDENTIFIER >>` — exact normal-appearance resource pointer;
- `/Subtype/Stamp` — annotation subtype in this fixture.

Each `<Resources>` entry also contains a zlib-compressed ID and data value. The decoded resource ID must be matched to the `/AP /N` pointer.

The appearance resource is a serialized PDF Form XObject:

```text
<</Length 208
/FormType 1
/Subtype/Form
/Type/XObject
/Resources<<...>>
/Matrix[1 0 0 1 -453.6001 -234.0001]
/BBox[453.6 234 529.2 324]
/Filter/FlateDecode>>
stream
  COMPRESSED_PDF_CONTENT
endstream
```

The inner stream is Flate/zlib compressed and contains standard PDF graphics operators.

## Important production correction

Do not assume the first Form XObject resource is the symbol artwork.

The prototype originally found the first resource containing `/Subtype/Form`. A production converter must:

1. parse `/AP << /N /BBObjPtr_IDENTIFIER >>` from `Raw`;
2. remove the `BBObjPtr_` prefix when comparing with decoded resource IDs, if required;
3. build a decoded resource map;
4. resolve the exact resource identified by `/AP /N`;
5. verify that the resolved resource is a Form XObject.

A fallback to the only Form resource may be allowed only when there is exactly one candidate, and it must generate a warning such as `appearance_pointer_fallback`.

---

# Conversion pipeline

Implement the converter as stages with explicit error boundaries.

```text
Input validation
  ↓
Read .btx or safely unpack one .btx from ZIP
  ↓
Parse BTX XML envelope
  ↓
Decode title and item payloads: hex → zlib
  ↓
Parse each PDF annotation dictionary
  ↓
Resolve exact /AP /N appearance resource
  ↓
Parse PDF Form XObject dictionary and stream
  ↓
Decode supported PDF stream filters
  ↓
Parse PDF graphics operators into an intermediate representation
  ↓
Emit SVG
  ↓
Emit DXF
  ↓
Write manifest and output ZIP
```

## Stage 1: input validation

Validate content, not only extension.

MVP accepted inputs:

- direct `.btx`;
- ZIP containing exactly one non-directory `.btx`.

Reject:

- encrypted ZIPs;
- nested archives;
- ZIP entries with absolute paths or `..` traversal;
- multiple BTX files for the MVP;
- malformed XML;
- unexpected XML root;
- oversized input or expanded content.

## Stage 2: XML envelope

Expected root:

```text
BluebeamRevuToolSet
```

Read the `Version` attribute and include it in the manifest.

Version `1` is confirmed for the fixture. Unknown versions should be processed only when their structure is compatible, with an `unverified_btx_version` warning. Do not silently claim full support.

Use an XML parser configured to prohibit DTDs, external entities and network access. In Python, prefer `defusedxml`. In Node/TypeScript, use a parser with entity expansion disabled and impose element/text limits.

## Stage 3: bounded hex/zlib decoding

Do not call unbounded decompression on untrusted SaaS uploads.

For each encoded value:

- ensure trimmed text contains only hexadecimal characters;
- ensure length is even;
- enforce compressed and expanded size limits;
- use streaming/bounded zlib inflation;
- reject data that exceeds configured output limits;
- identify the failing item/resource in the structured error.

## Stage 4: annotation dictionary parsing

For the supported stamp-snapshot variant, extract:

- subject;
- rectangle;
- annotation subtype;
- appearance pointer;
- optional rotation and temporary scale values for diagnostics.

PDF strings can contain escaped parentheses and backslashes. The initial implementation may use a constrained parser, but it must not split naïvely at the first `)`.

## Stage 5: resource resolution

For each item:

1. decode every resource ID;
2. decode every resource data payload within limits;
3. index resources by decoded ID;
4. resolve `/AP /N /BBObjPtr_X` to resource ID `X`;
5. validate `/Subtype/Form` and a stream;
6. retain additional resources for possible ExtGState, nested XObject, font or image support.

## Stage 6: Form stream

Parse:

- `/BBox [x0 y0 x1 y1]`;
- `/Matrix [a b c d e f]`, default identity only when absent and valid;
- `/Filter`, initially `FlateDecode`;
- stream bytes.

Prefer `/Length` when extracting stream data if it is valid. Use delimiter-based fallback carefully. Do not let arbitrary `endstream` text inside compressed data truncate the stream.

Unsupported filters should create a structured `unsupported_pdf_filter` failure for that symbol.

## Stage 7: PDF graphics to intermediate representation

Use a deliberately constrained PDF content parser. Do not execute PostScript or external content.

Confirmed or useful MVP operators:

### Graphics state

- `q`, `Q`
- `cm`
- `w`
- `J`, `j`
- `M`
- `d`
- `G`, `g`
- `RG`, `rg`
- `K`, `k`
- `ri`, `i`
- `gs`

### Paths

- `m`
- `l`
- `c`
- `v`
- `y`
- `h`
- `re`

### Painting and clipping

- `S`, `s`
- `f`, `F`, `f*`
- `B`, `B*`
- `b`, `b*`
- `n`
- `W`, `W*`

The parser must maintain:

- current transformation matrix;
- saved graphics-state stack;
- line width, cap, join, dash;
- stroke and fill colour;
- current point and subpath start;
- painted path list.

Unknown operators must be recorded. Do not silently ignore operators that could materially change the appearance.

### Unsupported-content detection

Return explicit warnings or symbol failures for:

- `Do` nested XObjects not yet handled;
- `BI`, `ID`, `EI` inline images;
- text operators such as `BT`, `ET`, `Tf`, `Tj`, `TJ`;
- fonts;
- shadings and patterns;
- masks;
- blend modes or transparency not represented;
- non-trivial clipping that the emitter cannot preserve.

A symbol may still be converted with warnings when unsupported operators do not affect geometry, but the result must be labelled as potentially incomplete.

---

# Intermediate representation

Use an internal model independent of SVG and DXF. Adapt naming to the repository language.

```ts
type Matrix = {
  a: number;
  b: number;
  c: number;
  d: number;
  e: number;
  f: number;
};

type Segment =
  | { kind: "move"; x: number; y: number }
  | { kind: "line"; x: number; y: number }
  | {
      kind: "cubic";
      x1: number; y1: number;
      x2: number; y2: number;
      x: number; y: number;
    }
  | { kind: "close" };

type GraphicsStyle = {
  stroke: boolean;
  fill: boolean;
  evenOdd: boolean;
  strokeRgb: [number, number, number];
  fillRgb: [number, number, number];
  lineWidthPoints: number;
  lineCap: 0 | 1 | 2;
  lineJoin: 0 | 1 | 2;
  dash?: { values: number[]; phase: number };
  opacity?: number;
};

type PaintedPath = {
  segments: Segment[];
  style: GraphicsStyle;
};

type ExtractedSymbol = {
  ordinal: number;
  subject: string;
  internalName?: string;
  annotationType?: string;
  rectPoints: [number, number, number, number];
  bbox: [number, number, number, number];
  formMatrix: Matrix;
  paths: PaintedPath[];
  warnings: ConversionWarning[];
};
```

Keep raw decoded dictionaries and PDF operator streams out of normal persisted results. They may be exposed only in an administrator/developer diagnostics mode.

---

# SVG requirements

SVG is the canonical browser-preview format.

For each symbol:

- use the annotation rectangle as the visible dimensions;
- preserve PDF-point dimensions in `viewBox`;
- escape title and metadata;
- apply the Form matrix and content `cm` transforms;
- account for PDF’s upward-positive Y axis versus SVG’s downward-positive Y axis;
- preserve cubic Bézier curves;
- preserve stroke/fill colour, line width, cap, join and dash where supported;
- clip to the symbol rectangle;
- produce standalone SVG with no scripts, event handlers, external URLs or remote resources;
- include accessible `<title>` and `<desc>` elements;
- generate deterministic output.

The validated approach uses:

```xml
<g transform="translate(0 HEIGHT) scale(1 -1)">
```

with already-transformed local path coordinates.

Sanitize XML values and never copy arbitrary source XML directly into output markup.

---

# DXF requirements

MVP DXF format:

- ASCII DXF R2000, `AC1015`;
- default units: millimetres;
- optional units: inches and PDF points;
- `$INSUNITS` set correctly;
- geometry on a stable layer such as `BTX_SYMBOL`;
- PDF points converted using:
  - `1 pt = 25.4 / 72 mm`;
  - `1 pt = 1 / 72 in`.

For maximum CAD interoperability, line segments may be emitted as `LINE` or grouped `LWPOLYLINE` entities.

SVG should preserve cubic curves. DXF may tessellate cubic Béziers using an **adaptive geometric tolerance**, not a hard-coded segment count. Suggested default maximum deviation:

```text
0.10 mm
```

Make the tolerance bounded and configurable internally. Prevent pathological curves from creating unbounded entities.

The first version may omit fills, transparency and exact line-weight styling from DXF, but must state this in the manifest warnings. Closed-path outlines should still be emitted.

Do not infer real-world architectural scale from the stored BTX appearance size. The stored rectangle represents the symbol’s PDF appearance size and Bluebeam tools can be resized when placed.

---

# Recommended service contract

Expose the engine behind a small internal interface.

Language-neutral shape:

```text
convertBtx(input, options) -> ConversionResult
```

Options:

```json
{
  "formats": ["svg", "dxf"],
  "dxfUnits": "mm",
  "curveToleranceMm": 0.1,
  "includeDiagnostics": false
}
```

Result:

```json
{
  "manifest": {},
  "files": [
    {
      "relativePath": "svg/Single_Door.svg",
      "mediaType": "image/svg+xml"
    }
  ],
  "warnings": []
}
```

Do not make the conversion core responsible for HTTP, authentication, object storage or database access.

---

# Architecture decision

Codex must choose the option that best matches the existing SymGov deployment.

## Option A — native TypeScript port

Choose this when:

- SymGov is Node/TypeScript end to end;
- it deploys to serverless or an environment without a dependable Python runtime;
- avoiding a child process materially simplifies operations.

Port the reference parser carefully and maintain the same intermediate model and golden tests.

Use platform zlib support and a secure XML parser. Keep the core in a server-only package; never run untrusted BTX decompression in the browser.

## Option B — Python conversion module/service

Choose this when:

- SymGov already deploys to a VPS or container;
- Python is already available or can be included predictably;
- preserving the tested reference implementation reduces risk.

Refactor `btx_extract_reference.py` into:

- a library API;
- a thin CLI wrapper;
- structured JSON output;
- deterministic exit codes.

If Node invokes Python:

- use `spawn`/equivalent with an argument array;
- never use `shell: true`;
- use a private temporary directory;
- impose timeout, memory and output limits;
- parse a JSON result rather than scraping console text;
- capture only bounded diagnostics;
- kill the complete process tree on timeout;
- clean temporary files in `finally`.

A small internal HTTP microservice is justified only if SymGov already uses service separation. Do not introduce one solely for this converter without a strong deployment reason.

---

# Suggested HTTP API

Adapt names and response conventions to the existing SymGov API.

## Convert

```http
POST /api/tools/btx-conversions
Content-Type: multipart/form-data
Authorization: existing SymGov authentication
```

Fields:

```text
file
formats=svg,dxf
dxfUnits=mm
includeDiagnostics=false
```

Possible synchronous response for small files:

```json
{
  "id": "conversion-id",
  "status": "completed",
  "toolSet": {
    "title": "Door Symbols",
    "btxVersion": "1",
    "symbolCount": 5
  },
  "warnings": [],
  "symbols": [
    {
      "id": "stable-result-id",
      "name": "Single Door",
      "widthPoints": 75.60001,
      "heightPoints": 90,
      "widthMm": 26.6700035,
      "heightMm": 31.75,
      "status": "converted",
      "warnings": [],
      "previewUrl": "signed-or-authorized-url",
      "downloads": {
        "svg": "signed-or-authorized-url",
        "dxf": "signed-or-authorized-url"
      }
    }
  ],
  "bundleUrl": "signed-or-authorized-url",
  "expiresAt": "ISO-8601"
}
```

If SymGov already has a job system, use the existing asynchronous pattern. Do not invent polling infrastructure when a synchronous bounded request is already the normal application pattern.

## Retrieve result

```http
GET /api/tools/btx-conversions/:id
```

Authorization must enforce the current user/tenant ownership.

## Delete result

```http
DELETE /api/tools/btx-conversions/:id
```

Use existing storage deletion conventions and include automatic expiry.

---

# Manifest format

Use a versioned manifest so future BTX support can evolve.

```json
{
  "manifestVersion": "symgov-btx/1",
  "source": {
    "originalFilename": "doors.zip",
    "sha256": "hex",
    "inputType": "zip",
    "btxFilename": "Doors.btx",
    "btxVersion": "1"
  },
  "toolSet": {
    "title": "Door Symbols",
    "symbolCount": 5
  },
  "options": {
    "formats": ["svg", "dxf"],
    "dxfUnits": "mm",
    "curveToleranceMm": 0.1
  },
  "capabilities": {
    "stampSnapshot": true,
    "pdfFormXObject": true,
    "vectorPaths": true,
    "text": false,
    "rasterImages": false,
    "nestedXObjects": false,
    "complexClipping": false
  },
  "warnings": [],
  "symbols": [
    {
      "ordinal": 0,
      "name": "Single Door",
      "internalName": "KSUHAVWPZEUUAFJB",
      "annotationType": "Bluebeam.PDF.Annotations.AnnotationBRXStamp",
      "status": "converted",
      "rectPoints": [0, 0, 75.60001, 90],
      "widthPoints": 75.60001,
      "heightPoints": 90,
      "widthMm": 26.6700035,
      "heightMm": 31.75,
      "paintedPathCount": 4,
      "warnings": [],
      "files": {
        "svg": "svg/Single_Door.svg",
        "dxf": "dxf/Single_Door.dxf"
      }
    }
  ]
}
```

Use stable machine-readable warning and error codes, with separate human-readable messages.

Suggested codes:

```text
invalid_archive
archive_contains_no_btx
archive_contains_multiple_btx
archive_encrypted
input_limit_exceeded
expanded_limit_exceeded
invalid_btx_xml
unexpected_btx_root
unverified_btx_version
invalid_hex_payload
zlib_decode_failed
annotation_dictionary_invalid
appearance_pointer_missing
appearance_resource_missing
appearance_pointer_fallback
form_xobject_invalid
unsupported_pdf_filter
unsupported_pdf_operator
nested_xobject_unsupported
text_unsupported
image_unsupported
complex_clip_unsupported
dxf_fill_omitted
conversion_timeout
partial_conversion
```

---

# Storage, retention and tenancy

Follow existing SymGov patterns.

Minimum requirements:

- every conversion belongs to the authenticated tenant and user as appropriate;
- no result URL may expose another tenant’s files;
- use authorized endpoints or short-lived signed URLs;
- store source and generated files only as long as needed;
- make retention configurable;
- suggested default for temporary conversions: 24 hours or the application’s existing transient-file policy;
- delete temporary local directories after upload to object storage or response completion;
- do not log BTX payloads, decoded annotation dictionaries, SVG bodies or DXF bodies;
- log IDs, counts, timings, sizes, result status and warning codes;
- allow explicit deletion from the results screen when consistent with existing UX.

If SymGov has a permanent symbol-library import concept, make persistence an explicit user action. Do not silently retain every source file indefinitely.

---

# Security and resource limits

Use configuration/environment values rather than scattered constants. Sensible initial defaults:

| Limit | Suggested default |
|---|---:|
| Upload size | 25 MB |
| ZIP entry count | 20 |
| BTX files per ZIP | 1 |
| Total expanded ZIP size | 100 MB |
| Single decoded payload | 25 MB |
| Symbols per Tool Set | 1,000 |
| Painted segments per symbol | 250,000 |
| Total painted segments | 1,000,000 |
| Conversion wall time | 30–60 seconds |
| Output ZIP size | 200 MB |

Tune these to the actual deployment.

Security controls:

- MIME and magic/content validation where practical;
- safe ZIP entry handling;
- no archive path extraction outside a private temp directory;
- DTD/external entity disabled;
- bounded zlib inflation;
- numeric validation: reject `NaN`, infinity and absurd coordinates;
- maximum graphics-state depth;
- maximum operand count and token length;
- maximum path and curve tessellation output;
- sanitized deterministic filenames;
- duplicate symbol names receive suffixes;
- no shell command construction;
- no SVG scripts or external references;
- antivirus scanning if SymGov already provides it;
- rate limiting using existing application controls.

---

# File naming

Create filenames from the display subject:

```text
Single Door → Single_Door.svg
```

Rules:

- normalize whitespace;
- allow a conservative ASCII set for generated filenames;
- strip leading/trailing dots and separators;
- prevent reserved names;
- cap length;
- use `symbol` when empty;
- suffix duplicates deterministically:
  - `Valve.svg`
  - `Valve_2.svg`
  - `Valve_3.svg`

Do not use the source filename as a filesystem path.

---

# Error handling and partial results

A malformed envelope should fail the whole conversion.

A malformed individual Tool Chest item should produce a failed symbol record while other items continue, unless the failure indicates a global resource or security problem.

Suggested result states:

```text
pending
processing
completed
completed_with_warnings
partially_completed
failed
expired
```

Every symbol:

```text
converted
converted_with_warnings
unsupported
failed
```

Frontend messages should explain:

- which symbols converted;
- which did not;
- why;
- whether the outputs may be visually incomplete.

Avoid exposing stack traces or internal temporary paths to users.

---

# Testing requirements

Use the supplied fixture and expected outputs.

## Unit tests

Cover:

- bounded hex/zlib decoding;
- PDF string parsing with escapes;
- numeric arrays;
- matrix concatenation and point transforms;
- graphics-state save/restore;
- `m`, `l`, `c`, `v`, `y`, `h`, `re`;
- stroke/fill operators;
- colours, line width, caps, joins, dash;
- exact AP-resource matching;
- duplicate names;
- SVG XML escaping;
- DXF unit conversion;
- adaptive curve tessellation;
- unsupported operator reporting;
- limit enforcement.

## Fixture/golden test

`fixtures/doors.zip` must produce exactly five successfully converted symbols:

| Ordinal | Symbol | Width pt | Height pt |
|---:|---|---:|---:|
| 0 | Single Door | 75.60001 | 90 |
| 1 | Double Door | 133.2 | 86.39999 |
| 2 | Sliding Door | 240.6076 | 40.10118 |
| 3 | Bi-fold Door | 224.8535 | 68.74503 |
| 4 | Pocket Door | 207.6672 | 64.44839 |

Assertions:

- title is `Door Symbols`;
- all five SVGs are valid XML;
- all five SVGs have non-empty vector paths;
- all five DXFs contain `AC1015`;
- `$INSUNITS` is `4` for millimetres;
- no output references external URLs;
- filenames are deterministic;
- exact AP pointers resolve correctly;
- repeated conversion creates semantically identical output.

Prefer semantic comparison of parsed paths/manifest fields over fragile byte-for-byte output comparison. A visual regression test using the supplied expected SVGs is valuable if the existing CI supports deterministic SVG rendering.

## API tests

Cover:

- unauthenticated request;
- wrong tenant access;
- valid direct BTX;
- valid ZIP;
- no BTX in ZIP;
- multiple BTX files;
- malicious traversal entry;
- oversized archive;
- invalid XML;
- partial symbol failure;
- result download authorization;
- expiry/deletion.

## UI tests

Cover:

- drag/drop and file picker;
- validation;
- output/unit selection;
- loading state;
- preview grid;
- per-symbol download;
- bulk ZIP download;
- warning and partial-failure presentation;
- keyboard navigation and accessible labels.

---

# Acceptance criteria

The feature is complete when:

1. An authenticated user can upload the supplied `doors.zip`.
2. SymGov reports Tool Set title `Door Symbols`.
3. Five symbols are listed with the expected names.
4. Every symbol has a correct browser SVG preview.
5. Every symbol can be downloaded as SVG and DXF.
6. A bulk ZIP contains the manifest and all requested files.
7. DXF defaults to millimetres and declares units correctly.
8. Exact `/AP /N` resource matching is implemented.
9. Upload, decompression, XML, token and output limits are enforced.
10. Generated SVG is sanitized and standalone.
11. Temporary source and output data follows SymGov’s retention policy.
12. Tenant authorization protects every conversion and download.
13. Unsupported content creates structured warnings rather than being silently discarded.
14. Automated tests use the supplied fixture and pass in CI.
15. Existing application lint, type-check, test and build commands pass.
16. Documentation explains the supported subset and known limitations.

---

# Known limitations of the validated prototype

High confidence for the supplied BTX:

- Version 1 XML envelope;
- hex-encoded zlib payloads;
- PDF annotation dictionary in `Raw`;
- stamp-snapshot Form XObject appearance;
- FlateDecode content stream;
- vector path geometry and transforms;
- line styling and colour;
- SVG and line-based DXF output.

Not yet generalised:

- non-stamp annotation classes;
- grouped markups represented differently;
- nested Form XObjects;
- raster images;
- text and embedded/subset fonts;
- transparency and blend modes;
- masks, patterns and shadings;
- complex clipping;
- measurement/calibration metadata;
- additional stream filters;
- newer or materially different BTX root versions.

The production feature must expose this as a capability boundary, not hide it.

---

# Suggested implementation sequence

## Phase 1 — engine and tests

- place the reference fixture under the existing test-fixture convention;
- implement/refactor conversion engine;
- add exact appearance pointer resolution;
- add bounded decoding and structured warnings;
- generate SVG, DXF and manifest;
- pass unit and golden tests.

## Phase 2 — server integration

- add authenticated conversion endpoint;
- connect existing upload/storage abstractions;
- add limits, timeout and cleanup;
- add result download and bundle generation;
- enforce tenant ownership.

## Phase 3 — UI

- add converter page/component;
- add upload controls and options;
- display previews and warnings;
- add individual and bulk downloads;
- add accessibility and responsive handling.

## Phase 4 — hardening

- security tests;
- observability;
- retention cleanup;
- deployment verification;
- documentation.

---

# Deliverables expected from Codex

- production conversion engine;
- API integration;
- SymGov UI integration;
- manifest types/schema;
- automated unit, fixture, API and UI tests appropriate to the repository;
- developer documentation;
- deployment changes, only when required;
- a concise implementation report.

Do not merely copy the prototype CLI into the repository without adapting it to SaaS security, tenancy, storage and error-handling requirements.

---

# Reference package contents

```text
CODEX_TASK.md
reference/
  btx_extract_reference.py
  BTX_FORMAT_ANALYSIS.md
fixtures/
  doors.zip
  Doors.btx
  expected_manifest.json
golden/
  svg/
  dxf/
  preview.png
```

The reference Python code is a reverse-engineering prototype, not automatically production-safe. Preserve its geometry logic while applying the requirements in this document.
