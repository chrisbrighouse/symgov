"""A small synthetic QElectroTech import package, shaped like `tools/build_package.py`'s output.

Four elements: two EN 60617 elements that share a concept (one carrying a
"Form 1 / Forme 1" caption the converter removes), one pole-set element with
the fallback credit whose concept name matches a concept the database already
holds, and one EN 60617 element credited to an individual author.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

COMMIT = "73cbffb" + "0" * 33
STYLE = "line-style:normal;line-weight:normal;filling:none;color:black"


def element(uid: str, name_en: str, name_fr: str, informations: str, body: str) -> bytes:
    return (
        '<definition type="element" width="20" height="20" hotspot_x="10" hotspot_y="10" link_type="simple">'
        f'<uuid uuid="{{{uid}}}"/><names><name lang="en">{name_en}</name><name lang="fr">{name_fr}</name></names>'
        f"<informations>{informations}</informations><description>{body}"
        '<terminal x="0" y="-10" orientation="n"/></description></definition>'
    ).encode("utf-8")


ELEMENTS = [
    {
        "qet_uuid": "11111111-1111-1111-1111-111111111111",
        "path": "10_electric/91_en_60617/en_60617_07/en_60617_07_21/en_60617_07_21_01.elmt",
        "set": "10_electric/91_en_60617",
        "ref": "07-21-01",
        "name": "Fuse, general symbol",
        "proposed": "Fuse, general symbol",
        "concept": "NEW:FuseGeneralSymbol",
        "category": "Switchgear / Distribution",
        "credit": "The QElectroTech team",
        "basis": "author_field",
        "informations": "Author: The QElectroTech team\nLicense: see ELEMENTS_LICENSE",
        "licence_class": "cc_by_3_0",
        "body": f'<rect x="-4" y="-8" width="8" height="16" style="{STYLE}"/>'
                f'<line x1="0" y1="-10" x2="0" y2="10" style="{STYLE}"/>',
    },
    {
        "qet_uuid": "22222222-2222-2222-2222-222222222222",
        "path": "10_electric/91_en_60617/en_60617_07/en_60617_07_21/en_60617_07_21_02.elmt",
        "set": "10_electric/91_en_60617",
        "ref": "07-21-02",
        "name": "Fuse, general symbol",
        "proposed": "Fuse, general symbol (EN 60617 07-21-02)",
        "concept": "NEW:FuseGeneralSymbol",
        "category": "Switchgear / Distribution",
        "credit": "The QElectroTech team",
        "basis": "author_field",
        "informations": "Author: The QElectroTech team",
        "licence_class": "cc_by_3_0",
        "body": f'<text x="-9" y="-11" text="Form 2 / Forme 2" font="Sans Serif,4,-1,5,50,0,0,0,0,0"/>'
                f'<rect x="-4" y="-8" width="8" height="16" style="{STYLE}"/>',
    },
    {
        "qet_uuid": "33333333-3333-3333-3333-333333333333",
        "path": "10_electric/10_allpole/130_terminals_terminal_strips/barrette_connexion_f.elmt",
        "set": "10_electric/10_allpole",
        "ref": "03-03-18",
        "name": "Terminal strip",
        "proposed": "Terminal strip",
        "concept": "NEW:TerminalStrip",
        "category": "Electrical Devices",
        "credit": "QElectroTech contributors",
        "basis": "fallback",
        "informations": "EN 60617: 03-03-18",
        "licence_class": "collection_default",
        "body": f'<circle x="-3" y="-3" diameter="6" style="{STYLE}"/>',
    },
    {
        "qet_uuid": "44444444-4444-4444-4444-444444444444",
        "path": "10_electric/91_en_60617/en_60617_08/en_60617_08_10/en_60617_08_10_01.elmt",
        "set": "10_electric/91_en_60617",
        "ref": "08-10-01",
        "name": "Lamp",
        "proposed": "Lamp",
        "concept": "NEW:Lamp",
        "category": "Lighting",
        "credit": "Ada Element",
        "basis": "author_field",
        "informations": "Author: Ada Element",
        "licence_class": "cc_by_3_0",
        "body": f'<circle x="-5" y="-5" diameter="10" style="{STYLE}"/>'
                f'<line x1="-3.5" y1="-3.5" x2="3.5" y2="3.5" style="{STYLE}"/>',
    },
]


def build_package() -> dict:
    files, manifest, worksheet = {}, [], []
    for item in ELEMENTS:
        data = element(item["qet_uuid"], item["name"], item["name"] + " (fr)", item["informations"], item["body"])
        elmt_file = "elmt/" + item["path"][:-5].replace("/", "__") + ".elmt"
        files[elmt_file] = data
        manifest.append({
            "qet_uuid": item["qet_uuid"], "path": item["path"], "set": item["set"], "folder_names": "Electric",
            "names": {"en": item["name"], "fr": item["name"] + " (fr)"}, "informations": item["informations"],
            "author_field": item["credit"] if item["basis"] == "author_field" else None, "author_candidate": None,
            "converter": None, "credit": item["credit"], "credit_basis": item["basis"], "licence_text": None,
            "licence_class": item["licence_class"], "en60617_ref": item["ref"], "link_type": "simple", "terminals": 1,
            "elmt_file": elmt_file, "elmt_sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
            "decision": "candidate",
        })
        worksheet.append({
            "qet_uuid": item["qet_uuid"], "action": "import", "proposed_name": item["proposed"], "original_name": item["name"],
            "discipline": "Electrical", "category": item["category"], "concept_key": item["concept"],
            "en60617_ref": item["ref"], "credit": item["credit"], "credit_basis": item["basis"], "path": item["path"],
            "notes": "", "confirmed": "yes",
        })
    return {
        "manifest": {
            "source": {"repository": "https://github.com/qelectrotech/qelectrotech-elements", "commit": COMMIT,
                       "committed_at": "2026-10-08T00:00:00+02:00", "licence_file": "ELEMENTS.LICENSE", "retrieved_on": "2026-10-20"},
            "phase": "1 (test)",
            "not_included": {},
            "elements": manifest,
        },
        "worksheet": worksheet,
        "files": files,
    }


def write_package(package: dict, directory: Path) -> Path:
    directory = Path(directory)
    (directory / "elmt").mkdir(parents=True, exist_ok=True)
    (directory / "curation").mkdir(exist_ok=True)
    for name, data in package["files"].items():
        (directory / name).write_bytes(data)
    (directory / "manifest.json").write_text(json.dumps(package["manifest"], indent=1), encoding="utf-8")
    with (directory / "curation" / "worksheet.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(package["worksheet"][0]))
        writer.writeheader()
        writer.writerows(package["worksheet"])
    return directory
