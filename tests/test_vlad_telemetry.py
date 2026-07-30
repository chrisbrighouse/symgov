import pytest
from scripts.run_vlad_validation import resolve_vlad_model


def test_vlad_model_resolution():
    assert resolve_vlad_model() is not None
