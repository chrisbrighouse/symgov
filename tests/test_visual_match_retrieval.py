"""Visual match retrieval: band rules and filter validation (no database)."""
from __future__ import annotations

import pytest

from symgov_backend.visual_match.errors import VisualMatchInputError
from symgov_backend.visual_match.retrieval import BandPolicy, assign_bands, validate_filters

P = BandPolicy()


def test_likely_needs_both_descriptors_in_the_first_group_and_a_cosine_floor():
    groups = [[0, 3], [1], [2]]
    assert assign_bands(groups, gemini_top=0, local_top=3, top_cosine=0.95, degraded=False, policy=P)[0] == "likely"
    assert assign_bands(groups, gemini_top=0, local_top=1, top_cosine=0.95, degraded=False, policy=P)[0] == "possible"
    assert assign_bands(groups, gemini_top=0, local_top=3, top_cosine=0.90, degraded=False, policy=P)[0] == "possible"


def test_degraded_mode_never_claims_likely_and_late_groups_are_weak():
    groups = [[i] for i in range(8)]
    bands = assign_bands(groups, gemini_top=0, local_top=0, top_cosine=0.99, degraded=True, policy=P)
    assert bands[:5] == ["possible"] * 5 and bands[5:] == ["weak"] * 3


def test_filters_are_validated_before_any_work():
    assert validate_filters({"catalogDisciplines": [" Piping ", ""]}) == {"catalogDisciplines": ["Piping"]}
    assert validate_filters(None) == {} and validate_filters({"useCases": []}) == {}
    for bad in ({"nope": ["x"]}, {"useCases": "P&ID"}, {"useCases": ["x" * 121]}, {"useCases": ["a"] * 51}):
        with pytest.raises(VisualMatchInputError) as caught:
            validate_filters(bad)
        assert caught.value.code == "invalid_filter"


def test_a_failed_query_embedding_degrades_and_logs_why(caplog):
    import numpy as np

    from symgov_backend.services.llm_embeddings import EmbeddingError
    from symgov_backend.visual_match.retrieval import _embed_rotations

    def embed(png):
        raise EmbeddingError("The embedding was served by a provider that is not approved.")

    gray = np.full((64, 96), 255, np.uint8)
    gray[30:34, 10:80] = 0
    with caplog.at_level("WARNING", logger="symgov_backend.visual_match.retrieval"):
        assert _embed_rotations(gray, embed) is None
    [record] = [r for r in caplog.records if "query embedding failed" in r.getMessage()]
    assert "error_type=EmbeddingError" in record.getMessage()
    assert "not approved" in record.getMessage()
