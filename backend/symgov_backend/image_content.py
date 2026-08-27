from __future__ import annotations


class UnsafeImageContentError(ValueError):
    pass


_IMAGE_ALIASES = {
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "image/x-png": "image/png",
}


def _normalized_media_type(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.split(";", 1)[0].strip().lower()
    return _IMAGE_ALIASES.get(normalized, normalized)


def sniff_image_media_type(payload: bytes) -> str | None:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"

    probe = payload[:4096].lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
    if probe.startswith(b"<svg"):
        return "image/svg+xml"
    if probe.startswith(b"<?xml"):
        declaration_end = probe.find(b"?>")
        if declaration_end != -1 and probe[declaration_end + 2 :].lstrip().startswith(b"<svg"):
            return "image/svg+xml"
    return None


def validate_stored_image(payload: bytes, *declared_media_types: str | None) -> str:
    sniffed = sniff_image_media_type(payload)
    if sniffed is None:
        raise UnsafeImageContentError("Stored object is not an allowed image type.")
    for declared in declared_media_types:
        normalized = _normalized_media_type(declared)
        if normalized is not None and normalized != sniffed:
            raise UnsafeImageContentError("Stored object media type does not match its bytes.")
    return sniffed


def safe_image_response_headers() -> dict[str, str]:
    return {
        "Content-Security-Policy": "sandbox",
        "X-Content-Type-Options": "nosniff",
    }
