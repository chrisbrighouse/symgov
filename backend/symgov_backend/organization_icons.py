from __future__ import annotations

import hashlib
import io
import re
import uuid
from typing import NamedTuple

from PIL import Image


FALLBACK_ICON_ALGORITHM_VERSION = "v1"
FALLBACK_ICON_SEED = "symgov.organization-fallback-icon.seed.v1"
MAX_FALLBACK_ICON_BYTES = 512

_DOMAIN = "symgov.organization-fallback-icon"
_COLOR_MASK = (0xB4, 0xDD, 0x68)

# --- Custom icon upload (Stage 3, icon upload slice) ---
#
# SVG is never accepted: the accepted pipeline contract (spec I-12) requires a
# vetted scan/parse/sanitize/rasterize pipeline before any SVG can be served,
# and no such dependency is available in this environment. Raster-only upload
# through Pillow (already a pinned dependency) satisfies the same floor: no
# untrusted SVG is ever served.
MAX_ICON_UPLOAD_BYTES = 512 * 1024
MIN_ICON_DIMENSION_PX = 32
MAX_ICON_DIMENSION_PX = 1024
MAX_ICON_DECODED_PIXELS = MAX_ICON_DIMENSION_PX * MAX_ICON_DIMENSION_PX
NORMALIZED_ICON_CONTENT_TYPE = "image/png"
ICON_UPLOAD_MIN_INTERVAL_SECONDS = 5
# Uploads travel as base64 JSON (the shared cookie-mutation CSRF guard requires
# application/json bodies); bound the encoded text before decoding it.
MAX_ICON_UPLOAD_BASE64_CHARS = ((MAX_ICON_UPLOAD_BYTES // 3) + 1) * 4

# Declared content type -> the exact Pillow format name it must sniff as.
ALLOWED_ICON_UPLOAD_CONTENT_TYPES = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/webp": "WEBP",
}


class IconUploadError(ValueError):
    """Raised for a rejected icon upload; the message is safe to return to the client."""


class NormalizedIcon(NamedTuple):
    png_bytes: bytes
    width: int
    height: int
    checksum_sha256: str


def validate_and_normalize_icon_upload(payload: bytes, declared_content_type: str) -> NormalizedIcon:
    """Validate an uploaded organization icon and return a normalized PNG derivative.

    Rejects anything that is not a small, well-formed raster image whose
    declared content type matches its sniffed format, and bounds dimensions to
    guard against decompression-bomb-style pixel counts before any full decode.
    """
    if not payload:
        raise IconUploadError("Icon upload is empty.")
    if len(payload) > MAX_ICON_UPLOAD_BYTES:
        raise IconUploadError(f"Icon upload exceeds the {MAX_ICON_UPLOAD_BYTES}-byte limit.")

    declared = (declared_content_type or "").split(";", 1)[0].strip().lower()
    if declared not in ALLOWED_ICON_UPLOAD_CONTENT_TYPES:
        if declared == "image/svg+xml":
            raise IconUploadError(
                "SVG icon upload is not supported; upload a PNG, JPEG, or WEBP raster image."
            )
        raise IconUploadError("Unsupported icon content type.")

    try:
        with Image.open(io.BytesIO(payload)) as probe:
            probe.verify()
    except Exception as exc:
        raise IconUploadError("Icon upload is not a valid image.") from exc

    try:
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != ALLOWED_ICON_UPLOAD_CONTENT_TYPES[declared]:
                raise IconUploadError("Declared content type does not match the uploaded image.")
            width, height = image.size
            if width * height > MAX_ICON_DECODED_PIXELS:
                raise IconUploadError("Icon image is too large to process.")
            if not (MIN_ICON_DIMENSION_PX <= width <= MAX_ICON_DIMENSION_PX) or not (
                MIN_ICON_DIMENSION_PX <= height <= MAX_ICON_DIMENSION_PX
            ):
                raise IconUploadError(
                    "Icon dimensions must be between "
                    f"{MIN_ICON_DIMENSION_PX} and {MAX_ICON_DIMENSION_PX} pixels."
                )
            # Re-encoding pixel data into a fresh image drops EXIF/XMP/ICC
            # profiles and any other ancillary chunks from the source file.
            normalized = image.convert("RGBA")
            clean = Image.new("RGBA", normalized.size)
            clean.putdata(list(normalized.get_flattened_data()))
            out = io.BytesIO()
            clean.save(out, format="PNG", optimize=True)
            png_bytes = out.getvalue()
    except IconUploadError:
        raise
    except Exception as exc:
        raise IconUploadError("Icon upload could not be processed.") from exc

    checksum = hashlib.sha256(png_bytes).hexdigest()
    return NormalizedIcon(png_bytes=png_bytes, width=width, height=height, checksum_sha256=checksum)


def build_organization_icon_object_key(organization_id: uuid.UUID | str, checksum_sha256: str) -> str:
    """Return the non-user-controlled, checksum-versioned storage key for an icon."""
    org_uuid = uuid.UUID(str(organization_id))
    digest = str(checksum_sha256).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("checksum_sha256 must be a 64-character lowercase hex SHA-256 digest.")
    return f"organization-icons/{org_uuid}/{digest}.png"


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
