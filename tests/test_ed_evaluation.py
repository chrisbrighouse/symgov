"""Stage 6 retrieval evaluation over the committed, steward-reviewed bundle.

The fixture names, for each realistic question, the approved claim that
should support it and the rank it must reach. A change to the corpus, the
tokenizer or the ranker that moves an answer is caught here rather than in
the pilot.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from symgov_backend.ed_corpus_cli import verify_bundle
from symgov_backend.ed_retrieval import retrieve


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "backend/symgov_backend/data/ed_knowledge"
EVALUATION = json.loads((DATA / "evaluation.stage6.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def bundle():
    [home] = sorted((DATA / "bundles").iterdir())
    result = verify_bundle(bundle=home / "bundle", repository=ROOT)
    return (
        (home / "bundle" / "postings.json").read_bytes(),
        (home / "bundle" / "chunks.jsonl").read_bytes(),
        result.index_digest,
    )


def _ask(bundle, question):
    postings, chunks, digest = bundle
    return retrieve(
        postings, chunks, question,
        knowledge_version="evaluation", approved_index_digest=digest, repository_root=ROOT,
    )


def test_every_approved_claim_is_the_expected_answer_to_some_question():
    [home] = sorted((DATA / "bundles").iterdir())
    claims = {json.loads(line)["id"] for line in (home / "bundle" / "chunks.jsonl").read_text().splitlines()}
    assert claims == {case["expectClaim"] for case in EVALUATION["retrieval"]}


@pytest.mark.parametrize("case", EVALUATION["retrieval"], ids=lambda case: case["question"])
def test_a_realistic_question_retrieves_its_approved_claim(bundle, case):
    result = _ask(bundle, case["question"])

    ranked = [item.chunk_id for item in result.items]
    assert result.status == "answered"
    assert case["expectClaim"] in ranked[: case["withinTop"]], ranked


@pytest.mark.parametrize("question", EVALUATION["noAnswer"])
def test_an_off_topic_question_retrieves_nothing(bundle, question):
    assert _ask(bundle, question).status == "cannot_answer"


def _suggested_questions():
    source = (ROOT / "frontend/src/edChat.js").read_text(encoding="utf-8")
    block = re.search(r"ED_SUGGESTED_QUESTIONS = \[(.*?)\];", source, re.S).group(1)
    return [
        match.group(2).replace("\\'", "'")
        for match in re.finditer(r"(['\"])((?:\\.|(?!\1).)*)\1", block)
    ]


def test_every_suggested_question_retrieves_a_passage(bundle):
    """The questions Ed offers must be ones its approved knowledge can reach."""
    questions = _suggested_questions()
    assert len(questions) >= 6
    for question in questions:
        result = _ask(bundle, question)
        assert result.status == "answered" and result.items, question
