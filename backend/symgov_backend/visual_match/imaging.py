"""Decode and normalise an image to the one-pixel centreline form the matcher compares.

Catalog renders and query crops go through the same `normalize`. grayscale -> ink mask -> crop to
content with a margin -> pad to square (aspect kept) -> box downsample -> coverage threshold ->
skeleton -> truncated distance transform. Black ink on a light background is assumed.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, UnidentifiedImageError
from scipy.ndimage import distance_transform_edt
from skimage.morphology import skeletonize

from .errors import VisualMatchInputError

SIZE = 128
SIZE_FINE = 256
MARGIN = 0.04          # fraction of the long side
COVERAGE = 0.12        # box-filtered ink coverage that still counts as a line after downsampling
DT_CAP = 8.0           # distances beyond this (px at SIZE) count the same
INK_THRESHOLD = 250
MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 25_000_000
MIN_SIDE = 16


@dataclass(frozen=True)
class Normalized:
    skeleton: np.ndarray     # bool (n, n)
    dt: np.ndarray           # float32 (n, n): distance to the nearest skeleton pixel, capped
    points: np.ndarray       # int16 (k, 2) row/col of skeleton pixels
    content_aspect: float    # content width / height before padding


def decode_gray(data: bytes, *, max_bytes: int = MAX_BYTES, max_pixels: int = MAX_PIXELS) -> np.ndarray:
    """Decode PNG/JPEG bytes by content to an 8-bit grey array (transparency composited on white)."""
    if len(data) > max_bytes:
        raise VisualMatchInputError("image_too_large", "The image is larger than the upload limit.")
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in {"PNG", "JPEG"}:
                raise VisualMatchInputError("unsupported_format", "Only PNG and JPEG images are supported.")
            if im.width * im.height > max_pixels:
                raise VisualMatchInputError("image_too_large", "The image has too many pixels.")
            if im.mode in {"RGBA", "LA", "P"}:
                bg = Image.new("RGBA", im.size, "WHITE")
                bg.alpha_composite(im.convert("RGBA"))
                im = bg
            gray = np.asarray(im.convert("L"))
    except VisualMatchInputError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise VisualMatchInputError("unreadable_image", "The image could not be read.") from exc
    if min(gray.shape) < MIN_SIDE:
        raise VisualMatchInputError("image_too_small", "The image is too small to match.")
    return gray


def normalize(gray: np.ndarray, size: int = SIZE, ink_threshold: int = INK_THRESHOLD) -> Normalized | None:
    """None when there is no usable line work (blank or a single dot)."""
    ink = gray < ink_threshold
    rows, cols = np.nonzero(ink)
    if rows.size == 0:
        return None
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    crop = ink[r0:r1, c0:c1]
    h, w = crop.shape
    side = max(h, w)
    pad = int(round(side * MARGIN)) + 1
    canvas = np.zeros((side + 2 * pad, side + 2 * pad), dtype=np.uint8)
    top, left = pad + (side - h) // 2, pad + (side - w) // 2
    canvas[top:top + h, left:left + w] = crop.astype(np.uint8) * 255
    small = np.asarray(Image.fromarray(canvas).resize((size, size), Image.Resampling.BOX), dtype=np.float32) / 255.0
    mask = small >= COVERAGE
    if not mask.any():
        return None
    skel = skeletonize(mask)
    if skel.sum() < 2:
        return None
    dt = np.minimum(distance_transform_edt(~skel), DT_CAP).astype(np.float32)
    return Normalized(skel, dt, np.argwhere(skel).astype(np.int16), w / h)


def rotated(n: Normalized, k: int) -> Normalized:
    """The skeleton and distance field turned by k quarter turns (counter-clockwise)."""
    if k % 4 == 0:
        return n
    sk = np.ascontiguousarray(np.rot90(n.skeleton, k))
    dt = np.ascontiguousarray(np.rot90(n.dt, k))
    aspect = n.content_aspect if k % 2 == 0 else 1.0 / n.content_aspect
    return Normalized(sk, dt, np.argwhere(sk).astype(np.int16), aspect)
