"""Shared pieces for the tests that stand a fake session in for the database.

Since the key holder table (migration 0072), authenticating a key also reads
its holder and the holder's user. The fake sessions answer those reads with an
eligible personal holder; the policies (origin, rate limit) have their own
tests, so these suites switch them off.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

HOLDER_ID = uuid.UUID("00000000-0000-0000-0000-00000000a001")

from symgov_backend.models import CatalogApiKeyHolder, User


def eligible_get(model, ident):
    if model is CatalogApiKeyHolder:
        return SimpleNamespace(
            api_key_id=ident, key_kind="personal", holder_user_id=HOLDER_ID, organization_id=None,
            claimed_at=datetime.now(timezone.utc), revoke_reason=None,
        )
    if model is User:
        return SimpleNamespace(id=ident, is_active=True)
    return None


def switch_off_key_policies(monkeypatch):
    import symgov_backend.catalog_api_auth as auth
    import symgov_backend.routes.catalog as catalog

    def nothing(session, request, context, settings):
        return None

    monkeypatch.setattr(auth, "apply_key_policies", nothing)
    monkeypatch.setattr(catalog, "apply_key_policies", nothing)
