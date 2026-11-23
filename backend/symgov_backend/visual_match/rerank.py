"""Stage 2: exact centreline rerank of the stage-1 shortlist, with the frozen benchmark score.

    score = chamfer + W_RUN * run + ROT_PENALTY * (rot != 0) + BETA * ln(stage1_rank)      (lower is better)

Frozen 2026-11-23 on the EL01 tune split (W_RUN=0, ROT_PENALTY=1.0, BETA=3). The unmatched-run
term hurt ranking in the benchmark, so it is zero here; it stays available for grouping (grouping.py).
Candidates must be passed in stage-1 order: a candidate's position is its stage-1 rank.
Mirroring is never used. Entries that differ only by orientation stay distinct (the rotation penalty).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .imaging import Normalized, rotated

W_RUN = 0.0
ROT_PENALTY = 1.0
# The benchmark chose the rotation to refine with its original 0.15 penalty and only then scored with
# ROT_PENALTY; production keeps that exact behaviour because it is what the frozen numbers measured.
ROT_SELECT_PENALTY = 0.15
BETA = 3.0
SHIFTS = [(dy, dx) for dy in (-2, -1, 0, 1, 2) for dx in (-2, -1, 0, 1, 2)]
SCALES = (0.9, 1.0, 1.1)
POSES = [(sc, dy, dx) for sc in SCALES for dy, dx in SHIFTS]
_SC = np.array([p[0] for p in POSES], np.float32)[:, None, None]
_T = np.array([[p[1], p[2]] for p in POSES], np.float32)[:, None, :]
_ISC = np.array([1.0 / p[0] for p in POSES], np.float32)[:, None, None]
_IT = np.array([[-p[1] / p[0], -p[2] / p[0]] for p in POSES], np.float32)[:, None, :]


@dataclass(frozen=True)
class Reranked:
    index: int            # position in the list passed in (stage-1 rank - 1)
    score: float
    chamfer: float
    rotation: int         # quarter turns applied to the query
    scale: float
    shift: tuple[int, int]


def _transform_all(pts: np.ndarray, size: int, inverse: bool) -> np.ndarray:
    c = (size - 1) / 2.0
    p = pts.astype(np.float32)[None]
    t = (p - c) * _ISC + c + _IT if inverse else (p - c) * _SC + c + _T
    return np.clip(np.rint(t), 0, size - 1).astype(np.int32)


def _chamfer_identity(q: Normalized, c: Normalized) -> float:
    d1 = c.dt[q.points[:, 0], q.points[:, 1]].mean()
    d2 = q.dt[c.points[:, 0], c.points[:, 1]].mean()
    return float((d1 + d2) / 2.0)


def _pose_chamfers(q: Normalized, c: Normalized, q_poses: np.ndarray) -> np.ndarray:
    size = q.skeleton.shape[0]
    d1 = c.dt[q_poses[..., 0], q_poses[..., 1]].mean(axis=1)
    cp = _transform_all(c.points, size, True)
    d2 = q.dt[cp[..., 0], cp[..., 1]].mean(axis=1)
    return (d1 + d2) / 2.0


def rerank(query: Normalized, candidates: list[Normalized], top: int = 50) -> list[Reranked]:
    rots = [rotated(query, k) for k in range(4)]
    q_poses = [_transform_all(r.points, r.skeleton.shape[0], False) for r in rots]
    out: list[Reranked] = []
    for i, cand in enumerate(candidates):
        base = [(_chamfer_identity(rots[k], cand) + (ROT_SELECT_PENALTY if k else 0.0), k) for k in range(4)]
        _, k = min(base)
        ch_all = _pose_chamfers(rots[k], cand, q_poses[k])
        j = int(np.argmin(ch_all))
        scale, dy, dx = POSES[j]
        ch = float(ch_all[j])
        score = ch + (ROT_PENALTY if k else 0.0) + BETA * float(np.log(i + 1))
        out.append(Reranked(i, round(score, 4), round(ch, 4), k, scale, (dy, dx)))
    out.sort(key=lambda r: (r.score, r.index))
    return out[:top]
