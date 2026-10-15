#!/usr/bin/env python3
"""Seed and serve a disposable local Symgov API for the accessibility pass.

    python3 scripts/a11y-local-api.py seed  --database-url URL --state-dir DIR
    python3 scripts/a11y-local-api.py serve --database-url URL --state-dir DIR \
        --port 8031 --web-origin http://localhost:8030

Local only. The database URL must name 127.0.0.1 or localhost, and before any
backend module is imported every production default is pointed into
--state-dir: the database and storage env files, the Hermes env file, the
agent runtime roots under /data/.openclaw, and object storage (SVGs are kept
as files under <state-dir>/objects). No agent, email or embedding worker runs.

`seed` expects a database already at `alembic upgrade head`. It creates three
accounts, all with PIN 1234 (see docs/ops/a11y-axe.md), the four DISC-style
symbols from tests/disc_dexpi_fixtures.py, a DEXPI pilot symbol (TST-1) and a
legacy intake symbol (LEG-1), all published, and prints the catalog IDs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
PIN = "1234"
READER_EMAIL = "a11y-reader@example.test"
STAFF_EMAIL = "a11y-staff@example.test"
ORG_ADMIN_EMAIL = "a11y-org-admin@example.test"
NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
PRODUCTION_PREFIXES = ("/data/.openclaw", "/root/.hermes", "/docker/symgov-hermes", "/docker/openclaw-hz0t/data/.openclaw")
FLAGS = (
    "SYMGOV_ORGANIZATIONS_ENABLED", "SYMGOV_ORGANIZATION_ADMIN_ENABLED", "SYMGOV_SYMBOL_SETS_ENABLED",
    "SYMGOV_ORGANIZATION_SYMBOLS_ENABLED", "SYMGOV_ORGANIZATION_AGENTS_ENABLED", "SYMGOV_PLATFORM_ADMIN_ENABLED",
    "SYMGOV_SEMANTIC_REVIEW_ENABLED",
)


def _require_local(database_url: str) -> None:
    host = urlsplit(database_url.replace("+psycopg", "")).hostname
    if host not in {"127.0.0.1", "localhost"}:
        sys.exit(f"Refusing a non-local database host: {host!r}. This helper is for a disposable local database only.")


def _isolate(args) -> Path:
    """Environment and production-path isolation. Runs before any backend import."""
    state = Path(args.state_dir).resolve()
    (state / "objects").mkdir(parents=True, exist_ok=True)
    (state / "sandbox").mkdir(parents=True, exist_ok=True)
    (state / "empty.env").write_text("", encoding="utf-8")
    (state / "db.env").write_text(
        f"SYMGOV_DATABASE_URL={args.database_url}\nSYMGOV_MIGRATION_DATABASE_URL={args.database_url}\n", encoding="utf-8"
    )
    # A storage endpoint nothing listens on: an unpatched storage call fails locally.
    (state / "storage.env").write_text(
        "SYMGOV_S3_ENDPOINT=http://127.0.0.1:9\nSYMGOV_S3_BUCKET=a11y-local\n"
        "SYMGOV_S3_ACCESS_KEY_ID=local\nSYMGOV_S3_SECRET_ACCESS_KEY=local\n", encoding="utf-8"
    )
    for key in list(os.environ):
        if key.endswith(("_API_KEY", "_SECRET_KEY")) or key.startswith(("OPENROUTER", "GEMINI", "LANGFUSE", "AGENTMAIL")):
            os.environ.pop(key)
    os.environ.update({
        "SYMGOV_ENVIRONMENT": "local",
        "SYMGOV_DATABASE_URL": args.database_url,
        "SYMGOV_MIGRATION_DATABASE_URL": args.database_url,
        "SYMGOV_HERMES_ENV_FILE": str(state / "empty.env"),
        "SYMGOV_HERMES_HOST_OPENCLAW_ROOT": str(state / "sandbox"),
        "SYMGOV_HERMES_CONTAINER_OPENCLAW_ROOT": str(state / "sandbox"),
        "SYMGOV_EMAIL_TRANSPORT": "smtp",
        "SYMGOV_SMTP_HOST": "",
        "SYMGOV_ENABLE_AGENT_WORKERS": "0",
        "SYMGOV_ENABLE_LIBBY_WORKER": "0",
        "SYMGOV_ED_PILOT_ORGANIZATION_CODES": "",
        "SYMGOV_ED_SEMANTIC_SEARCH_ENABLED": "0",
        "SYMGOV_LLM_TELEMETRY_ENABLED": "0",
        **{flag: "1" for flag in FLAGS},
    })
    if getattr(args, "web_origin", None):
        os.environ["SYMGOV_CSRF_TRUSTED_ORIGINS"] = args.web_origin
        os.environ["SYMGOV_CSRF_TRUSTED_HOSTS"] = urlsplit(args.web_origin).hostname
    for path in (REPO_ROOT / "backend", REPO_ROOT / "tests"):
        sys.path.insert(0, str(path))

    from symgov_backend import db, runtime

    db.DEFAULT_ENV_FILE = state / "db.env"
    runtime.DEFAULT_STORAGE_ENV_FILE = state / "storage.env"
    return state


class DiskBucket:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, object_key: str) -> Path:
        path = (self.root / object_key.lstrip("/")).resolve()
        if self.root not in path.parents:
            raise RuntimeError("Object key escapes the local bucket")
        return path

    def put(self, *, object_key, payload, content_type, env_file=None):
        path = self._path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        path.with_name(path.name + ".content-type").write_text(content_type, encoding="utf-8")
        return {"object_key": object_key}

    def get(self, *, object_key, env_file=None, max_bytes=None):
        path = self._path(object_key)
        if not path.is_file():
            raise RuntimeError("Storage download failed with HTTP 404")
        meta = path.with_name(path.name + ".content-type")
        content_type = meta.read_text(encoding="utf-8") if meta.is_file() else "application/octet-stream"
        return {"object_key": object_key, "payload": path.read_bytes(), "content_type": content_type}


def _redirect_production_paths(state: Path, bucket: DiskBucket) -> list[str]:
    """Point every module-level production path into the sandbox and storage at the disk bucket."""
    redirected = []
    sandbox = state / "sandbox"
    for name, module in list(sys.modules.items()):
        if not name.startswith("symgov_backend") or module is None:
            continue
        for attr, value in list(vars(module).items()):
            if isinstance(value, Path) and str(value).startswith(PRODUCTION_PREFIXES):
                setattr(module, attr, sandbox / str(value).lstrip("/"))
                redirected.append(f"{name}.{attr}")
            elif attr == "download_object_bytes" and callable(value):
                setattr(module, attr, bucket.get)
            elif attr == "upload_object_bytes" and callable(value):
                setattr(module, attr, bucket.put)
    return redirected


def _publish_pilot(engine, actor_id):
    """The DEXPI pilot symbol the DISC import links to, as tests/test_disc_dexpi_ingest_postgresql.py seeds it."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from symgov_backend.catalog_facets import compute_catalog_facets
    from symgov_backend.semantic_concepts import create_semantic_concept, transition_semantic_concept_revision

    with Session(engine) as session, session.begin():
        concept, revision = create_semantic_concept(
            session, concept_kind="physical_equipment", preferred_name="CentrifugalPump",
            definition="The pilot's concept.", created_by_user_id=actor_id, created_at=NOW,
        )
        for target_state in ("review", "approved", "published"):
            transition_semantic_concept_revision(session, revision.id, target_state=target_state, actor_id=actor_id, occurred_at=NOW)
        concept_code = concept.concept_code
    symbol_id, revision_id, page_id, entry_id, pack_id = (uuid.uuid4() for _ in range(5))
    payload = {
        "name": "ShellAndTubeHeatExchanger (HEX 1)",
        "summary": "ShellAndTubeHeatExchanger (HEX 1), converted from the DEXPI 1.2 example corpus.",
        "description": "A heat exchanger as drawn in the DEXPI TrainingTestCases corpus.",
        "keywords": ["DEXPI", "HeatExchanger"],
        "dexpi": {
            "name_basis": "dexpi_component_class", "component_classes": ["CentrifugalPump"],
            "attribution": "Local a11y fixture attribution.",
            "semantic_concept": {"concept_key": "CentrifugalPump", "concept_code": concept_code},
        },
    }
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO publication_packs (id,pack_code,title,audience,effective_date,status,created_at,updated_at) "
            "VALUES (:id,'dexpi-ttc-1-2-1-3','DEXPI pilot','public',DATE '2026-09-24','published',:now,:now)"
        ), {"id": pack_id, "now": NOW})
        connection.execute(text(
            "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,visibility,created_at,updated_at) "
            "VALUES (:id,:slug,'ShellAndTubeHeatExchanger (HEX 1)','Equipment','Piping / P&ID',:owner,'public',:now,:now)"
        ), {"id": symbol_id, "slug": "dexpi-ttc-" + "ab" * 16, "owner": actor_id, "now": NOW})
        connection.execute(text(
            "INSERT INTO symbol_revisions (id,symbol_id,revision_label,lifecycle_state,payload_json,author_id,created_at) "
            "VALUES (:id,:symbol,'r1','published',CAST(:payload AS jsonb),:owner,:now)"
        ), {"id": revision_id, "symbol": symbol_id, "owner": actor_id, "now": NOW, "payload": json.dumps(payload)})
        connection.execute(text("UPDATE governed_symbols SET current_revision_id=:r WHERE id=:s"), {"r": revision_id, "s": symbol_id})
        connection.execute(text(
            "INSERT INTO catalog_symbol_identifiers (identifier,role,governed_symbol_id,allocation_source,allocated_at) "
            "VALUES ('TST-1','canonical',:s,'global_sequence',now())"
        ), {"s": symbol_id})
        connection.execute(text("UPDATE governed_symbols SET catalog_symbol_id='TST-1' WHERE id=:s"), {"s": symbol_id})
        connection.execute(text(
            "INSERT INTO published_pages (id,page_code,title,pack_id,current_symbol_revision_id,effective_date,created_at,updated_at) "
            "VALUES (:id,'PAGE-TST-1','HEX',:pack,:r,DATE '2026-09-24',:now,:now)"
        ), {"id": page_id, "pack": pack_id, "r": revision_id, "now": NOW})
        connection.execute(text(
            "INSERT INTO pack_entries (id,pack_id,symbol_revision_id,published_page_id,sort_order,created_at) "
            "VALUES (:id,:pack,:r,:page,1,:now)"
        ), {"id": entry_id, "pack": pack_id, "r": revision_id, "page": page_id, "now": NOW})
    with Session(engine) as session, session.begin():
        compute_catalog_facets(session, [revision_id])


def seed(args) -> None:
    state = _isolate(args)
    bucket = DiskBucket((state / "objects").resolve())

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    from disc_dexpi_fixtures import build_package, write_package
    from symgov_backend import disc_dexpi_ingest as ingest
    from symgov_backend.models import RightsRecord
    from symgov_backend.rights_provenance import transition_rights_record
    from symgov_backend.services import disc_dexpi_ingestion as plan_module
    from test_semantic_review_routes_postgresql import _bind_member, _organization, _user
    from test_symbol_details_postgresql import _insert_public_symbol

    _redirect_production_paths(state, bucket)
    engine = create_engine(args.database_url)
    Session_ = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    actor_id = _user(Session_, email="a11y-importer@example.test")
    _publish_pilot(engine, actor_id)
    plan_module.EXPECTED_OVERLAP_CLASSES = ("CentrifugalPump",)
    directory = write_package(build_package(), state / "disc-package")
    package = ingest.load_package(directory)
    plan = plan_module.plan_import(package)
    with Session(engine) as session, session.begin():
        ingest.apply(session, plan=plan, package_data=package, actor_id=actor_id, occurred_at=NOW)
    with Session(engine) as session:
        ingest.upload_assets(session, plan, package, storage_env_file=None, dry_run=False, uploader=bucket.put)
        session.rollback()
    with Session(engine) as session, session.begin():
        record = session.query(RightsRecord).filter_by(decision_status="proposed").one()
        transition_rights_record(
            session, record.id, target_status="approved", occurred_at=NOW,
            decided_by_user_id=actor_id, decision_reason="Local accessibility fixture.",
        )
    with Session(engine) as session, session.begin():
        report = ingest.publish(session, plan=plan, actor_id=actor_id, occurred_at=NOW,
                                storage_env_file=None, dry_run=False, fetcher=bucket.get)
    _insert_public_symbol(
        engine, actor_id, slug="legacy-intake-hydrant", catalog_id="LEG-1",
        payload={"name": "Legacy Hydrant", "summary": "A hydrant from the intake pipeline.",
                 "classification": {"discipline": "Fire & Life Safety", "category": "Equipment"}},
        pack_code="legacy-intake", revision_at=NOW - timedelta(days=30), page_at=NOW - timedelta(days=29),
    )

    _user(Session_, email=READER_EMAIL)
    _user(Session_, email=STAFF_EMAIL, roles=("admin", "reviewer", "submitter"))
    org_admin_id = _user(Session_, email=ORG_ADMIN_EMAIL)
    with Session_() as session:
        acme = _organization(session, code="acme", now=NOW, active=True)
        _bind_member(session, organization=acme, user_id=org_admin_id, now=NOW, base_role="admin")
        session.commit()
    engine.dispose()
    print(json.dumps({
        "published": {item["slug"]: item["catalog_symbol_id"] for item in report["published"]},
        "others": ["TST-1", "LEG-1"],
        "accounts": {"reader": READER_EMAIL, "staff": STAFF_EMAIL, "orgAdmin": ORG_ADMIN_EMAIL, "pin": PIN},
    }, indent=2))


def serve(args) -> None:
    state = _isolate(args)
    bucket = DiskBucket((state / "objects").resolve())

    import uvicorn

    from symgov_backend.app import create_app

    redirected = _redirect_production_paths(state, bucket)
    print(json.dumps({"service": "symgov-api (local a11y)", "port": args.port, "redirectedPaths": len(redirected)}), flush=True)
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("seed", "serve"):
        command = sub.add_parser(name)
        command.add_argument("--database-url", required=True, help="postgresql+psycopg://... on 127.0.0.1 only")
        command.add_argument("--state-dir", required=True, help="Scratch directory for env files and local objects")
        if name == "serve":
            command.add_argument("--port", type=int, default=8031)
            command.add_argument("--web-origin", default="http://localhost:8030", help="Origin the browser uses")
    args = parser.parse_args()
    _require_local(args.database_url)
    seed(args) if args.command == "seed" else serve(args)


if __name__ == "__main__":
    main()
