"""The recorded crosswalk decision register, and how it is matched to a queue.

The register is data, not code: `CROSSWALK` holds the *proposals*, which a
re-import compares as imported facts, and this holds the *decisions* about
them. The planner is pure, so everything here runs without a database; the
committed application lives in `test_ics_storage.py`.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from symgov_backend import ics_taxonomy as api

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs/ics-taxonomy-runbook.md"


def _described(domain, code, status, crosswalk_id="0" * 32, label="label"):
    return {
        "crosswalk_id": crosswalk_id,
        "domain": domain,
        "ics_code": code,
        "ics_label": label,
        "relation": "candidate",
        "review_status": status,
        "reason": "reason",
        "reviewed_by_user_id": None,
        "reviewed_at": None,
        "review_note": None,
    }


def test_register_covers_every_proposal_exactly_once():
    """The register and `CROSSWALK` must describe the same 22 pairs."""
    content = (Path(api.__file__).parent / "data/ICS.csv").read_bytes()
    prepared = api.prepare_import(
        content, retrieved_at=datetime.now(timezone.utc), last_modified=None
    )
    proposals = {(m["domain"], m["code"]) for m in prepared["crosswalk"]}
    register = api.load_decision_register()
    decided = {(row["domain"], row["ics_code"]) for row in register["decisions"]}

    assert decided == proposals
    assert len(register["decisions"]) == len(prepared["crosswalk"])

    # No domain may be left without a browse target by its own decisions.
    approved = {
        row["domain"] for row in register["decisions"] if row["decision"] == "approved"
    }
    assert approved == {domain for domain, _ in proposals}


def test_register_is_validated_on_load(tmp_path):
    register = api.load_decision_register()
    good = register["decisions"][0]

    def written(decisions):
        path = tmp_path / "register.json"
        path.write_text(json.dumps({"decisions": decisions}), encoding="utf-8")
        return path

    with pytest.raises(ValueError, match="empty"):
        api.load_decision_register(written([]))
    with pytest.raises(ValueError, match="twice"):
        api.load_decision_register(written([good, dict(good)]))
    with pytest.raises(ValueError, match="approved or rejected"):
        api.load_decision_register(written([{**good, "decision": "needs_review"}]))
    with pytest.raises(ValueError, match="domain and an ICS code"):
        api.load_decision_register(written([{**good, "ics_code": ""}]))
    with pytest.raises(ValueError, match="non-blank"):
        api.load_decision_register(written([{**good, "note": "  "}]))
    with pytest.raises(ValueError, match="non-blank"):
        api.load_decision_register(
            written([{**good, "note": "x" * (api.MAX_REVIEW_NOTE + 1)}])
        )


def test_plan_records_undecided_rows_and_skips_settled_ones():
    register = {"decisions": [
        {"domain": "Mechanical", "ics_code": "21", "decision": "approved", "note": "n"},
        {"domain": "Mechanical", "ics_code": "25", "decision": "rejected", "note": "m"},
    ]}
    plan = api.plan_register_application(register, [
        _described("Mechanical", "21", "needs_review", crosswalk_id="a" * 32),
        _described("Mechanical", "25", "rejected", crosswalk_id="b" * 32),
    ])

    assert plan["problems"] == []
    assert [a["ics_code"] for a in plan["planned"]] == ["21"]
    assert plan["planned"][0]["from"] == "needs_review"
    assert plan["planned"][0]["to"] == "approved"
    assert plan["planned"][0]["note"] == "n"
    # Already carrying the register's decision, so re-running changes nothing.
    assert [a["ics_code"] for a in plan["unchanged"]] == ["25"]


def test_plan_refuses_a_register_that_has_drifted_from_the_queue():
    register = {"decisions": [
        {"domain": "Mechanical", "ics_code": "21", "decision": "approved"},
    ]}

    absent = api.plan_register_application(register, [])
    assert absent["planned"] == []
    assert any("not in the queue" in problem for problem in absent["problems"])

    unregistered = api.plan_register_application(register, [
        _described("Mechanical", "21", "needs_review"),
        _described("HVAC", "27", "needs_review"),
    ])
    assert any("not in the register" in problem for problem in unregistered["problems"])

    ambiguous = api.plan_register_application(register, [
        _described("Mechanical", "21", "needs_review", crosswalk_id="a" * 32),
        _described("Mechanical", "21", "needs_review", crosswalk_id="b" * 32),
    ])
    assert ambiguous["planned"] == []
    assert any("more than one" in problem for problem in ambiguous["problems"])


def test_plan_never_silently_overwrites_a_reviewers_correction():
    """A row decided the other way is a human's later call, not stale data."""
    register = {"decisions": [
        {"domain": "Mechanical", "ics_code": "21", "decision": "approved", "note": "n"},
    ]}
    described = [_described("Mechanical", "21", "rejected")]

    refused = api.plan_register_application(register, described)
    assert refused["planned"] == []
    assert any("already rejected" in problem for problem in refused["problems"])

    allowed = api.plan_register_application(register, described, allow_correction=True)
    assert allowed["problems"] == []
    assert allowed["planned"][0]["from"] == "rejected"
    assert allowed["planned"][0]["to"] == "approved"


def test_runbook_register_matches_the_recorded_register():
    """The human table and the machine register cannot drift apart."""
    register = api.load_decision_register()
    expected = {
        (row["domain"], row["ics_code"]): row["decision"] for row in register["decisions"]
    }

    section = RUNBOOK.read_text(encoding="utf-8").split("## Mapping decision register")[1]
    section = section.split("**These decisions are not yet")[0]
    documented = {}
    for line in section.splitlines():
        match = re.match(
            r"^\|\s*([^|]+?)\s*\|\s*`([^`]+)`[^|]*\|\s*(approved|rejected)\s*\|", line
        )
        if match:
            documented[(match.group(1), match.group(2))] = match.group(3)

    assert documented == expected
