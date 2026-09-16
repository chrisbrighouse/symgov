"""Official ICS operator import. Dry-run by default; never runs migrations."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from .ics_taxonomy import fetch_official, prepare_import, ingest_taxonomy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True, help='New directory for source and manifest')
    parser.add_argument('--apply', action='store_true', help='Requires separately approved target database')
    parser.add_argument('--author-id', type=uuid.UUID, help='Existing authorized operator UUID (apply only)')
    args = parser.parse_args(argv)
    if args.apply and (args.author_id is None or not os.environ.get('SYMGOV_DATABASE_URL')):
        parser.error('--apply requires --author-id and SYMGOV_DATABASE_URL in the environment')
    try:
        content, pulled_at, last_modified = fetch_official()
        prepared = prepare_import(content, retrieved_at=pulled_at, last_modified=last_modified)
        report = {k: prepared[k] for k in ('counts', 'provenance', 'crosswalk')}
        report['mode'] = 'apply' if args.apply else 'dry-run'
        args.archive.mkdir(parents=True, exist_ok=False)
        (args.archive / 'ICS.csv').write_bytes(content)
        # Archive first: even a failed DB import has a replayable source.
        (args.archive / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
        if args.apply:
            engine = create_engine(os.environ['SYMGOV_DATABASE_URL'], hide_parameters=True)
            try:
                with Session(engine) as session, session.begin():
                    result = ingest_taxonomy(session, prepared, author_id=args.author_id)
                report['result'] = {
                    **result,
                    'scheme_id': str(result['scheme_id']),
                    'import_id': str(result['import_id']),
                }
            finally:
                engine.dispose()
            (args.archive / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
        return 0
    except Exception:
        # Driver/network exceptions may include connection credentials. Never echo them.
        print('ICS import failed; no successful apply is reported. Check source approval, archive path and database readiness.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
