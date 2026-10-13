"""The source register's status flags are operator data: stored, never served.

Decision 2026-10-13: origo/sizing/label/connections/mapping status stays on the
stored revision but is left out of every reader-facing payload -- the Catalog
row (`payload_with_rights_attribution`) and the Catalog Integration API
(`disc_extras`).
"""
from __future__ import annotations

import copy

from symgov_backend.published_catalog import (
    OPERATOR_ONLY_DISC_KEYS,
    payload_with_rights_attribution,
    served_disc,
)
from symgov_backend.symbol_state_variants import disc_extras


def _disc_payload() -> dict:
    return {
        "name": "Modular Valve Double Isolation and Bleed (DISC ND0004)",
        "dexpi": {"attribution_source": "rights_record", "component_classes": ["CustomOperatedValve"]},
        "disc": {
            "disc_id": "ND0004",
            "grouping": "Valve",
            "register_status": {"origo": "OK", "sizing": "OK", "label": "OK", "connections": None, "mapping": "OK"},
            "size_mm": {"h": 11, "w": 18},
            "references": {"TR1970": "STPV035"},
        },
        "geometry": {"units": "mm"},
    }


def test_register_status_is_the_only_operator_only_key():
    assert OPERATOR_ONLY_DISC_KEYS == {"register_status"}


def test_catalog_row_payload_leaves_out_register_status_and_keeps_the_rest():
    stored = _disc_payload()
    before = copy.deepcopy(stored)

    served = payload_with_rights_attribution(stored, "Licensed from DISC DEXPI.")

    assert "register_status" not in served["disc"]
    assert served["disc"]["disc_id"] == "ND0004"
    assert served["disc"]["size_mm"] == {"h": 11, "w": 18}
    assert served["disc"]["references"] == {"TR1970": "STPV035"}
    assert served["dexpi"]["attribution"] == "Licensed from DISC DEXPI."
    assert stored == before, "the stored payload must never be modified"


def test_catalog_row_payload_without_attribution_text_still_leaves_out_register_status():
    served = payload_with_rights_attribution(_disc_payload(), None)
    assert "register_status" not in served["disc"]


def test_payload_without_register_status_is_returned_unchanged():
    pilot = {"name": "Gate valve", "dexpi": {"attribution": "DEXPI TrainingTestCases"}}
    assert payload_with_rights_attribution(pilot, None) is pilot
    assert payload_with_rights_attribution(None, None) == {}


def test_integration_api_extras_leave_out_register_status():
    stored = _disc_payload()
    extras = disc_extras(stored)
    assert "register_status" not in extras["disc"]
    assert extras["disc"]["grouping"] == "Valve"
    assert extras["geometry"] == {"units": "mm"}
    assert "register_status" in stored["disc"]


def test_served_disc_passes_through_non_mappings():
    assert served_disc(None) is None
    assert served_disc("ND0004") == "ND0004"
