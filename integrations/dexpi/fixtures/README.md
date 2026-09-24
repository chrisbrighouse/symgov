# DEXPI conversion fixtures

Fixtures for `backend/symgov_backend/services/dexpi_converter.py` and
`tests/test_dexpi_conversion.py`.

## `C01V04-VER.EX01.xml`

The DEXPI reference P&ID, vendored from
[`gitlab.com/dexpi/TrainingTestCases`](https://gitlab.com/dexpi/TrainingTestCases)
at `dexpi 1.3/example pids/C01 DEXPI Reference P&ID/`.

- **SHA-256** `a2b172f04e0dcf9a668e158c6dee3b5fd0dd4e9027b572dc39e54470562b809c`
- **Licence** Creative Commons Attribution 4.0 International (CC BY 4.0),
  <https://creativecommons.org/licenses/by/4.0/>
- **Attribution** DEXPI e.V. and the corpus contributors
- **Specification version** DEXPI 1.3 (`PlantInformation/@ApplicationVersion` 1.3.1)

Unmodified. It carries a 24-entry `ShapeCatalogue`, 19 of whose shapes declare
an ISO 10628 registration number, which is what makes it the integration
fixture rather than a synthetic one.

The pinned SHA-256 is the same digest the converter reports as
`manifest["source_sha256"]`, and the same one an ingestion run records as
`SourcePackageEntry.original_asset_sha256`. If this file is ever re-vendored,
that digest changes and the ingestion provenance stops matching — re-pin
deliberately, never silently.

## `minimal_catalogue.xml`

Handwritten for the test suite. Not derived from any DEXPI corpus file and
therefore carries no third-party licence.

It exercises every primitive the converter supports (`Circle`, `PolyLine`,
`Line`, `TrimmedCurve`, filled `Shape`, `Text`) in one shape, plus the cases the
converter must handle rather than crash on:

| Shape | Exercises |
|---|---|
| `EVERY_PRIMITIVE_SHAPE` | all six primitives, a white stroke to normalise, an ISO registration number |
| `ABSURD_LINE_WEIGHT_SHAPE` | a `LineWeight` of 5000 on a 4-unit symbol, which must be clamped |
| `POINT_ONLY_SHAPE` | zero extent in both axes — refused as `degenerate_extent` |
| `NO_GEOMETRY_SHAPE` | attributes but no drawable primitive — refused as `no_geometry` |
| (unnamed) | no `ComponentName`, so it is not a catalogue entry at all |

Coordinates are Y-up as Proteus declares them, so the converter's Y-flip is
observable: `EVERY_PRIMITIVE_SHAPE`'s polyline rises to the right in source
coordinates and must fall to the right in the emitted SVG.
