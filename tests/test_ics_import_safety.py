"""Regression: importing browse metadata must never classify concepts."""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from symgov_backend import ics_taxonomy as api


def test_crosswalk_sync_cannot_touch_semantic_assignments():
    session = Mock()
    prepared = api.prepare_import(
        (Path(api.__file__).parent / 'data/ICS.csv').read_bytes(),
        retrieved_at=datetime.now(timezone.utc), last_modified=None,
    )
    # The obsolete writer is removed, rather than left as an accidental public API.
    assert not hasattr(api, 'sync_crosswalk')
    assert not session.mock_calls


@pytest.mark.parametrize('code', ['A.B', '29..020', 'ABC._DEF'])
def test_dots_are_only_valid_in_ics_grammar(code):
    from symgov_backend.classification_schemes import NODE_CODE_PATTERN
    assert NODE_CODE_PATTERN.fullmatch(code) is None
