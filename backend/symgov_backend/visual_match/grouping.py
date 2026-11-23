"""Interchangeable-glyph grouping (design R4, D14).

Two catalog entries are interchangeable when, at identity pose (no rotation, no mirroring),
their centrelines at 256 px match to within TAU_PX everywhere except for a largest unmatched stroke
run of at most MAX_RUN_FRACTION of the side, and a mean-chamfer prefilter at 128 px passes. Entries
that differ only by orientation are therefore never grouped. Rule set from benchmark Phase 2
(80 judged pairs; provisional: 1 error in 68 on the tuning pairs).
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import label

from .imaging import Normalized

TAU_PX = 2.0
MAX_RUN_FRACTION = 0.03
PREFILTER_MEAN_CHAMFER = 1.5


def mean_chamfer(a: Normalized, b: Normalized) -> float:
    d1 = b.dt[a.points[:, 0], a.points[:, 1]].mean()
    d2 = a.dt[b.points[:, 0], b.points[:, 1]].mean()
    return float((d1 + d2) / 2.0)


def largest_unmatched_run(a: Normalized, b: Normalized) -> float:
    """Longest connected run of stroke pixels with no counterpart within TAU_PX, as a fraction of the side."""
    size = a.skeleton.shape[0]
    worst = 0
    for src, other in ((a, b), (b, a)):
        far = other.dt[src.points[:, 0], src.points[:, 1]] > TAU_PX
        if not far.any():
            continue
        m = np.zeros((size, size), bool)
        m[src.points[far, 0], src.points[far, 1]] = True
        lab, n = label(m, structure=np.ones((3, 3)))
        if n:
            worst = max(worst, int(np.bincount(lab.ravel())[1:].max()))
    return worst / size


def interchangeable(a128: Normalized, b128: Normalized, a256: Normalized, b256: Normalized) -> bool:
    if mean_chamfer(a128, b128) >= PREFILTER_MEAN_CHAMFER:
        return False
    return largest_unmatched_run(a256, b256) <= MAX_RUN_FRACTION


def group_members(items: list[tuple[Normalized, Normalized]]) -> list[list[int]]:
    """Connected components of the interchangeable relation over (n128, n256) pairs, in first-seen order."""
    n = len(items)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if find(i) != find(j) and interchangeable(items[i][0], items[j][0], items[i][1], items[j][1]):
                parent[find(j)] = find(i)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=lambda g: g[0])
