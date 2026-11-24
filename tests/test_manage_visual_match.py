"""The `visual-match` operator command: wiring only (the index itself is covered in test_visual_match_index*)."""
from __future__ import annotations

import json

import pytest


class _Session:
    rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def cli(monkeypatch):
    import manage_symgov
    from symgov_backend.visual_match import index as index_module

    session = _Session()
    monkeypatch.setattr(manage_symgov, "create_session_factory", lambda **_: lambda: session)
    calls = []

    class Report:
        def __init__(self):
            self.built = 3

    def fake_index(sess, **kwargs):
        calls.append(kwargs)
        return Report()

    monkeypatch.setattr(index_module, "index_visual_descriptors", fake_index)
    monkeypatch.setattr(index_module, "index_status", lambda sess, **kw: {"published": 5, "indexed": 4})
    return manage_symgov, session, calls


def test_the_command_is_registered():
    import manage_symgov

    command = next(a for a in manage_symgov.build_parser()._actions if a.dest == "command")
    assert "visual-match" in command.choices


def test_status_prints_coverage(cli, capsys):
    manage, _, _ = cli
    assert manage.main(["visual-match", "status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"published": 5, "indexed": 4}


def test_index_is_a_dry_run_unless_applied_and_no_embed_sends_nothing_to_the_provider(cli, capsys):
    manage, _, calls = cli
    assert manage.main(["visual-match", "index"]) == 0
    assert calls[-1]["apply"] is False and calls[-1]["embed"] is not None
    assert json.loads(capsys.readouterr().out)["applied"] is False
    assert manage.main(["visual-match", "index", "--apply", "--no-embed", "--limit", "7"]) == 0
    assert calls[-1]["apply"] is True and calls[-1]["embed"] is None and calls[-1]["limit"] == 7


def test_probe_needs_an_image_and_a_failure_names_only_the_error_class(cli, capsys, monkeypatch):
    manage, session, _ = cli
    with pytest.raises(SystemExit):
        manage.main(["visual-match", "probe"])
    from symgov_backend.visual_match import index as index_module

    def boom(*a, **k):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(index_module, "index_status", boom)
    assert manage.main(["visual-match", "status"]) == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "secret detail" not in err and session.rollbacks == 1
