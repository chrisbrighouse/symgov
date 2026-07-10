# Bluebeam Revu BTX reverse engineering: `Doors.btx`

## Result

The five symbols in this tool set were extracted as native vector geometry. No rasterisation, OCR, or image tracing was needed.

| Symbol | Source rectangle (PDF pt) | Approx. size in mm* | SVG | DXF |
|---|---:|---:|---|---|
| Single Door | 75.6000 × 90.0000 | 26.670 × 31.750 | Yes | Yes |
| Double Door | 133.2000 × 86.4000 | 46.990 × 30.480 | Yes | Yes |
| Sliding Door | 240.6076 × 40.1012 | 84.881 × 14.147 | Yes | Yes |
| Bi-fold Door | 224.8535 × 68.7450 | 79.323 × 24.252 | Yes | Yes |
| Pocket Door | 207.6672 × 64.4484 | 73.260 × 22.736 | Yes | Yes |

\* The millimetre figures use the PDF conversion `1 point = 25.4 / 72 mm`. These are the stored appearance dimensions, not necessarily a real-world architectural door scale. Bluebeam tools can be resized when placed.

I interpreted “SVF” in the request as **SVG**. Autodesk SVF/SVF2 and JTAG Serial Vector Format are unrelated formats.

## What the BTX actually contains

This sample is not an opaque binary container. It is an XML document:

```xml
<BluebeamRevuToolSet Version="1">
  <Title>...</Title>
  <ToolChestItem>
    <Resources>...</Resources>
    <Name>...</Name>
    <Type>Bluebeam.PDF.Annotations.AnnotationBRXStamp</Type>
    <Raw>...</Raw>
    <X>...</X>
    <Y>...</Y>
    <Index>...</Index>
    <Mode>drawing</Mode>
  </ToolChestItem>
</BluebeamRevuToolSet>
```

The long strings beginning `789c` are:

1. hexadecimal text;
2. decoded to bytes;
3. inflated with zlib.

This applies to `Title`, every resource `ID`, every resource `Data`, and each item’s `Raw` value.

The decoded title is simply:

```text
Door Symbols
```

## The important internal chain

For every item in this sample, the useful path is:

```text
ToolChestItem
  Raw
    zlib -> PDF annotation dictionary
      /Subj(...)             human-readable symbol name
      /Rect[...]             stored appearance rectangle
      /AP << /N /BBObjPtr_X  appearance-resource pointer
  Resources
    ID zlib -> X             matches the /AP pointer
    Data zlib -> PDF Form XObject
      /BBox [...]
      /Matrix [...]
      /Filter /FlateDecode
      stream ... endstream
        second zlib layer -> PDF graphics operators
```

So the geometry is effectively a small self-contained PDF appearance stream embedded in the BTX.

## Worked example: Single Door

The first item’s decoded `Raw` value is:

```text
<</TmpScaleX 1/TmpScaleY 1/IT/StampSnapshot
/AP<</N/BBObjPtr_MULLBWVKNYJMKQAK>>
/Rotation 0/Subj(Single Door)/Subtype/Stamp
/Rect[0 0 75.60001 90]/F 4/C[1 0 0]
/BS<</W 1/S/S/Type/Border>>>>
```

The appearance pointer `MULLBWVKNYJMKQAK` matches the decoded ID of the first resource. Inflating that resource’s `Data` produces a PDF Form XObject:

```text
<</Length 208/FormType 1/Subtype/Form/Type/XObject
/Resources<</ProcSet[/PDF/Text]/ExtGState/BBObjPtr_ZKQBZSEJOBNQGYZC>>
/Matrix[1 0 0 1 -453.6001 -234.0001]
/BBox[453.6 234 529.2 324]
/Filter/FlateDecode>>
stream
  ...compressed PDF drawing commands...
endstream
```

Inflating that inner stream produces ordinary PDF path instructions. A shortened excerpt is:

```text
q
0.12 0 0 0.12 0 0 cm
/R8 gs
6 w
1 J
1 j
0 0 0 RG
3873 2586 m
3873 2133 l
S
...
Q
```

The key operators are:

- `q` / `Q`: save and restore graphics state;
- `cm`: concatenate a transformation matrix;
- `w`: line width;
- `J` / `j`: line cap and join;
- `RG`: stroke colour;
- `m`: move to;
- `l`: line to;
- `re`: rectangle;
- `S`: stroke the current path;
- `W*` and `n`: establish the clipping path without painting it.

The door swing is not stored as a semantic “arc”. It is a sequence of short line segments approximating a quarter-circle. The Double Door swing is encoded similarly.

## Coordinate conversion

There are two relevant transforms in the sample:

1. the Form XObject `/Matrix`, which translates the stored BBox to a local origin;
2. the content stream’s `0.12 0 0 0.12 0 0 cm`, which scales the large integer-like drawing coordinates.

For example, one Single Door point is `(3873, 2586)`. Applying the stream scale and then the Form matrix gives approximately:

```text
x = 3873 × 0.12 - 453.6001 = 11.1599 pt
y = 2586 × 0.12 - 234.0001 = 76.3199 pt
```

The Form BBox is 75.6 × 90 points, matching the annotation `/Rect` almost exactly.

SVG uses a downward-positive Y axis, whereas PDF and DXF conventionally use upward-positive Y. The SVG files therefore retain the local PDF coordinates inside a Y-flipped group. The DXF files retain the upward-positive coordinates directly.

The stored line width is `6`, with a scale of `0.12`, so the visible line width is `0.72 pt`, approximately `0.254 mm`.

## Other fields and what can safely be said

- `Name` is a 16-character internal identifier. It looks generated, but its algorithm and lifecycle cannot be inferred from this one file.
- `Type` is `Bluebeam.PDF.Annotations.AnnotationBRXStamp` for all five items. This is why the appearance is represented as a stamp snapshot/Form XObject.
- `Mode` is `drawing` for every item.
- `X` and `Y` are zero or tiny floating-point residues. Their role is not needed to reproduce the appearance.
- `Index` is not a unique sequence in this sample (`1, 0, 2, 0, 1`), so it should not be treated as the item order without more evidence. The extractor preserves XML order.
- The annotation dictionary contains `/C[1 0 0]`, but the actual appearance stream sets `0 0 0 RG`, so the visible artwork is black. Appearance-stream styling should take precedence when converting.
- The second and third resources form an ExtGState pointer chain. In this sample it resolves to `<</Type/ExtGState/OPM 1>>`; it does not alter the extracted geometry.

## Prototype extractor

`btx_extract.py` is a standard-library Python prototype. It accepts either a `.btx` or a ZIP containing exactly one `.btx`:

```bash
python btx_extract.py doors.zip -o extracted --dxf-units mm
```

Supported DXF unit choices are `pt`, `mm`, and `in`.

The output includes:

- one SVG per symbol;
- one ASCII DXF per symbol;
- the decompressed PDF operator stream for each symbol;
- `manifest.json` with dimensions, BBox, matrix, item names, and output filenames.

## Current DXF strategy

The DXFs are R2000 ASCII files using `LINE` entities on a `BTX_SYMBOL` layer. Cubic Bézier curves, if encountered, are currently tessellated into line segments. In this particular library, the door swings are already stored as polylines, so no extra curve approximation was introduced.

Line colour, fill, clipping, and line width are represented accurately in SVG. The first DXF prototype focuses on geometry interoperability and does not yet reproduce fills, transparency, or PDF graphics-state effects.

## Confidence and limits

### High confidence for this file

- XML envelope and Version 1 root structure;
- hexadecimal zlib encoding;
- PDF annotation dictionary in `Raw`;
- resource-pointer matching through `BBObjPtr_...` IDs;
- PDF Form XObject appearance storage;
- nested FlateDecode content stream;
- geometry, dimensions, transforms, stroke style, and symbol names;
- successful vector extraction of all five symbols.

### Not yet generalised

Other BTX libraries may include:

- non-stamp annotation classes;
- grouped markups rather than a single Form XObject;
- nested Form or Image XObjects;
- raster images;
- text and embedded/subset fonts;
- transparency, blend modes, masks, patterns, and shadings;
- complex clipping paths;
- measurement/calibration metadata;
- newer BTX root versions or different resource conventions.

The next reliable step is to assemble a small corpus from multiple Revu versions and tool types, then run the decoder against each and classify the annotation/resource structures. A production converter should use an intermediate representation for paths, styling, text, images, groups, and transforms, with render-comparison tests against screenshots or PDFs generated by Revu.

## Package contents

- `svg/` — extracted vector symbols;
- `dxf/` — extracted CAD geometry in millimetres;
- `pdf_operators/` — second-stage decompressed PDF content streams;
- `decoded/Doors_pretty.xml` — formatted original XML, payloads still compressed;
- `decoded/decoded_payloads.txt` — all first-stage payloads decoded, plus inner Form streams;
- `manifest.json` — machine-readable summary;
- `preview.png` — visual contact sheet;
- `btx_extract.py` — repeatable extraction prototype;
- `source/Doors.btx` — original unpacked BTX used for the analysis.
