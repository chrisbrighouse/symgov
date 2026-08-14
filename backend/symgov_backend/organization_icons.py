from __future__ import annotations

import hashlib
import uuid


FALLBACK_ICON_ALGORITHM_VERSION = "v1"
FALLBACK_ICON_SEED = "symgov.organization-fallback-icon.seed.v1"
MAX_FALLBACK_ICON_BYTES = 512

_DOMAIN = "symgov.organization-fallback-icon"
_COLOR_MASK = (0xB4, 0xDD, 0x68)


def generate_organization_fallback_icon(
    organization_id: uuid.UUID | str,
    seed_version: str = FALLBACK_ICON_ALGORITHM_VERSION,
    *,
    seed: str = FALLBACK_ICON_SEED,
) -> str:
    """Return the deterministic, PII-free fallback SVG for an organization UUID."""
    if seed_version != FALLBACK_ICON_ALGORITHM_VERSION:
        raise ValueError(f"Unsupported fallback icon algorithm version: {seed_version!r}")

    try:
        immutable_id = uuid.UUID(str(organization_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("organization_id must be a valid UUID") from exc

    try:
        seed_bytes = seed.encode("ascii")
    except (AttributeError, UnicodeEncodeError) as exc:
        raise ValueError("fallback icon seed must be non-empty ASCII") from exc
    if not seed_bytes or len(seed_bytes) > 128:
        raise ValueError("fallback icon seed must contain 1-128 ASCII bytes")

    digest_input = "|".join((_DOMAIN, seed_version, seed, str(immutable_id))).encode("ascii")
    digest = hashlib.sha256(digest_input).digest()
    color = "#" + "".join(
        f"{component ^ mask:02x}"
        for component, mask in zip(digest[:3], _COLOR_MASK, strict=True)
    )

    patterns = (
        '<rect x="14" y="14" width="16" height="36" rx="8" fill="#ffffff" '
        'fill-opacity="0.72"/><rect x="34" y="14" width="16" height="36" rx="8" '
        'fill="#ffffff" fill-opacity="0.32"/>',
        '<path d="M20 10L34 24L20 38L6 24Z" fill="#ffffff" fill-opacity="0.72"/>'
        '<path d="M44 26L58 40L44 54L30 40Z" fill="#ffffff" fill-opacity="0.32"/>',
        '<circle cx="20" cy="22" r="12" fill="#ffffff" fill-opacity="0.72"/>'
        '<circle cx="44" cy="42" r="14" fill="#ffffff" fill-opacity="0.32"/>',
    )
    icon = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" '
        f'data-algorithm-version="{FALLBACK_ICON_ALGORITHM_VERSION}">'
        f'<rect width="64" height="64" rx="12" fill="{color}"/>'
        f'{patterns[digest[3] % len(patterns)]}</svg>'
    )
    if len(icon.encode("utf-8")) > MAX_FALLBACK_ICON_BYTES:
        raise AssertionError("fallback icon exceeded its fixed output bound")
    return icon
