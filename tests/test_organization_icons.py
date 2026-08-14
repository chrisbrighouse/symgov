from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from xml.etree import ElementTree
import uuid

import pytest

from symgov_backend.organization_icons import (
    FALLBACK_ICON_ALGORITHM_VERSION,
    FALLBACK_ICON_SEED,
    MAX_FALLBACK_ICON_BYTES,
    generate_organization_fallback_icon,
)


ORGANIZATION_ID = uuid.UUID("12345678-1234-5678-9234-567812345678")
OTHER_ORGANIZATION_ID = uuid.UUID("87654321-4321-6789-a234-678987654321")


def test_fallback_icon_matches_stable_v1_vector():
    first = generate_organization_fallback_icon(ORGANIZATION_ID)
    second = generate_organization_fallback_icon(ORGANIZATION_ID)

    assert FALLBACK_ICON_ALGORITHM_VERSION == "v1"
    assert FALLBACK_ICON_SEED == "symgov.organization-fallback-icon.seed.v1"
    assert first == second
    assert first == (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" '
        'data-algorithm-version="v1"><rect width="64" height="64" rx="12" '
        'fill="#285d7f"/><circle cx="20" cy="22" r="12" fill="#ffffff" '
        'fill-opacity="0.72"/><circle cx="44" cy="42" r="14" fill="#ffffff" '
        'fill-opacity="0.32"/></svg>'
    )


def test_fallback_icon_is_repeatable_across_processes():
    repository_root = Path(__file__).resolve().parents[1]
    script = (
        "import uuid; "
        "from symgov_backend.organization_icons import generate_organization_fallback_icon; "
        f"print(generate_organization_fallback_icon(uuid.UUID('{ORGANIZATION_ID}')))"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repository_root / "backend")

    outputs = [
        subprocess.check_output(
            [sys.executable, "-c", script],
            cwd=repository_root,
            env=environment,
            text=True,
        ).strip()
        for _ in range(2)
    ]

    assert outputs == [generate_organization_fallback_icon(ORGANIZATION_ID)] * 2


def test_fallback_icon_separates_organization_uuids_and_seed_versions():
    default_icon = generate_organization_fallback_icon(ORGANIZATION_ID)

    assert generate_organization_fallback_icon(OTHER_ORGANIZATION_ID) != default_icon
    assert (
        generate_organization_fallback_icon(
            ORGANIZATION_ID,
            seed="symgov.organization-fallback-icon.seed.test-only",
        )
        != default_icon
    )
    with pytest.raises(ValueError, match="Unsupported fallback icon algorithm version"):
        generate_organization_fallback_icon(ORGANIZATION_ID, seed_version="v2")


def test_fallback_icon_is_bounded_valid_safe_svg():
    icon = generate_organization_fallback_icon(ORGANIZATION_ID)
    root = ElementTree.fromstring(icon)

    assert len(icon.encode("utf-8")) <= MAX_FALLBACK_ICON_BYTES
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.attrib == {
        "viewBox": "0 0 64 64",
        "data-algorithm-version": FALLBACK_ICON_ALGORITHM_VERSION,
    }
    assert len(list(root)) == 3
    assert not any(forbidden in icon.lower() for forbidden in ("script", "foreignobject", "data:"))
    assert not any(attribute.lower().endswith("href") for element in root.iter() for attribute in element.attrib)


def test_fallback_icon_is_independent_of_mutable_organization_attributes():
    organization = SimpleNamespace(
        id=ORGANIZATION_ID,
        display_name="Example Engineering",
        legal_name="Example Engineering Limited",
        code="EXAMPLE-1",
        email="owner@example.test",
    )
    pii_values = [organization.display_name, organization.legal_name, organization.code, organization.email]
    original_icon = generate_organization_fallback_icon(organization.id)

    organization.display_name = "Renamed Company"
    organization.legal_name = "Renamed Company PLC"
    organization.code = "RENAMED-1"
    organization.email = "new-owner@example.test"
    pii_values.extend(
        (organization.display_name, organization.legal_name, organization.code, organization.email)
    )
    changed_icon = generate_organization_fallback_icon(organization.id)

    assert changed_icon == original_icon
    assert all(value not in original_icon for value in pii_values)
