"""seed CFIHOS source standards and add a provider identifier to editions

Revision ID: 20260910_0055
Revises: 20260910_0054
Create Date: 2026-09-10 00:00:00.000000

Follow-on to SM-P0-05, approved by Chris on 2026-09-10. Section 15.1 does not
ask SM-P0-05 to seed anything, so the seed is its own migration -- the same
shape 20260909_0052 used for the CFIHOS classification schemes.

`CFIHOS CORE source standard v2.0.csv` is a governed register of 305 source
standards, each with a `CFIHOS-9000xxxx` code, a code string and a title.
CFIHOS bakes the edition into the code string -- `API Spec 6D:2014` -- which
is exactly the split section 7.10 wants pulled apart into a standard plus an
edition. 19 base standards in the register genuinely carry two editions each
(API Spec 17D at 2011 and 2021, IOGP S-560, IEC 62271-200 and so on), so the
split is load-bearing rather than tidy-up: without it those 19 would collide
or duplicate.

**One schema change.** `standard_versions` gains a nullable
`provider_identifier`, with a partial unique index. Section 7.10 says to
preserve Standard and StandardVersion and extends neither, so this goes
beyond the specification's literal text and was approved explicitly. Without
it the CFIHOS code has nowhere to live: neither table had a provider-
identifier column, and dropping the code would discard exactly the "exact
provider identifier" traceability SM-P0-05 exists to provide, as well as the
only stable key by which a later CFIHOS release could be reconciled against
this seed. It sits on the *edition* because that is what the register
enumerates -- CFIHOS numbers the two API Spec 17D editions separately.

**What is seeded: 238 of the 305 rows, as 219 standards and 238 editions.**

The other 67 are excluded. Their codes use conventions no rule can split:
`ASME BPVC Section VIII` carries no edition at all, `Machinery Directive
2006/42/EC` has a year that is part of the identity rather than an edition,
`ASME B31.3 - 2020` spaces its edition differently, `GCRT5033 Iss 2` counts
issues, and `Standards of the Tubular Exchanger Manufacturers Association
Tenth Edition` spells it in words. Regexing an edition out of those would
invent data. No excluded row shares a base standard with an included one, so
nothing seeded here is left with a missing edition. They are omitted rather
than stored with a guessed split; adding them later, by hand, costs nothing
that this migration takes away.

Decisions recorded rather than silently applied:

* **Only `CODE:YYYY` is treated as a split.** 238 rows match; 3 of those carry
  a second colon inside the base (`BS EN 15804:2012+A2:2019`), and the base
  keeps it. `+A2` is BSI amendment notation -- "incorporating Amendment 2" --
  and is part of the standard's identity, so `STANDARD_CODE_PATTERN` in
  `standard_sources.py` was widened to accept `+` rather than the four
  affected rows being dropped.
* **Where two editions disagree on the title, the later edition wins.** 14 of
  the 19 do: API Spec 17D was "Design and Operation of Subsea Production
  Systems" in 2011 and "Specification for Subsea Wellhead and Tree Equipment"
  in 2021. `standards.title` holds one name, and the current one is the
  useful one. The superseded title is not lost -- it stays in CFIHOS's own
  register against its own code, which `provider_identifier` now records.
* **`issuing_body` is derived from the code prefix, and only where the prefix
  names a body unambiguously.** 213 of 219 get one. The six that do not are
  five `EN` standards -- EN 13852-1 is CEN and EN 60079-0 is CENELEC, and the
  prefix alone does not say which -- and `AC 150/5345-27E`, where "AC" is a
  document class (Advisory Circular), not an issuing body. Those stay NULL
  rather than being guessed.
* **`effective_date` is NULL throughout.** The register supplies a four-digit
  year, and a year is not a date. Inventing 1 January would make an ordering
  key that reads as a fact.
* **The "source standard still to be completed" flag is dropped.** 123 of the
  238 carry it. It tracks CFIHOS's progress writing its own register entry,
  not the standard: `API Spec 2C:2020` is flagged incomplete and is plainly a
  real, citable standard. There is nowhere to store it -- `standards` has no
  JSONB and no status vocabulary is defined for it -- and no seeded row is
  withheld because of it.
* **No `symbol_standard_links` rows are created.** Seeding a vocabulary is not
  asserting anything about a symbol. Every link is a governed assertion that
  starts at `proposed`.

Source provenance. Extracted from `CORE CFIHOS V2.0 CSV/CFIHOS CORE source
standard v2.0.csv` inside CORE-CFIHOS-CSV-v2.0.zip, published at
https://www.jip36-cfihos.org/cfihos-standards/, sha256
a69b98012d9e4a46495a3aed48eef8ce75a69d001eaa28b4c38cb0f3c921909d. The archive
is gitignored and absent from a fresh clone, so this migration is the extract
and no test reads the archive. The CSV is cp1252-encoded, not UTF-8; 57 of the
seeded titles carry en dashes, em dashes or ellipses, preserved verbatim.

Identifiers are uuid5(NAMESPACE_URL, "urn:symgov:standard:<code>") and
"urn:symgov:standard-version:<code>:<label>", so every environment agrees on
them without a lookup, and a re-upgrade re-seeds the same rows rather than a
duplicate set.
"""
from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260910_0055"
down_revision: Union[str, None] = "20260910_0054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (standard_code, title, issuing_body, ((version_label, cfihos_code), ...)).
# A frozen extract; see the docstring for how it was derived. Repeated here
# literally rather than imported, because a migration must keep applying
# after the application constants move on.
_SOURCE_STANDARDS: tuple[tuple[str, str, str | None, tuple[tuple[str, str], ...]], ...] = (
    ("API SPEC 17D", "Specification for Subsea Wellhead and Tree Equipment",
     "American Petroleum Institute", (("2011", "CFIHOS-90000003"), ("2021", "CFIHOS-90000171"),)),
    ("API SPEC 17J", "Specification for Unbonded Flexible Pipe",
     "American Petroleum Institute", (("2014", "CFIHOS-90000004"),)),
    ("API SPEC 17K", "Specification for Bonded Flexible Pipe",
     "American Petroleum Institute", (("2017", "CFIHOS-90000005"),)),
    ("API SPEC 2C", "Offshore Pedestal-Mounted Cranes",
     "American Petroleum Institute", (("2020", "CFIHOS-90000006"),)),
    ("API SPEC 5L", "Line Pipe",
     "American Petroleum Institute", (("2018", "CFIHOS-90000007"),)),
    ("API SPEC 6D", "Specification for Pipeline and Piping Valves",
     "American Petroleum Institute", (("2014", "CFIHOS-90000008"),)),
    ("API STD 17F", "Standard for Subsea Production Control Systems",
     "American Petroleum Institute", (("2017", "CFIHOS-90000009"),)),
    ("API STD 526", "Flanged Steel Pressure-Relief Valves",
     "American Petroleum Institute", (("2017", "CFIHOS-90000010"),)),
    ("API STD 546", "Brushless Synchronous Machines—500 kVA and Larger",
     "American Petroleum Institute", (("2008", "CFIHOS-90000011"),)),
    ("API STD 600", "Steel Gate Valves—Flanged and Butt-Welding Ends, Bolted Bonnets",
     "American Petroleum Institute", (("2015", "CFIHOS-90000012"),)),
    ("API STD 603", "Corrosion-Resistant, Bolted Bonnet Gate Valves—Flanged and Butt-Welding Ends",
     "American Petroleum Institute", (("2018", "CFIHOS-90000013"),)),
    ("API STD 609", "Butterfly Valves: Double-Flanged, Lug- and Wafer-Type",
     "American Petroleum Institute", (("2016", "CFIHOS-90000014"),)),
    ("API STD 610", "Centrifugal Pumps for Petroleum, Petrochemical and Natural Gas Industries",
     "American Petroleum Institute", (("2010", "CFIHOS-90000015"),)),
    ("API STD 611", "General-purpose Steam Turbines for Petroleum, Chemical, and Gas Industry Services",
     "American Petroleum Institute", (("2008", "CFIHOS-90000016"), ("2022", "CFIHOS-90000213"),)),
    ("API STD 612", "Petroleum, Petrochemical, and Natural Gas Industries—Steam Turbines— Special-purpose Applications",
     "American Petroleum Institute", (("2014", "CFIHOS-90000017"), ("2020", "CFIHOS-90000203"),)),
    ("API STD 613", "Special Purpose Gear Units for Petroleum, Chemical and Gas Industry Services",
     "American Petroleum Institute", (("2007", "CFIHOS-90000018"),)),
    ("API STD 616", "Gas Turbines for the Petroleum, Chemical, and Gas Industry Services",
     "American Petroleum Institute", (("2011", "CFIHOS-90000019"),)),
    ("API STD 617-1", "Axial and Centrifugal Compressors and Expander-Compressors - Part 1",
     "American Petroleum Institute", (("2010", "CFIHOS-90000020"),)),
    ("API STD 617-2", "Axial and Centrifugal Compressors and Expander-Compressors - Part 2",
     "American Petroleum Institute", (("2010", "CFIHOS-90000021"),)),
    ("API STD 617", "Axial and Centrifugal Compressors and Expander-Compressors",
     "American Petroleum Institute", (("2014", "CFIHOS-90000022"),)),
    ("API STD 618", "Reciprocating Compressors for Petroleum, Chemical and Gas Industry Services",
     "American Petroleum Institute", (("2007", "CFIHOS-90000023"),)),
    ("API STD 619", "Rotary-Type Positive Displacement Compressors for Petroleum, Petrochemical and Natural Gas Industries",
     "American Petroleum Institute", (("2010", "CFIHOS-90000024"),)),
    ("API STD 650", "Welded Tanks for Oil Storage",
     "American Petroleum Institute", (("2012", "CFIHOS-90000025"),)),
    ("API STD 660", "Shell-and-Tube Heat Exchangers",
     "American Petroleum Institute", (("2015", "CFIHOS-90000026"),)),
    ("API STD 670", "Machinery Protection Systems",
     "American Petroleum Institute", (("2014", "CFIHOS-90000027"),)),
    ("API STD 672", "Packaged, Integrally Geared Centrifugal Air Compressors for Petroleum, Chemical, and Gas Industry Services",
     "American Petroleum Institute", (("2004", "CFIHOS-90000028"),)),
    ("API STD 673", "Centrifugal Fans for Petroleum, Chemical, and Gas Industry Services",
     "American Petroleum Institute", (("2014", "CFIHOS-90000029"),)),
    ("API STD 674", "Positive Displacement Pumps—Reciprocating",
     "American Petroleum Institute", (("2010", "CFIHOS-90000030"), ("2016", "CFIHOS-90000108"),)),
    ("API STD 675", "Positive Displacement Pumps—Controlled Volume for Petroleum, Chemical, and Gas Industry Services",
     "American Petroleum Institute", (("2012", "CFIHOS-90000195"), ("2019", "CFIHOS-90000031"),)),
    ("API STD 676", "Positive Displacement Pumps—Rotary",
     "American Petroleum Institute", (("2009", "CFIHOS-90000032"),)),
    ("API STD 677", "General-Purpose Gear Units for Petroleum, Chemical and Gas Industry Services",
     "American Petroleum Institute", (("2006", "CFIHOS-90000033"),)),
    ("API STD 681", "Liquid Ring Compressors and Vacuum Pumps in Petroleum, Chemical, and Gas Industry Services",
     "American Petroleum Institute", (("1996", "CFIHOS-90000034"), ("2021", "CFIHOS-90000205"),)),
    ("EN 13852-1", "Cranes. Offshore cranes. General-purpose offshore cranes",
     None, (("2013", "CFIHOS-90000037"),)),
    ("EN 14015", "Specification for the design and manufacture of site built, vertical, cylindrical, flat-bottomed, above ground, welded, steel tanks for the storage of liquids at ambient temperature and above",
     None, (("2004", "CFIHOS-90000038"),)),
    ("IEC 60034-1", "Rotating electrical machines – Part 1: Rating and performance",
     "International Electrotechnical Commission", (("2017", "CFIHOS-90000040"),)),
    ("IEC 60269-1", "Low-voltage fuses – Part 1: General requirements",
     "International Electrotechnical Commission", (("2014", "CFIHOS-90000041"),)),
    ("IEC 60282-1", "High-voltage fuses – Part 1: Current-limiting fuses",
     "International Electrotechnical Commission", (("2009", "CFIHOS-90000042"), ("2020", "CFIHOS-90000043"),)),
    ("IEC 60529", "DEGREES OF PROTECTION PROVIDED BY ENCLOSURES (IP CODE)",
     "International Electrotechnical Commission", (("2013", "CFIHOS-90000044"),)),
    ("IEC 60534-4", "Industrial-Process Control Valves - Part 4: Inspection and Routine Testing",
     "International Electrotechnical Commission", (("2006", "CFIHOS-90000045"),)),
    ("IEC 60534-7", "Industrial-process control valves – Part 7: Control valve data sheet",
     "International Electrotechnical Commission", (("2010", "CFIHOS-90000046"),)),
    ("IEC 60947-2", "Low voltage Equipment - Part 2: Circuit-breakers",
     "International Electrotechnical Commission", (("2006", "CFIHOS-90000048"),)),
    ("IEC 61439-1", "Low-voltage switchgear and controlgear assemblies – Part 1: General rules",
     "International Electrotechnical Commission", (("2020", "CFIHOS-90000049"),)),
    ("IEC 61439-2", "Low-voltage switchgear and controlgear assemblies – Part 2: Power switchgear and controlgear assemblies",
     "International Electrotechnical Commission", (("2020", "CFIHOS-90000050"),)),
    ("IEC 62271-100", "High-voltage switchgear and controlgear – Part 100: Alternating-current circuit-breakers",
     "International Electrotechnical Commission", (("2017", "CFIHOS-90000051"),)),
    ("IEC 62271-200", "High-voltage switchgear and controlgear - Part 200: AC metal-enclosed switchgear and controlgear for rated voltages above 1 kV and up to and including 52 kV",
     "International Electrotechnical Commission", (("2011", "CFIHOS-90000052"), ("2021", "CFIHOS-90000317"),)),
    ("IOGP S-560", "LV Switchgear & Controlgear to IEC 61439-1&2",
     "International Association of Oil & Gas Producers", (("2016", "CFIHOS-90000053"), ("2022", "CFIHOS-90000240"),)),
    ("IOGP S-561", "Subsea Trees (API)",
     "International Association of Oil & Gas Producers", (("2018", "CFIHOS-90000054"), ("2023", "CFIHOS-90000241"),)),
    ("IOGP S-562", "Valve – Ball to API Spec 6D",
     "International Association of Oil & Gas Producers", (("2019", "CFIHOS-90000055"),)),
    ("IOGP S-563", "Materials for Piping and Valve Components - Material Data Sheets",
     "International Association of Oil & Gas Producers", (("2018", "CFIHOS-90000056"),)),
    ("IOGP S-611", "Valve – Gate to API Spec 600 and API Spec 603",
     "International Association of Oil & Gas Producers", (("2019", "CFIHOS-90000057"),)),
    ("IOGP S-612", "Air Compressor – Integrally Geared Centrifugal (API Std 672) – Version 2.0",
     "International Association of Oil & Gas Producers", (("2018", "CFIHOS-90000058"), ("2022", "CFIHOS-90000220"),)),
    ("IOGP S-613", "Air Dryer Package",
     "International Association of Oil & Gas Producers", (("2018", "CFIHOS-90000059"),)),
    ("IOGP S-614", "Heat Exchangers – Shell and Tube to API Std 660",
     "International Association of Oil & Gas Producers", (("2018", "CFIHOS-90000060"),)),
    ("IOGP S-615", "Information Requirements for Centrifugal Pumps (API)",
     "International Association of Oil & Gas Producers", (("2019", "CFIHOS-90000061"), ("2023", "CFIHOS-90000308"),)),
    ("IOGP S-616", "Line Pipe to API Spec 5L and ISO 3183",
     "International Association of Oil & Gas Producers", (("2019", "CFIHOS-90000062"),)),
    ("IOGP S-617", "Cranes – Offshore General Purpose to EN 13852-1",
     "International Association of Oil & Gas Producers", (("2018", "CFIHOS-90000063"),)),
    ("IOGP S-618", "Cranes – Offshore Pedestal Mounted to API Spec 2C",
     "International Association of Oil & Gas Producers", (("2018", "CFIHOS-90000064"),)),
    ("IOGP S-619", "Pressure Vessels – Unfired Fusion Welded – Version 2.0",
     "International Association of Oil & Gas Producers", (("2018", "CFIHOS-90000065"), ("2022", "CFIHOS-90000221"),)),
    ("IOGP S-620", "HV Switchgear and Controlgear",
     "International Association of Oil & Gas Producers", (("2018", "CFIHOS-90000066"), ("2022", "CFIHOS-90000242"),)),
    ("IOGP S-700", "Special Purpose Couplings",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000067"),)),
    ("IOGP S-703", "Low Voltage Motors – Specification",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000068"),)),
    ("IOGP S-705", "Welding of Pressure Containing Equipment & Piping",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000069"),)),
    ("IOGP S-708", "Subsea Pipeline Valves",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000070"),)),
    ("IOGP S-710", "Air-cooled Heat Exchangers",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000071"),)),
    ("IOGP S-712", "General Purpose Gear Units",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000072"),)),
    ("IOGP S-713", "Special Purpose Gear Units",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000073"),)),
    ("IOGP S-715", "Coating & Painting for Offshore, Marine Coastal & Subsea Environments",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000074"),)),
    ("IOGP S-717", "Noise Emitting Equipment",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000075"),)),
    ("IOGP S-719", "Water Fire Mist Protection Systems",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000076"),)),
    ("ISO 10437", "PETROLEUM, PETROCHEMICAL AND NATURAL GAS INDUSTRIES — STEAM TURBINES — SPECIAL-PURPOSE APPLICATIONS",
     "International Organization for Standardization", (("2003", "CFIHOS-90000079"),)),
    ("ISO 10438-1", "Lubrication, shaft-sealing and control-oil systems and auxiliaries — Part 1: General requirements",
     "International Organization for Standardization", (("2007", "CFIHOS-90000080"),)),
    ("ISO 10439-2", "PETROLEUM, PETROCHEMICAL AND NATURAL GAS INDUSTRIES — AXIAL AND CENTRIFUGAL COMPRESSORS AND EXPANDER-COMPRESSORS — PART 2: NON-INTEGRALLY GEARED CENTRIFUGAL AND AXIAL COMPRESSORS",
     "International Organization for Standardization", (("2015", "CFIHOS-90000081"),)),
    ("ISO 10440-1", "Petroleum, petrochemical and natural gas industries — Rotary-type positive-displacement compressors — Part 1: Process compressors",
     "International Organization for Standardization", (("2007", "CFIHOS-90000082"),)),
    ("ISO 10441", "Flexible couplings for mechanical power transmission — Special-purpose applications",
     "International Organization for Standardization", (("2007", "CFIHOS-90000083"),)),
    ("ISO 13631", "Petroleum and natural gas industries — Packaged reciprocating gas compressors",
     "International Organization for Standardization", (("2002", "CFIHOS-90000084"),)),
    ("ISO 13705", "Fired heaters for general refinery service",
     "International Organization for Standardization", (("2012", "CFIHOS-90000085"),)),
    ("ISO 13706", "Air-cooled heat exchangers",
     "International Organization for Standardization", (("2011", "CFIHOS-90000086"),)),
    ("ISO 13707", "PETROLEUM AND NATURAL GAS INDUSTRIES — RECIPROCATING COMPRESSORS",
     "International Organization for Standardization", (("2000", "CFIHOS-90000087"),)),
    ("ISO 13709", "Centrifugal pumps for petroleum, petrochemical and natural gas industries",
     "International Organization for Standardization", (("2009", "CFIHOS-90000088"),)),
    ("ISO 13880", "Content and drafting of a technical specification",
     "International Organization for Standardization", (("1999", "CFIHOS-90000089"),)),
    ("ISO 14224", "PETROLEUM, PETROCHEMICAL AND NATURAL GAS INDUSTRIES — COLLECTION AND EXCHANGE OF RELIABILITY AND MAINTENANCE DATA FOR EQUIPMENT",
     "International Organization for Standardization", (("2016", "CFIHOS-90000090"),)),
    ("ISO 15348", "PIPEWORK — METAL BELLOWS EXPANSION JOINTS — GENERAL [WITHDRAWN]",
     "International Organization for Standardization", (("2002", "CFIHOS-90000091"),)),
    ("ISO 15547-1", "Plate-type heat exchangers — Part 1: Plate-and-frame heat exchangers",
     "International Organization for Standardization", (("2005", "CFIHOS-90000092"),)),
    ("ISO 1680", "Acoustics — Test code for the measurement of airborne noise emitted by rotating electrical machines",
     "International Organization for Standardization", (("2013", "CFIHOS-90000093"),)),
    ("ISO 16812", "Shell-and-tube heat exchangers",
     "International Organization for Standardization", (("2019", "CFIHOS-90000094"),)),
    ("ISO 19901-5", "PETROLEUM AND NATURAL GAS INDUSTRIES — SPECIFIC REQUIREMENTS FOR OFFSHORE STRUCTURES — PART 5: WEIGHT CONTROL DURING ENGINEERING AND CONSTRUCTION",
     "International Organization for Standardization", (("2016", "CFIHOS-90000095"),)),
    ("ISO 21049", "Pumps — Shaft sealing systems for centrifugal and rotary pumps",
     "International Organization for Standardization", (("2004", "CFIHOS-90000096"),)),
    ("ISO 23251", "PETROLEUM, PETROCHEMICAL AND NATURAL GAS INDUSTRIES — PRESSURE-RELIEVING AND DEPRESSURING SYSTEMS",
     "International Organization for Standardization", (("2019", "CFIHOS-90000097"),)),
    ("ISO 25457", "Flare details for general refinery and petrochemical service",
     "International Organization for Standardization", (("2008", "CFIHOS-90000098"),)),
    ("ISO 28300", "PETROLEUM, PETROCHEMICAL AND NATURAL GAS INDUSTRIES — VENTING OF ATMOSPHERIC AND LOW-PRESSURE STORAGE TANKS",
     "International Organization for Standardization", (("2008", "CFIHOS-90000099"),)),
    ("ISO 3046-1", "Reciprocating internal combustion engines — Performance — Part 1: Declarations of power, fuel…",
     "International Organization for Standardization", (("2002", "CFIHOS-90000100"),)),
    ("ISO 3183", "PETROLEUM AND NATURAL GAS INDUSTRIES — STEEL PIPE FOR PIPELINE TRANSPORTATION SYSTEMS",
     "International Organization for Standardization", (("2019", "CFIHOS-90000102"),)),
    ("ISO 8528-1", "RECIPROCATING INTERNAL COMBUSTION ENGINE DRIVEN ALTERNATING CURRENT GENERATING SETS — PART 1: APPLICATION, RATINGS AND PERFORMANCE",
     "International Organization for Standardization", (("2018", "CFIHOS-90000103"),)),
    ("OCIMF MLA4", "Design and Construction Specification for Marine Loading Arms (MLA4)",
     "Oil Companies International Marine Forum", (("2019", "CFIHOS-90000106"),)),
    ("IOGP S-701", "AC Uninterruptible Power Systems (UPS)",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000109"),)),
    ("IOGP S-702", "DC Uninterruptible Power Systems (UPS)",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000110"),)),
    ("IOGP S-704", "High Voltage Three-phase Cage Induction Motors",
     "International Association of Oil & Gas Producers", (("2021", "CFIHOS-90000111"),)),
    ("IOGP S-707", "Actuators for On-off Valves",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000112"),)),
    ("IOGP S-716", "Small Bore Tubing and Fittings",
     "International Association of Oil & Gas Producers", (("2021", "CFIHOS-90000113"),)),
    ("IOGP S-720", "Transformers",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000114"),)),
    ("IOGP S-722", "Flare Package",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000115"),)),
    ("IOGP S-723", "Electric Process Heaters",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000116"),)),
    ("IOGP S-724", "Subsea Fasteners (Alloy and Carbon Steel Bolting)",
     "International Association of Oil & Gas Producers", (("2021", "CFIHOS-90000117"),)),
    ("IOGP S-725", "Subsea Fasteners (Corrosion-resistant Bolting)",
     "International Association of Oil & Gas Producers", (("2021", "CFIHOS-90000118"),)),
    ("IOGP S-726", "Recommended Practice for Application of Subsea Fasteners",
     "International Association of Oil & Gas Producers", (("2021", "CFIHOS-90000119"),)),
    ("IOGP S-728", "Reciprocating Positive Displacement Pumps",
     "International Association of Oil & Gas Producers", (("2021", "CFIHOS-90000120"),)),
    ("IOGP S-730", "Flanged Steel Pressure-relief Valves",
     "International Association of Oil & Gas Producers", (("2021", "CFIHOS-90000121"),)),
    ("IOGP S-732", "Low Voltage Motor Control Centres",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000122"),)),
    ("IOGP S-734", "AC Uninterruptible Power Supply (UPS) System",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000123"),)),
    ("IOGP S-738", "Insulation for Piping and Equipment",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000124"),)),
    ("IOGP S-740", "Batteries (IEC)",
     "International Association of Oil & Gas Producers", (("2020", "CFIHOS-90000125"),)),
    ("NFPA 750", "Standard On Water Mist Fire Protection Systems.",
     "National Fire Protection Association", (("2019", "CFIHOS-90000126"), ("2023", "CFIHOS-90000212"),)),
    ("DIRECTIVE 94/9/EC", "Equipment and protective systems intended for use in potentially explosive atmospheres",
     "European Union", (("1994", "CFIHOS-90000128"),)),
    ("EN 60079-0", "Explosive atmospheres - part 0: equipment - general requirements",
     None, (("2018", "CFIHOS-90000129"),)),
    ("NFPA 70", "National Electrical Code",
     "National Fire Protection Association", (("2020", "CFIHOS-90000130"), ("2023", "CFIHOS-90000294"),)),
    ("API RECOMMENDED PRACTICE 615", "Valve selection guide",
     "American Petroleum Institute", (("2016", "CFIHOS-90000131"),)),
    ("ISO 3977-5", "Gas turbines - procurement - part 5: applications for petroleum and natural gas industries",
     "International Organization for Standardization", (("2001", "CFIHOS-90000132"),)),
    ("ISO 13710", "Petroleum, petrochemical and natural gas industries — reciprocating positive displacement pumps",
     "International Organization for Standardization", (("2004", "CFIHOS-90000133"),)),
    ("IEC 60076-2", "Power transformers – part 2: temperature rise for liquid-immersed transformers",
     "International Electrotechnical Commission", (("2011", "CFIHOS-90000134"),)),
    ("EN 60079-14", "Explosive atmospheres part 14: electrical installations design, selection and erection",
     None, (("2016", "CFIHOS-90000135"),)),
    ("ISO 3166-1", "Codes for the representation of names of countries and their subdivisions",
     "International Organization for Standardization", (("2020", "CFIHOS-90000136"),)),
    ("ISO 639-1", "Codes for the representation of names of languages — part 1: alpha-2 code",
     "International Organization for Standardization", (("2002", "CFIHOS-90000137"),)),
    ("API STD 614", "Lubrication, Shaft-Sealing and Oil-Control Systems and Auxiliaries",
     "American Petroleum Institute", (("2007", "CFIHOS-90000139"),)),
    ("API RECOMMENDED PRACTICE 13C", "Recommended Practice on Drilling Fluid Processing Systems Evaluation",
     "American Petroleum Institute", (("2014", "CFIHOS-90000140"),)),
    ("API RECOMMENDED PRACTICE 7L", "Procedures for Inspection, Maintenance, Repair, and Remanufacture of Drilling Equipment",
     "American Petroleum Institute", (("1995", "CFIHOS-90000141"), ("2006", "CFIHOS-90000142"),)),
    ("API RECOMMENDED PRACTICE 8B", "Recommended Practice for Procedures for Inspections, Maintenance, Repair, and Remanufacture of Hoisting Equipment",
     "American Petroleum Institute", (("2014", "CFIHOS-90000143"),)),
    ("API SPEC 16F", "Specification for Marine Drilling Riser Equipment",
     "American Petroleum Institute", (("2017", "CFIHOS-90000144"),)),
    ("API SPEC 4F", "Specification for Drilling and Well Servicing Structures",
     "American Petroleum Institute", (("2020", "CFIHOS-90000145"),)),
    ("API SPEC 7-1", "Specification for Rotary Drill Stem Elements",
     "American Petroleum Institute", (("2004", "CFIHOS-90000146"), ("2006", "CFIHOS-90000170"),)),
    ("ISO 12944-2", "Paints and varnishes - Corrosion protection of steel structures by protective paint systems - Part 2: Classification of environments",
     "International Organization for Standardization", (("2017", "CFIHOS-90000147"),)),
    ("API STD 53", "Well Control Equipment Systems for Drilling Wells",
     "American Petroleum Institute", (("2018", "CFIHOS-90000148"),)),
    ("API STD 64", "Diverter Equipment Systems",
     "American Petroleum Institute", (("2017", "CFIHOS-90000149"),)),
    ("BS 436-5", "Spur and helical gears - Definitions and allowable values of deviations relevant to radial composite deviations and runout information",
     "British Standards Institution", (("1997", "CFIHOS-90000158"),)),
    ("BS 5950-1", "Structural use of steelwork in building - Code of practice for design. Rolled and welded sections",
     "British Standards Institution", (("2000", "CFIHOS-90000159"),)),
    ("BS 7608+A1", "Guide to fatigue design and assessment of steel products",
     "British Standards Institution", (("2015", "CFIHOS-90000160"),)),
    ("ISO 16368", "Mobile elevating work platforms — Design, calculations, safety requirements and test methods",
     "International Organization for Standardization", (("2010", "CFIHOS-90000163"),)),
    ("ISO 6707-1", "Buildings and civil engineering works — Vocabulary — Part 1: General terms",
     "International Organization for Standardization", (("2020", "CFIHOS-90000164"),)),
    ("API RECOMMENDED PRACTICE 500", "Recommended Practice for Classification of Locations for Electrical Installations at Petroleum Facilities Classified as Class I, Division 1 and Division 2",
     "American Petroleum Institute", (("2012", "CFIHOS-90000166"),)),
    ("API SPEC 7K", "Drilling and Well Servicing Equipment",
     "American Petroleum Institute", (("2015", "CFIHOS-90000167"),)),
    ("IOGP REPORT 544", "Standardization of barrier definitions - Supplement to Report 415",
     "International Association of Oil & Gas Producers", (("2016", "CFIHOS-90000168"),)),
    ("API STD 671", "Special-Purpose Couplings for Petroleum, Chemical, and Gas Industry Services",
     "American Petroleum Institute", (("2020", "CFIHOS-90000169"),)),
    ("IEC 62550", "Spare parts provisioning",
     "International Electrotechnical Commission", (("2017", "CFIHOS-90000175"),)),
    ("BS EN 15804:2012+A2", "Sustainability of construction works. Environmental product declarations. Core rules for the product category of construction products",
     "British Standards Institution", (("2019", "CFIHOS-90000178"),)),
    ("ISO 21930", "Core rules for environmental product declarations of construction products and services",
     "International Organization for Standardization", (("2017", "CFIHOS-90000179"),)),
    ("API STD 685", "Sealless Centrifugal Pumps for Petroleum, Petrochemical, and Gas Industry Process Service",
     "American Petroleum Institute", (("2022", "CFIHOS-90000181"),)),
    ("IEC 62271-111", "High-voltage switchgear and controlgear - Part 111: Automatic circuit reclosers for alternating current systems up to and including 38 kV",
     "International Electrotechnical Commission", (("2019", "CFIHOS-90000184"),)),
    ("API STD 663", "Hairpin Type Heat Exchangers",
     "American Petroleum Institute", (("2014", "CFIHOS-90000190"), ("2022", "CFIHOS-90000185"),)),
    ("API 6D", "Specification for Valves",
     "American Petroleum Institute", (("2021", "CFIHOS-90000186"),)),
    ("API SPEC 11E", "Pumping Units",
     "American Petroleum Institute", (("2022", "CFIHOS-90000187"),)),
    ("BS 8004:2015+A1", "Code of practice for foundations",
     "British Standards Institution", (("2020", "CFIHOS-90000189"),)),
    ("API SPEC 12L", "Specification for Vertical and Horizontal Emulsion Treaters",
     "American Petroleum Institute", (("2008", "CFIHOS-90000193"),)),
    ("API RECOMMENDED PRACTICE 14C", "Analysis, Design, Installation, and Testing of Safety Systems for Offshore Production Facilities",
     "American Petroleum Institute", (("2017", "CFIHOS-90000194"),)),
    ("API RECOMMENDED PRACTICE 576", "Inspection of Pressure-relieving Devices",
     "American Petroleum Institute", (("2017", "CFIHOS-90000196"),)),
    ("API STD 668", "Brazed Aluminum Plate-fin Heat Exchangers",
     "American Petroleum Institute", (("2018", "CFIHOS-90000197"),)),
    ("ISO 7149", "Continuous handling equipment",
     "International Organization for Standardization", (("1982", "CFIHOS-90000198"),)),
    ("ISO/TS 15926-4", "Integration of life-cycle data for process plants including oil and gas production facilities",
     "International Organization for Standardization", (("2007", "CFIHOS-90000199"),)),
    ("IEC 62282-3-1", "Fuel cell technologies - Part 3-1: Stationary fuel cell power systems - Safety",
     "International Electrotechnical Commission", (("2007", "CFIHOS-90000201"),)),
    ("API STD 2CCU", "Improved design, manufacture, inspection and testing of Cargo Carrying Units.",
     "American Petroleum Institute", (("2017", "CFIHOS-90000202"),)),
    ("API RECOMMENDED PRACTICE 1631", "Interior Lining of Underground Storage Tanks",
     "American Petroleum Institute", (("1997", "CFIHOS-90000204"),)),
    ("ISO 1049", "Continuous mechanical handling equipment for loose bulk materials",
     "International Organization for Standardization", (("1975", "CFIHOS-90000206"),)),
    ("ICAO ANNEX 14 VOL I", "Aerodrome Design and Operations",
     "International Civil Aviation Organization", (("2022", "CFIHOS-90000208"),)),
    ("ICAO ANNEX 14 VOL II", "Heliports",
     "International Civil Aviation Organization", (("2020", "CFIHOS-90000209"),)),
    ("AC 150/5345-27E", "Specification for Wind Cone Assemblies",
     None, (("2013", "CFIHOS-90000210"),)),
    ("IEC 61400-1", "Wind energy generation systems - Part 1: Design requirements",
     "International Electrotechnical Commission", (("2019", "CFIHOS-90000211"),)),
    ("API STD 537", "Flare Details for Petroleum, Petrochemical, and Natural Gas Industries",
     "American Petroleum Institute", (("2017", "CFIHOS-90000214"),)),
    ("IEC 62830-5", "Semiconductor devices - Semiconductor devices for energy harvesting and generation - Part 5: Test method for measuring generated power from flexible thermoelectric devices",
     "International Electrotechnical Commission", (("2021", "CFIHOS-90000215"),)),
    ("IEC 60076-1", "Power transformers - Part 1: General",
     "International Electrotechnical Commission", (("2011", "CFIHOS-90000216"),)),
    ("IEC 62040-5-3", "Uninterruptible power systems (UPS) - Part 5-3: DC output UPS - Performance and test requirements",
     "International Electrotechnical Commission", (("2016", "CFIHOS-90000217"),)),
    ("IEEE 841", "IEEE Standard for Petroleum and Chemical Industry--Premium-Efficiency, Severe-Duty, Totally Enclosed Squirrel Cage Induction Motors from 0.75 kW to 370 kW (1 hp to 500 hp)",
     "Institute of Electrical and Electronics Engineers", (("2021", "CFIHOS-90000218"),)),
    ("IEEE C37.20.1", "IEEE Standard for Metal-Enclosed Low-Voltage (1000 V ac and below, 3200 V dc and below) Power Circuit Breaker Switchgear - Amendment 1: Control and Secondary Circuits and Devices, and All Wiring",
     "Institute of Electrical and Electronics Engineers", (("2020", "CFIHOS-90000219"),)),
    ("IOGP S-711", "Diesel Engines",
     "International Association of Oil & Gas Producers", (("2021", "CFIHOS-90000222"),)),
    ("IOGP S-718", "Basic Process Measurement Instruments",
     "International Association of Oil & Gas Producers", (("2022", "CFIHOS-90000223"),)),
    ("IOGP S-727", "Metal-Enclosed LV Power Circuit Breaker Switchgear (IEEE Std C37.20.1)",
     "International Association of Oil & Gas Producers", (("2021", "CFIHOS-90000224"),)),
    ("IOGP S-729", "Control Valves",
     "International Association of Oil & Gas Producers", (("2022", "CFIHOS-90000225"),)),
    ("IOGP S-731", "Operator & Mounting Kits for Subsea Pipeline Valves (API)",
     "International Association of Oil & Gas Producers", (("2021", "CFIHOS-90000226"),)),
    ("IOGP S-735", "Casing & Tubing (API)",
     "International Association of Oil & Gas Producers", (("2022", "CFIHOS-90000227"),)),
    ("IOGP S-736", "LV AC Drives (IEC)",
     "International Association of Oil & Gas Producers", (("2021", "CFIHOS-90000228"),)),
    ("IOGP S-737", "Deluge Skids",
     "International Association of Oil & Gas Producers", (("2021", "CFIHOS-90000229"),)),
    ("IOGP S-739", "Pressure Regulators",
     "International Association of Oil & Gas Producers", (("2022", "CFIHOS-90000230"),)),
    ("IOGP S-741", "DC UPS And Associated Batteries (NEMA PE5)",
     "International Association of Oil & Gas Producers", (("2021", "CFIHOS-90000231"),)),
    ("IOGP S-742", "MV AC Contactors, Controllers, and Control Centers (UL 347)",
     "International Association of Oil & Gas Producers", (("2022", "CFIHOS-90000232"),)),
    ("IOGP S-747", "HV AC Drive Systems",
     "International Association of Oil & Gas Producers", (("2022", "CFIHOS-90000233"),)),
    ("IOGP S-750", "Extra-high Voltage Gas-insulated Switchgear",
     "International Association of Oil & Gas Producers", (("2023", "CFIHOS-90000234"),)),
    ("IOGP S-753", "Battery Energy Storage Systems (BESS)",
     "International Association of Oil & Gas Producers", (("2023", "CFIHOS-90000235"),)),
    ("IOGP S-754", "ANSI Transformers (IEEE)",
     "International Association of Oil & Gas Producers", (("2023", "CFIHOS-90000236"),)),
    ("NEMA PE5", "NEMA PE5",
     "National Electrical Manufacturers Association", (("1997", "CFIHOS-90000237"),)),
    ("PIP ELSAP04", "Uninterruptible Power Supply (UPS) System Specification",
     "Process Industry Practices", (("2020", "CFIHOS-90000238"),)),
    ("UL845", "UL Standard for Safety Motor Control Centers",
     "Underwriters Laboratories", (("2021", "CFIHOS-90000239"),)),
    ("IEC 60051-1", "Direct acting indicating analogue electrical measuring instruments and their accessories - Part 1: Definitions and general requirements common to all parts",
     "International Electrotechnical Commission", (("2016", "CFIHOS-90000244"),)),
    ("IEC 62057-1", "Electrical energy meters - Test equipment, techniques and procedures - Part 1: Stationary meter test units (MTUs)",
     "International Electrotechnical Commission", (("2023", "CFIHOS-90000245"),)),
    ("IEC TR 61641", "Enclosed low-voltage switchgear and controlgear assemblies - Guide for testing under conditions of arcing due to internal fault",
     "International Electrotechnical Commission", (("2014", "CFIHOS-90000246"),)),
    ("IEC 62271-1", "High-voltage switchgear and controlgear - Part 1: Common specifications for alternating current switchgear and controlgear",
     "International Electrotechnical Commission", (("2017", "CFIHOS-90000247"),)),
    ("IEC 62271-3", "High-voltage switchgear and controlgear - Part 3: Digital interfaces based on IEC 61850",
     "International Electrotechnical Commission", (("2015", "CFIHOS-90000248"),)),
    ("ISO 12944-5", "Paints and varnishes - Corrosion protection of steel structures by protective paint systems - Part 5: Protective paint systems",
     "International Organization for Standardization", (("2019", "CFIHOS-90000249"),)),
    ("ISO/IEC 646", "Information technology — ISO 7-bit coded character set for information interchange",
     "ISO/IEC", (("1991", "CFIHOS-90000253"),)),
    ("IEC 60085", "Electrical insulation - Thermal evaluation and designation",
     "International Electrotechnical Commission", (("2007", "CFIHOS-90000291"),)),
    ("IEC 60079-0", "Explosive atmospheres - Part 0: Equipment - General requirements",
     "International Electrotechnical Commission", (("2017", "CFIHOS-90000292"),)),
    ("NFPA 497", "Recommended Practice for the Classification of Flammable Liquids, Gases, or Vapors and of Hazardous (Classified) Locations for Electrical Installations in Chemical Process Areas",
     "National Fire Protection Association", (("2024", "CFIHOS-90000293"),)),
    ("IEC 62262", "Degrees of protection provided by enclosures for electrical equipment against external mechanical impacts (IK code)",
     "International Electrotechnical Commission", (("2002", "CFIHOS-90000295"),)),
    ("IEC 60034-18-42", "Rotating electrical machines - Part 18-42: Partial discharge resistant electrical insulation systems (Type II) used in rotating electrical machines fed from voltage converters - Qualification tests",
     "International Electrotechnical Commission", (("2017", "CFIHOS-90000296"),)),
    ("IEC 62439-3", "Industrial communication networks - High availability automation networks - Part 3: Parallel Redundancy Protocol (PRP) and High-availability Seamless Redundancy (HSR)",
     "International Electrotechnical Commission", (("2021", "CFIHOS-90000297"),)),
    ("ISO 5753-1", "Rolling bearings - Internal clearance - Part 1: Radial internal clearance for radial bearings",
     "International Organization for Standardization", (("2009", "CFIHOS-90000298"),)),
    ("IEC 60269-2", "Low-voltage fuses - Part 2: Supplementary requirements for fuses for use by authorized persons (fuses mainly for industrial application) - Examples of standardized systems of fuses A to K",
     "International Electrotechnical Commission", (("2013", "CFIHOS-90000299"),)),
    ("IEC 61000-2-4", "Electromagnetic compatibility (EMC) - Part 2-4: Environment - Compatibility levels in industrial plants for low-frequency conducted disturbances",
     "International Electrotechnical Commission", (("2002", "CFIHOS-90000300"),)),
    ("API STD 541", "Form-wound Squirrel Cage Induction Motors—375 kW (500 Horsepower) and Larger",
     "American Petroleum Institute", (("2014", "CFIHOS-90000301"),)),
    ("IEC 60721-2-6", "Classification of environmental conditions - Part 2-6: Environmental conditions appearing in nature - Earthquake vibration and shock",
     "International Electrotechnical Commission", (("2022", "CFIHOS-90000302"),)),
    ("IEC 60034-12", "Rotating electrical machines - Part 12: Starting performance of single-speed three-phase cage induction motors",
     "International Electrotechnical Commission", (("2016", "CFIHOS-90000303"),)),
    ("ISO 12944-1", "Paints and varnishes — Corrosion protection of steel structures by protective paint systems — Part 1: General introduction",
     "International Organization for Standardization", (("2017", "CFIHOS-90000304"),)),
    ("IEC 60034-14", "Rotating electrical machines - Part 14: Mechanical vibration of certain machines with shaft heights 56 mm and higher - Measurement, evaluation and limits of vibration severity",
     "International Electrotechnical Commission", (("2018", "CFIHOS-90000305"),)),
    ("IEC 61243-5", "Live working - Voltage detectors - Part 5: Voltage detecting systems (VDS)",
     "International Electrotechnical Commission", (("1997", "CFIHOS-90000306"),)),
    ("IEC 62271-206", "High-voltage switchgear and controlgear - Part 206: Voltage presence indicating systems for rated voltages above 1 kV and up to and including 52 kV",
     "International Electrotechnical Commission", (("2011", "CFIHOS-90000307"),)),
    ("ISO 7-1", "Pipe threads where pressure-tight joints are made on the threads - Part 1: Dimensions, tolerances and designation",
     "International Organization for Standardization", (("1994", "CFIHOS-90000310"),)),
    ("ISO 228-1", "Pipe threads where pressure-tight joints are not made on the threads-Part 1: Dimensions, tolerances and designation",
     "International Organization for Standardization", (("2000", "CFIHOS-90000311"),)),
    ("ISO 15156-1", "Petroleum and natural gas industries - Materials for use in H2S-containing environments in oil and gas production - Part 1: General principles for selection of cracking-resistant materials",
     "International Organization for Standardization", (("2020", "CFIHOS-90000312"),)),
    ("ISO 17945", "Petroleum, petrochemical and natural gas industries - Metallic materials resistant to sulfide stress cracking in corrosive petroleum refining environments",
     "International Organization for Standardization", (("2015", "CFIHOS-90000313"),)),
    ("BS EN 13445-1:2002+A3", "Unfired pressure vessels - General",
     "British Standards Institution", (("2007", "CFIHOS-90000314"),)),
    ("EN 13445-1", "Unfired pressure vessels - General",
     None, (("2021", "CFIHOS-90000315"),)),
    ("IEC 62271", "High-voltage switchgear and controlgear - All parts",
     "International Electrotechnical Commission", (("2024", "CFIHOS-90000316"),)),
    ("IOGP S-733", "Procurement Data Sheet for LV Motors (IEEE Std 841) – V2.0.",
     "International Association of Oil & Gas Producers", (("2022", "CFIHOS-90000318"),)),
)


def _standard_id(standard_code: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"urn:symgov:standard:{standard_code}")


def _version_id(standard_code: str, version_label: str) -> uuid.UUID:
    return uuid.uuid5(
        uuid.NAMESPACE_URL, f"urn:symgov:standard-version:{standard_code}:{version_label}"
    )


def upgrade() -> None:
    op.add_column(
        "standard_versions", sa.Column("provider_identifier", sa.Text(), nullable=True)
    )
    op.create_check_constraint(
        "provider_identifier",
        "standard_versions",
        "provider_identifier is null or (btrim(provider_identifier) <> '' "
        "and char_length(provider_identifier) <= 512)",
    )
    op.create_index(
        "uq_standard_versions_provider_identifier",
        "standard_versions",
        ["provider_identifier"],
        unique=True,
        postgresql_where=sa.text("provider_identifier is not null"),
    )

    connection = op.get_bind()
    for standard_code, title, issuing_body, editions in _SOURCE_STANDARDS:
        standard_id = _standard_id(standard_code)
        connection.execute(
            sa.text(
                "INSERT INTO standards (id, standard_code, title, issuing_body, status, "
                "created_at, updated_at) "
                "VALUES (:id, :standard_code, :title, :issuing_body, 'active', now(), now()) "
                "ON CONFLICT DO NOTHING"
            ),
            {
                "id": standard_id,
                "standard_code": standard_code,
                "title": title,
                "issuing_body": issuing_body,
            },
        )
        for version_label, provider_identifier in editions:
            connection.execute(
                sa.text(
                    "INSERT INTO standard_versions (id, standard_id, version_label, "
                    "effective_date, status, provider_identifier, created_at, updated_at) "
                    "VALUES (:id, :standard_id, :version_label, NULL, 'active', "
                    ":provider_identifier, now(), now()) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": _version_id(standard_code, version_label),
                    "standard_id": standard_id,
                    "version_label": version_label,
                    "provider_identifier": provider_identifier,
                },
            )


def downgrade() -> None:
    """Remove the seeded register and the column that carries its identifiers.

    Deleting the editions fails rather than cascading if a symbol standard
    link has since been asserted against one -- `symbol_standard_links`
    references `standard_versions` with no ON DELETE, so PostgreSQL refuses.
    That is the correct outcome: a downgrade must not silently discard a
    governed source assertion. 20260909_0052's downgrade takes the same line
    for the same reason.

    Only rows this migration seeded are touched. They are addressed by their
    deterministic uuid5 identifiers, so a standard registered by hand that
    happens to share a code is left alone.
    """
    connection = op.get_bind()
    for standard_code, _title, _issuing_body, editions in _SOURCE_STANDARDS:
        for version_label, _provider_identifier in editions:
            connection.execute(
                sa.text("DELETE FROM standard_versions WHERE id = :id"),
                {"id": _version_id(standard_code, version_label)},
            )
        connection.execute(
            sa.text("DELETE FROM standards WHERE id = :id"), {"id": _standard_id(standard_code)}
        )

    op.drop_index("uq_standard_versions_provider_identifier", table_name="standard_versions")
    op.drop_constraint("provider_identifier", "standard_versions", type_="check")
    op.drop_column("standard_versions", "provider_identifier")
