import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

from symgov_backend import ics_taxonomy as api


def test_cli_defaults_to_dry_run_and_archives_source(tmp_path, monkeypatch, capsys):
    cli = importlib.import_module('symgov_backend.ics_import')
    content = (Path(api.__file__).parent / 'data/ICS.csv').read_bytes()
    monkeypatch.setattr(cli, 'fetch_official', lambda: (content, datetime.now(timezone.utc), 'Tue, 25 Mar 2025 09:50:52 GMT'))
    monkeypatch.setattr(cli, 'create_engine', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('dry-run touched DB')))
    assert cli.main(['--archive', str(tmp_path / 'pull')]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['mode'] == 'dry-run'
    assert report['counts'] == {'fields': 40, 'groups': 401, 'subgroups': 940}
    assert (tmp_path / 'pull/ICS.csv').read_bytes() == content
    assert json.loads((tmp_path / 'pull/manifest.json').read_text()) == report
