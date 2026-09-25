"""The DXF preview backfill against a real PostgreSQL.

PostgreSQL-only because the candidates come from `active_public_symbol_projections`,
a view, and because what is proven is the chain the Catalogue relies on: the
attachment, the transformation step, the preview authorization and the refreshed
published views.

The symbol under test is published through `runtime.publish_revision_to_pack`,
the routine both publication paths share, with a payload shaped like the 17
production symbols: a DXF `source_object_key` and nothing previewable.

Redaction: this file never prints the disposable container's connection string,
and its seeded identity uses a synthetic `@example.test` email.
"""
from __future__ import annotations

import hashlib
import io
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import ezdxf
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

from symgov_backend import dxf_preview_backfill as backfill  # noqa: E402
from symgov_backend.asset_manifest import list_download_assets  # noqa: E402
from symgov_backend.models import (  # noqa: E402
    AssetTransformation,
    Attachment,
    AuditEvent,
    GovernedSymbol,
    PublicationPack,
    PublishedPreviewAuthorization,
    SymbolRevision,
)
from symgov_backend.published_catalog import (  # noqa: E402
    choose_published_preview_asset,
    published_fallback_source_asset,
)
from symgov_backend.runtime import publish_revision_to_pack  # noqa: E402

NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)
DXF_KEY = "external-submissions/test/members/0016-pump/PipeAcc_Equipment_Pump.dxf"


def _dxf_bytes() -> bytes:
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_circle((0, 0), radius=5)
    msp.add_solid([(-2, -3), (-2, 3), (3, 0)])
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


class _FakeBucket:
    def __init__(self, objects: dict[str, bytes] | None = None):
        self.objects = dict(objects or {})

    def put(self, *, object_key, payload, content_type, env_file=None):
        assert content_type == "image/svg+xml"
        self.objects[object_key] = payload

    def get(self, *, object_key, env_file=None):
        if object_key not in self.objects:
            raise RuntimeError("Storage download failed with HTTP 404")
        return {"payload": self.objects[object_key]}


@pytest.fixture(scope="module")
def database():
    with _database("symgov-dxf-preview") as (engine, url, _raw_url):
        _alembic(url, "upgrade", "head")
        yield engine


@pytest.fixture(scope="module")
def actor_id(database) -> uuid.UUID:
    identifier = uuid.uuid4()
    with database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                "must_change_pin,is_active,created_at,updated_at) "
                "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
            ),
            {"id": identifier, "email": "dxf-preview@example.test", "now": NOW},
        )
    return identifier


@pytest.fixture(scope="module")
def published(database, actor_id) -> uuid.UUID:
    """One public DXF-only symbol, published the way the Catalogue expects."""
    with Session(database) as session, session.begin():
        symbol = GovernedSymbol(
            id=uuid.uuid4(), slug="pipeacc-equipment-pump-test", canonical_name="Pump",
            category="Pumps", discipline="Piping / P&ID", owner_id=actor_id,
            owner_organization_id=None, visibility="public", organization_wide=False,
            current_revision_id=None, created_at=NOW, updated_at=NOW,
        )
        session.add(symbol)
        session.flush()
        revision = SymbolRevision(
            id=uuid.uuid4(), symbol_id=symbol.id, revision_label="1", lifecycle_state="approved",
            payload_json={"name": "Pump", "source_object_key": DXF_KEY, "source_file": "PipeAcc_Equipment_Pump.dxf"},
            rationale="test", author_id=actor_id, created_at=NOW,
        )
        session.add(revision)
        session.flush()
        symbol.current_revision_id = revision.id
        pack = PublicationPack(
            id=uuid.uuid4(), pack_code="pack-dxf-preview-test", title="DXF preview test",
            audience="public", effective_date=NOW.date(), status="published",
            created_at=NOW, updated_at=NOW,
        )
        session.add(pack)
        session.flush()
        publish_revision_to_pack(
            session, symbol=symbol, revision=revision, publication_pack=pack,
            sort_order=1, effective_date=NOW.date(), published_at=NOW,
        )
        session.execute(text("SELECT refresh_published_symbol_views()"))
        return revision.id


def _run(database, actor_id, bucket):
    with Session(database) as session, session.begin():
        candidates = backfill.find_candidates(session)
        rendered, failures = backfill.render_candidates(candidates, storage_env_file=None, fetcher=bucket.get)
        result = backfill.apply(
            session, rendered=rendered, actor_id=actor_id, occurred_at=NOW,
            storage_env_file=None, uploader=bucket.put,
        )
    return candidates, failures, result


def test_the_dxf_only_symbol_is_the_one_candidate(database, published):
    with Session(database) as session:
        candidates = backfill.find_candidates(session)
    assert [revision.id for _symbol, revision, _asset in candidates] == [published]


def test_apply_attaches_a_preview_the_catalogue_can_serve(database, actor_id, published):
    dxf = _dxf_bytes()
    bucket = _FakeBucket({DXF_KEY: dxf})
    _candidates, failures, result = _run(database, actor_id, bucket)
    assert failures == [] and result["failures"] == []
    assert len(result["recorded"]) == 1
    key = result["recorded"][0]["object_key"]
    assert key in bucket.objects

    with Session(database) as session:
        revision = session.get(SymbolRevision, published)
        payload = revision.payload_json
        # Previewed as the SVG; downloaded as the DXF, and only the DXF.
        assert choose_published_preview_asset(payload)["object_key"] == key
        downloads = list_download_assets(payload, fallback_source_asset=published_fallback_source_asset(payload))
        assert [asset["object_key"] for asset in downloads] == [DXF_KEY]
        # Display data only: the governed revision is otherwise unchanged.
        assert (revision.lifecycle_state, revision.revision_label) == ("published", "1")

        attachment = session.query(Attachment).filter_by(object_key=key).one()
        assert (attachment.parent_type, attachment.parent_id) == ("symbol_revision", published)
        assert attachment.sha256 == hashlib.sha256(bucket.objects[key]).hexdigest()

        step = session.query(AssetTransformation).filter_by(symbol_revision_id=published).one()
        assert step.step_index == 1
        assert step.source_asset_sha256 == hashlib.sha256(dxf).hexdigest()
        assert step.derived_asset_sha256 == attachment.sha256
        assert step.tool_version

        assert session.query(PublishedPreviewAuthorization).filter_by(
            symbol_revision_id=published, object_key=key
        ).one()
        event = session.query(AuditEvent).filter_by(
            entity_id=published, action="symbol_revision_preview_generated"
        ).one()
        assert event.actor_id == actor_id


def test_a_second_run_finds_nothing_to_do(database, actor_id, published):
    candidates, failures, result = _run(database, actor_id, _FakeBucket())
    assert candidates == [] and failures == [] and result["recorded"] == []
