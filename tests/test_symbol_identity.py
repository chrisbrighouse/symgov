from __future__ import annotations

from types import SimpleNamespace
import uuid


def test_governed_symbol_human_readable_id_uses_existing_identity_contract():
    from symgov_backend.symbol_identity import governed_symbol_human_readable_id

    revision_id = uuid.uuid4()
    revision = SimpleNamespace(payload_json={
        "package_display_id": "ABCD",
        "package_symbol_sequence": 7,
    })
    session = SimpleNamespace(get=lambda model, identifier: revision if identifier == revision_id else None)

    public_symbol = SimpleNamespace(catalog_symbol_id="0003-12", current_revision_id=None, slug="public-slug")
    private_symbol = SimpleNamespace(catalog_symbol_id=None, current_revision_id=revision_id, slug=f"org-draft-{uuid.uuid4()}")
    unidentified_private_symbol = SimpleNamespace(
        catalog_symbol_id=None,
        current_revision_id=uuid.uuid4(),
        slug=f"org-draft-{uuid.uuid4()}",
    )

    assert governed_symbol_human_readable_id(session, public_symbol) == "0003-12"
    assert governed_symbol_human_readable_id(session, private_symbol) == "ABCD-7"
    assert governed_symbol_human_readable_id(session, unidentified_private_symbol) is None
