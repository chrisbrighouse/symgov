"""Stage 6 contract review: Ed's keyword gates, checked against real text.

The gates are UX heuristics, not the security boundary (Ed has no mutation
tools). They must still not refuse ordinary questions, let change requests
through, or hide the steward's own approved wording.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from symgov_backend.services import ed_orchestration as orchestration


ROOT = Path(__file__).resolve().parents[1]
[BUNDLE] = sorted((ROOT / "backend/symgov_backend/data/ed_knowledge/bundles").iterdir())
CHUNKS = [json.loads(line) for line in (BUNDLE / "bundle" / "chunks.jsonl").read_text().splitlines()]


def _in_scope(question: str) -> bool:
    return bool(orchestration._ALLOWED_TOPIC.search(question)) and not orchestration._is_operation_request(question)


@pytest.mark.parametrize("title", [chunk["title"] for chunk in CHUNKS])
def test_every_approved_claim_title_is_an_answerable_question(title):
    assert _in_scope(title)


@pytest.mark.parametrize(
    "question",
    [
        "List my projects.",
        "What permissions do I have?",
        "Where do submissions go?",
        "What classifications exist?",
        "Which standards are available?",
        "Tell me about the Catalogue",
        "Where can I change my PIN?",
        "Which organisations am I in?",
        "When will my submission be approved?",
        "Who can approve a submission?",
        "Do I need to be an admin to publish a symbol?",
        "Can a member add symbols to a symbol set?",
        "Why can I see this symbol but not edit it?",
        "Can you tell me who can approve submissions?",
        "What do I have to do to publish a symbol?",
        "How do I switch organization?",
        "What happens when a project is closed?",
    ],
)
def test_ordinary_questions_are_in_scope(question):
    assert _in_scope(question)


@pytest.mark.parametrize(
    "request_text",
    [
        "Close project P-01.",
        "Set my active project to P-01.",
        "Grant me the admin role.",
        "Ed: delete the project.",
        "Why is the sky blue? Also, delete the project.",
        "Explain why this failed and then delete the draft symbol set.",
        "Archive this symbol set.",
        "Rename the project to P-02.",
        "Revoke access for the reviewer.",
        "Please assign me the reviewer role.",
        "Submit this symbol for review.",
    ],
)
def test_change_requests_are_refused_before_the_model(request_text):
    assert orchestration._is_operation_request(request_text)


@pytest.mark.parametrize("chunk_id", [chunk["id"] for chunk in CHUNKS])
def test_no_approved_claim_reads_as_ed_claiming_a_change(chunk_id):
    [chunk] = [item for item in CHUNKS if item["id"] == chunk_id]

    assert not orchestration._claims_operation(chunk["text"])


@pytest.mark.parametrize(
    "claim",
    [
        "I have closed project P-01 for you.",
        "I've closed project P-01.",
        "Ed has granted you the admin role.",
        "The Symgov symbol set was deleted.",
        "The record has been removed.",
        "The Symgov symbol set is being deleted.",
    ],
)
def test_claims_that_ed_changed_something_are_still_caught(claim):
    assert orchestration._claims_operation(claim)


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("Why am I asked to change my PIN after signing in?", True),
        ("Once the PIN is changed you continue to where you were going.", True),
        ("My PIN is 4590, why is it rejected?", False),
        ("password: hunter2x", False),
        ("api_key=abc123def456", False),
    ],
)
def test_redaction_removes_credentials_but_not_ordinary_words(text, kept):
    redacted = orchestration.redact_credentials(text)

    assert (redacted == text) is kept
    if not kept:
        assert "[REDACTED]" in redacted
        assert not any(secret in redacted for secret in ("4590", "hunter2x", "abc123def456"))
