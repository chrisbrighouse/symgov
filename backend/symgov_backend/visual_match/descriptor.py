"""The local hand-made descriptor (~360 floats). It is the D19 fallback when the embedding call fails."""
from __future__ import annotations

import numpy as np
from skimage.measure import moments_central, moments_hu, moments_normalized

from .imaging import Normalized


def descriptor(n: Normalized) -> np.ndarray:
    sk = n.skeleton.astype(np.float32)
    s = sk.shape[0]
    grid = sk.reshape(16, s // 16, 16, s // 16).sum(axis=(1, 3)).ravel()
    pts = n.points.astype(np.float32)
    d = pts - pts.mean(axis=0)
    r = np.hypot(d[:, 0], d[:, 1])
    rn = r / (r.max() + 1e-6)
    ang = (np.arctan2(-d[:, 0], d[:, 1]) + np.pi) / (2 * np.pi)
    ra = np.zeros((8, 12), np.float32)
    np.add.at(ra, (np.minimum((rn * 8).astype(int), 7), np.minimum((ang * 12).astype(int), 11)), 1)
    hu = moments_hu(moments_normalized(moments_central(sk), 3))
    hu = -np.sign(hu) * np.log10(np.abs(hu) + 1e-12) / 12.0
    aspect = np.array([np.log(max(n.content_aspect, 1e-3)) / 3.0], np.float32)
    parts = [(grid, 1.0), (ra.ravel(), 1.0), (hu.astype(np.float32), 0.6), (aspect, 0.4)]
    v = np.concatenate([w * p / (np.linalg.norm(p) + 1e-9) for p, w in parts])
    return (v / (np.linalg.norm(v) + 1e-9)).astype(np.float32)


def cosine_shortlist(query_vectors: list[np.ndarray], matrix: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Best cosine over the query's rotations (identity wins ties). Rows of `matrix` are unit vectors."""
    sims = np.stack([matrix @ (v / (np.linalg.norm(v) + 1e-9)) for v in query_vectors])
    sims[1:] -= 1e-4
    # rounded so BLAS summation-order noise cannot reorder equal scores; the sort is stable, so ties keep
    # catalog order and results are deterministic
    best = np.round(sims.max(axis=0), 6)
    idx = np.argsort(-best, kind="stable")[:k]
    return idx, best[idx]
