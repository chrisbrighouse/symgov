#!/usr/bin/env python3
"""Harvest the DEXPI example corpus into the pilot's selected symbol set (WP2).

Converts every Proteus XML in a local checkout of the DEXPI public example
corpus, then collapses the catalogue entries into distinct geometries and
applies decision D1.  Writes the converted assets and one `selection.json`
that WP4's ingestion driver consumes.

The corpus is not vendored: it is 87 MB and the pilot needs 169 symbols from
it.  Clone it first, at the revision you intend to record:

    git clone --depth 1 https://gitlab.com/dexpi/TrainingTestCases

Then:

    python3 scripts/run_dexpi_harvest.py --corpus ./TrainingTestCases \\
        --output /var/tmp/dexpi-harvest

Nothing here touches a database.  See
`docs/plans/2026-09-22-dexpi-symbol-library-pilot-implementation-plan.md`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.services.dexpi_converter import (  # noqa: E402
    DexpiConversionError,
    convert_dexpi,
)
from symgov_backend.services.dexpi_selection import select_symbols  # noqa: E402

# D5's attribution, and the values that reach every emitted SVG's metadata.
# CC BY 4.0 names the licensor; the corpus is published by DEXPI e.V. with
# per-file vendor contributions, which the selection records separately.
ATTRIBUTION = {
    "creator": "DEXPI e.V. and corpus contributors",
    "licence": "CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)",
    "source": "https://gitlab.com/dexpi/TrainingTestCases",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", required=True, help="Path to a TrainingTestCases checkout.")
    parser.add_argument("--output", required=True, help="Directory for converted assets and selection.json.")
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress the per-file conversion log."
    )
    return parser


def convert_corpus(corpus: Path, output: Path, *, quiet: bool) -> tuple[list, dict]:
    manifests: list[tuple[dict, str]] = []
    stats = {"files": 0, "converted": 0, "refused": 0, "symbols": 0, "failed_symbols": 0}

    # `rglob` is case-sensitive on Linux and 17 of the corpus's files are named
    # `.XML` -- every one of them a SAG contribution. Globbing for the lowercase
    # spelling alone silently harvested 130 files instead of 147.
    candidates = sorted(
        path for path in corpus.rglob("*") if path.is_file() and path.suffix.lower() == ".xml"
    )
    for ordinal, path in enumerate(candidates):
        if ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "<ShapeCatalogue" not in text:
            continue

        stats["files"] += 1
        relative = path.relative_to(corpus).as_posix()
        destination = output / "assets" / f"{ordinal:04d}"
        try:
            manifest = convert_dexpi(path, destination, attribution=ATTRIBUTION)
        except DexpiConversionError as error:
            stats["refused"] += 1
            if not quiet:
                print(f"  refused {relative}: {error.code}", file=sys.stderr)
            continue

        # The converter records only the basename; the entry's place inside the
        # package is this script's knowledge, so it writes it in.
        manifest["source_path"] = relative
        (destination / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

        stats["converted"] += 1
        stats["symbols"] += manifest["successful_symbol_count"]
        stats["failed_symbols"] += manifest["failed_symbol_count"]
        manifests.append((manifest, relative))

    return manifests, stats


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    corpus = Path(args.corpus).resolve()
    output = Path(args.output).resolve()
    if not corpus.is_dir():
        print(f"corpus not found: {corpus}", file=sys.stderr)
        return 2
    output.mkdir(parents=True, exist_ok=True)

    manifests, stats = convert_corpus(corpus, output, quiet=args.quiet)
    if not manifests:
        print("no catalogue-bearing files were converted", file=sys.stderr)
        return 1

    selection = select_symbols(manifests)
    selection["attribution"] = ATTRIBUTION
    selection["conversion"] = stats
    (output / "selection.json").write_text(json.dumps(selection, indent=2) + "\n", encoding="utf-8")

    summary = selection["summary"]
    print(f"files converted        {stats['converted']} of {stats['files']}")
    print(f"catalogue symbols      {stats['symbols']} converted, {stats['failed_symbols']} refused")
    print(f"distinct geometries    {summary['distinct_geometries']}")
    print(f"selected               {summary['selected_count']}")
    print(f"excluded (D1)          {summary['excluded_count']}")
    print(f"  named from reference {summary['name_basis_counts'].get('dexpi_reference_shape', 0)}")
    print(f"  named from class     {summary['name_basis_counts'].get('dexpi_component_class', 0)}")
    print(f"with ISO registration  {summary['with_registration']}"
          f" ({summary['with_editioned_registration']} edition-pinned)")
    print(f"semantic concepts      {summary['concept_count']}")
    print(f"written                {output / 'selection.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
