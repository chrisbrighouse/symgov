from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
import uuid
from zipfile import ZipFile

from fastapi.testclient import TestClient

from symgov_backend.app import create_app
from symgov_backend.auth import AuthenticatedUser
from symgov_backend.catalog_api_auth import hash_api_key
from symgov_backend.dependencies import get_db_session
from symgov_backend.models import CatalogApiKey, CatalogApiUsageEvent
from symgov_backend.routes import catalog as catalog_routes


class _CatalogApiKeyQuery:
    def __init__(self, rows):
        self.rows = rows
        self.criteria = []

    def filter(self, *criteria):
        self.criteria.extend(criteria)
        return self

    def one_or_none(self):
        compiled = "\n".join(str(item.compile(compile_kwargs={"literal_binds": True})) for item in self.criteria)
        return next((row for row in self.rows if row.key_hash in compiled), None)


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _DownloadSession:
    def __init__(self, api_key, symbols):
        self.api_key = api_key
        self.symbols = symbols
        self.commits = 0
        self.rollbacks = 0
        self.added = []

    def query(self, model):
        assert model is CatalogApiKey
        return _CatalogApiKeyQuery([self.api_key])

    def execute(self, statement, params=None):
        symbol_ref = str((params or {}).get("symbol_ref") or "")
        row = self.symbols.get(symbol_ref)
        return _Rows([row] if row else [])

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def add(self, value):
        self.added.append(value)


def _api_key(token="download-token"):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return SimpleNamespace(
        id=uuid.uuid4(),
        customer_name="Acme Engineering",
        integration_name="CAD integration",
        key_prefix="symgov_live_test",
        key_hash=hash_api_key(token),
        scopes_json=["catalog.read"],
        status="active",
        expires_at=now + timedelta(days=1),
        revoked_at=None,
        last_used_at=None,
    )


def _symbol(*, slug, name, display_id, object_key, format_="PNG"):
    package_id, sequence = display_id.split("-", 1)
    return SimpleNamespace(
        symbol_id=str(uuid.uuid4()),
        slug=slug,
        canonical_name=name,
        category="symbol",
        discipline="Electrical",
        symbol_revision_id=str(uuid.uuid4()),
        revision_label="A",
        revision_created_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        payload_json={
            "name": name,
            "package_display_id": package_id,
            "package_symbol_sequence": int(sequence),
            "downloads": [
                {
                    "object_key": object_key,
                    "filename": f"source.{format_.lower()}",
                    "format": format_,
                    "downloadable": True,
                }
            ],
        },
        rationale="Approved",
        page_id=str(uuid.uuid4()),
        page_code="P-1",
        page_title=name,
        effective_date=datetime(2026, 7, 20, tzinfo=timezone.utc),
        page_updated_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        pack_id=str(uuid.uuid4()),
        pack_code=package_id,
        pack_title="Pack",
        pack_effective_date=datetime(2026, 7, 20, tzinfo=timezone.utc),
        sort_order=1,
        last_updated_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )


def test_catalog_download_api_returns_single_symbol_with_catalog_filename(monkeypatch):
    symbol = _symbol(
        slug="rotating-motor",
        name="Rotating Motor",
        display_id="00023-3",
        object_key="symbols/motor.png",
    )
    session = _DownloadSession(_api_key(), {"rotating-motor": symbol})
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: session
    monkeypatch.setattr(
        catalog_routes,
        "download_object_bytes",
        lambda *, object_key, env_file: {"payload": b"png-bytes", "content_type": "image/png"},
    )

    response = TestClient(app).post(
        "/api/v1/catalog/symbols/download",
        headers={"Authorization": "Bearer download-token"},
        json={"symbolIds": ["rotating-motor"], "format": "PNG"},
    )

    assert response.status_code == 200
    assert response.content == b"png-bytes"
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["content-disposition"] == 'attachment; filename="Rotating Motor (00023-3).png"'
    assert response.headers["x-symgov-selected-count"] == "1"
    assert response.headers["x-symgov-downloaded-count"] == "1"
    assert response.headers["x-symgov-skipped-symbols"] == ""
    assert any(
        isinstance(event, CatalogApiUsageEvent)
        and event.route_name == "catalog_symbol_download"
        and event.result_count == 1
        for event in session.added
    )


def test_catalog_download_api_returns_timestamped_format_zip_and_reports_skips(monkeypatch):
    motor = _symbol(
        slug="rotating-motor",
        name="Rotating Motor",
        display_id="00023-3",
        object_key="symbols/motor.png",
    )
    pump = _symbol(
        slug="centrifugal-pump",
        name="Centrifugal Pump",
        display_id="00023-4",
        object_key="symbols/pump.png",
    )
    valve = _symbol(
        slug="gate-valve",
        name="Gate Valve",
        display_id="00023-5",
        object_key="symbols/valve.svg",
        format_="SVG",
    )
    session = _DownloadSession(
        _api_key(),
        {
            "rotating-motor": motor,
            "centrifugal-pump": pump,
            "gate-valve": valve,
        },
    )
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: session
    stored_bytes = {
        "symbols/motor.png": b"motor-png",
        "symbols/pump.png": b"pump-png",
    }
    monkeypatch.setattr(
        catalog_routes,
        "download_object_bytes",
        lambda *, object_key, env_file: {
            "payload": stored_bytes[object_key],
            "content_type": "image/png",
        },
    )
    monkeypatch.setattr(
        catalog_routes,
        "catalog_download_now",
        lambda: datetime(2026, 7, 20, 14, 30, 12, tzinfo=timezone.utc),
        raising=False,
    )

    response = TestClient(app).post(
        "/api/v1/catalog/symbols/download",
        headers={"Authorization": "Bearer download-token"},
        json={
            "symbolIds": ["rotating-motor", "centrifugal-pump", "gate-valve"],
            "format": "png",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert response.headers["content-disposition"] == (
        'attachment; filename="symgov-png-20260720-143012.zip"'
    )
    assert response.headers["x-symgov-selected-count"] == "3"
    assert response.headers["x-symgov-downloaded-count"] == "2"
    assert response.headers["x-symgov-skipped-symbols"] == "00023-5"
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == [
            "Rotating Motor (00023-3).png",
            "Centrifugal Pump (00023-4).png",
        ]
        assert archive.read("Rotating Motor (00023-3).png") == b"motor-png"
        assert archive.read("Centrifugal Pump (00023-4).png") == b"pump-png"


def test_catalog_download_api_accepts_the_interactive_user_session(monkeypatch):
    symbol = _symbol(
        slug="rotating-motor",
        name="Rotating Motor",
        display_id="00023-3",
        object_key="symbols/motor.png",
    )
    session = _DownloadSession(_api_key(), {"rotating-motor": symbol})
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: session
    monkeypatch.setattr(
        catalog_routes,
        "current_user_from_token",
        lambda current_session, token: AuthenticatedUser(
            id=str(uuid.uuid4()),
            email="catalog.user@example.com",
            display_name="Catalog User",
            roles=("reviewer",),
            must_change_pin=False,
        ) if token == "browser-session" else None,
        raising=False,
    )
    monkeypatch.setattr(
        catalog_routes,
        "download_object_bytes",
        lambda *, object_key, env_file: {"payload": b"png-bytes", "content_type": "image/png"},
    )

    client = TestClient(app)
    client.cookies.set("symgov_session", "browser-session")
    response = client.post(
        "/api/v1/catalog/symbols/download",
        json={"symbolIds": ["rotating-motor"], "format": "PNG"},
    )

    assert response.status_code == 200
    assert response.content == b"png-bytes"


def test_catalog_download_filename_sanitizes_path_and_header_characters():
    assert catalog_routes.catalog_symbol_download_filename(
        'Motor / Pump: "Duty"',
        "00023-9",
        "PNG",
    ) == "Motor - Pump- Duty (00023-9).png"
    assert catalog_routes.catalog_symbol_download_filename(
        "Motor", "00023-9", "../../evil"
    ) == "Motor (00023-9).bin"
    assert catalog_routes.catalog_download_header_token("00023-9\r\nX-Evil: yes") == "00023-9--X-Evil--yes"


def test_catalog_download_content_disposition_supports_unicode_names():
    assert catalog_routes.catalog_download_content_disposition("旋转电机 (00023-3).png") == (
        "attachment; filename=\"____ (00023-3).png\"; "
        "filename*=UTF-8''%E6%97%8B%E8%BD%AC%E7%94%B5%E6%9C%BA%20%2800023-3%29.png"
    )


def test_catalog_download_api_rejects_more_than_ten_symbols():
    session = _DownloadSession(_api_key(), {})
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: session

    response = TestClient(app).post(
        "/api/v1/catalog/symbols/download",
        headers={"Authorization": "Bearer download-token"},
        json={"symbolIds": [f"symbol-{index}" for index in range(11)], "format": "PNG"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Select 1 to 10 symbols and one format."


def test_catalog_download_api_rejects_aliases_for_the_same_resolved_symbol(monkeypatch):
    symbol = _symbol(
        slug="rotating-motor",
        name="Rotating Motor",
        display_id="00023-3",
        object_key="symbols/motor.png",
    )
    session = _DownloadSession(
        _api_key(),
        {
            "rotating-motor": symbol,
            "00023-3": symbol,
        },
    )
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: session
    monkeypatch.setattr(
        catalog_routes,
        "download_object_bytes",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("Storage must not be called.")),
    )

    response = TestClient(app).post(
        "/api/v1/catalog/symbols/download",
        headers={"Authorization": "Bearer download-token"},
        json={"symbolIds": ["rotating-motor", "00023-3"], "format": "PNG"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Each selected symbol must be unique."


def test_catalog_download_api_rejects_unknown_fields_and_overlong_refs():
    session = _DownloadSession(_api_key(), {})
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: session
    client = TestClient(app)
    headers = {"Authorization": "Bearer download-token"}

    unknown = client.post(
        "/api/v1/catalog/symbols/download",
        headers=headers,
        json={"symbolIds": ["0003-12"], "format": "PNG", "properties": True},
    )
    overlong = client.post(
        "/api/v1/catalog/symbols/download",
        headers=headers,
        json={"symbolIds": ["x" * 257], "format": "PNG"},
    )
    malicious_format = client.post(
        "/api/v1/catalog/symbols/download",
        headers=headers,
        json={"symbolIds": ["0003-12"], "format": "../../evil\r\nX-Evil: yes"},
    )

    assert unknown.status_code == 400
    assert overlong.status_code == 400
    assert malicious_format.status_code == 400


def test_catalog_download_api_errors_when_format_is_unavailable_for_every_symbol(monkeypatch):
    valve = _symbol(
        slug="gate-valve",
        name="Gate Valve",
        display_id="00023-5",
        object_key="symbols/valve.svg",
        format_="SVG",
    )
    session = _DownloadSession(_api_key(), {"gate-valve": valve})
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: session
    monkeypatch.setattr(
        catalog_routes,
        "download_object_bytes",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("Storage must not be called.")),
    )

    response = TestClient(app).post(
        "/api/v1/catalog/symbols/download",
        headers={"Authorization": "Bearer download-token"},
        json={"symbolIds": ["gate-valve"], "format": "PNG"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "The selected format is not available for any selected symbol."
