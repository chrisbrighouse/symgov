"""Visual match core: imaging, descriptor, rerank, grouping. Synthetic shapes only; no catalog, no network."""
import io

import numpy as np
import pytest
from PIL import Image, ImageDraw

from symgov_backend.visual_match.descriptor import cosine_shortlist, descriptor
from symgov_backend.visual_match.errors import VisualMatchInputError
from symgov_backend.visual_match.grouping import group_members, interchangeable, largest_unmatched_run
from symgov_backend.visual_match.imaging import SIZE_FINE, SIZE, decode_gray, normalize, rotated
from symgov_backend.visual_match.rerank import ROT_PENALTY, rerank


def draw(kind: str, *, w: int = 3, side: int = 256, turn: int = 0, mirror: bool = False) -> np.ndarray:
    im = Image.new("L", (side, side), 255)
    d = ImageDraw.Draw(im)
    m = side // 8
    if kind == "circle_cross":
        d.ellipse([m, m, side - m, side - m], outline=0, width=w)
        d.line([m, m, side - m, side - m], fill=0, width=w)
        d.line([m, side - m, side - m, m], fill=0, width=w)
    elif kind == "triangle":
        d.polygon([(side // 2, m), (side - m, side - m), (m, side - m)], outline=0, width=w)
    elif kind == "bowtie":
        d.line([m, m, side - m, side - m], fill=0, width=w)
        d.line([m, side - m, side - m, m], fill=0, width=w)
        d.line([m, m, m, side - m], fill=0, width=w)
        d.line([side - m, m, side - m, side - m], fill=0, width=w)
    elif kind == "arrow_right":
        d.line([m, side // 2, side - m, side // 2], fill=0, width=w)
        d.polygon([(side - m, side // 2), (side - 3 * m, side // 2 - m), (side - 3 * m, side // 2 + m)], outline=0, width=w)
    elif kind == "tee":
        d.line([m, m, side - m, m], fill=0, width=w)
        d.line([side // 2, m, side // 2, side - m], fill=0, width=w)
    a = np.asarray(im)
    if mirror:
        a = a[:, ::-1]
    return np.ascontiguousarray(np.rot90(a, turn))


def norm(gray, size=SIZE):
    n = normalize(gray, size=size)
    assert n is not None
    return n


KINDS = ["circle_cross", "triangle", "bowtie", "arrow_right", "tee"]


def test_decode_rejects_bad_inputs():
    with pytest.raises(VisualMatchInputError) as e:
        decode_gray(b"not an image")
    assert e.value.code == "unreadable_image"
    buf = io.BytesIO(); Image.new("L", (64, 64), 255).save(buf, format="GIF")
    with pytest.raises(VisualMatchInputError) as e:
        decode_gray(buf.getvalue())
    assert e.value.code == "unsupported_format"
    buf = io.BytesIO(); Image.new("L", (4, 4), 255).save(buf, format="PNG")
    with pytest.raises(VisualMatchInputError) as e:
        decode_gray(buf.getvalue())
    assert e.value.code == "image_too_small"
    with pytest.raises(VisualMatchInputError) as e:
        decode_gray(b"x" * 100, max_bytes=10)
    assert e.value.code == "image_too_large"
    buf = io.BytesIO(); Image.new("L", (200, 200), 255).save(buf, format="PNG")
    with pytest.raises(VisualMatchInputError) as e:
        decode_gray(buf.getvalue(), max_pixels=1000)
    assert e.value.code == "image_too_large"


def test_decode_composites_transparency_on_white():
    buf = io.BytesIO(); Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(buf, format="PNG")
    assert decode_gray(buf.getvalue()).min() == 255


def test_blank_image_has_no_line_work():
    assert normalize(np.full((100, 100), 255, np.uint8)) is None


def test_line_weight_invariance():
    thin, thick = norm(draw("circle_cross", w=2)), norm(draw("circle_cross", w=9))
    other = norm(draw("triangle", w=9))
    same = rerank(thin, [thick])[0].chamfer
    assert same < 2.5 and same < rerank(thin, [other])[0].chamfer / 2


def test_scale_invariance_and_self_match_ranks_first():
    q = norm(draw("triangle", side=400))
    cands = [norm(draw(k, side=256)) for k in KINDS]
    # candidates passed in a stage-1 order that puts the right answer last: the rerank must still find it
    order = [i for i in range(len(KINDS)) if KINDS[i] != "triangle"] + [KINDS.index("triangle")]
    res = rerank(q, [cands[i] for i in order])
    assert KINDS[order[res[0].index]] == "triangle" or res[0].index == len(order) - 1


def test_rotated_query_matches_with_rotation_penalty():
    base = draw("arrow_right"); turned = draw("arrow_right", turn=1)
    r = rerank(norm(turned), [norm(base)])[0]
    assert r.rotation in {1, 3} and r.chamfer < 1.5
    assert r.score >= ROT_PENALTY  # non-identity orientation is penalised, so an upright twin would outrank it


def test_orientation_only_entries_stay_distinct():
    a, b = norm(draw("arrow_right")), norm(draw("arrow_right", turn=2))
    assert not interchangeable(a, b, norm(draw("arrow_right"), SIZE_FINE), norm(draw("arrow_right", turn=2), SIZE_FINE))


def test_mirror_is_never_used():
    # an asymmetric glyph mirrored is not matched at identity; a rotation cannot fake a mirror here
    base = norm(draw("arrow_right")); mir = norm(draw("arrow_right", mirror=True))
    assert rerank(mir, [base])[0].rotation != 0


def test_rerank_deterministic_and_ties_keep_stage1_order():
    q = norm(draw("tee"))
    c = norm(draw("tee"))
    r1 = rerank(q, [c, c, c]); r2 = rerank(q, [c, c, c])
    assert r1 == r2 and [r.index for r in r1] == [0, 1, 2]


def test_stage1_rank_term_breaks_near_ties():
    q = norm(draw("tee"))
    c = norm(draw("tee"))
    res = rerank(q, [c, c])
    assert res[0].score < res[1].score


def test_descriptor_is_unit_and_discriminates():
    d = {k: descriptor(norm(draw(k))) for k in KINDS}
    assert all(abs(np.linalg.norm(v) - 1) < 1e-5 for v in d.values())
    m = np.stack([d[k] for k in KINDS])
    idx, sims = cosine_shortlist([d["bowtie"]], m, 3)
    assert KINDS[idx[0]] == "bowtie" and sims[0] > sims[1]


def test_shortlist_stable_on_ties():
    v = descriptor(norm(draw("tee")))
    idx, _ = cosine_shortlist([v], np.stack([v, v, v]), 3)
    assert list(idx) == [0, 1, 2]


def test_grouping_exact_copies_group_and_small_marks_split():
    plain = draw("circle_cross"); marked = plain.copy()
    im = Image.fromarray(marked); ImageDraw.Draw(im).line([100, 20, 100, 60], fill=0, width=3)  # a 40 px diagnostic mark
    marked = np.asarray(im)
    items = [(norm(g), norm(g, SIZE_FINE)) for g in (plain, plain.copy(), marked, draw("triangle"))]
    groups = group_members(items)
    assert [0, 1] in groups
    assert [2] in groups and [3] in groups
    assert largest_unmatched_run(items[0][1], items[2][1]) > 0.03


def test_rotation_helper_is_a_quarter_turn():
    n = norm(draw("arrow_right"))
    assert (rotated(rotated(n, 1), 3).skeleton == n.skeleton).all()
