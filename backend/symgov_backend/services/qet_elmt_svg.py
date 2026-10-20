"""Convert a QElectroTech .elmt element definition to a standalone SVG.

Pure and deterministic: `qet_ingestion` recomputes every SVG from the original
.elmt bytes at plan time, so a converted file can never drift from its source.
Written for the 2026-10-20 harvest (`symgov-qet-harvest-20261020/tools`), and
checked by eye against QElectroTech's own rendering of 72 elements.

Rendering follows QElectroTech's own part classes (sources/editor/graphicspart/*.cpp):
- coordinates are element-local, y down, origin at the hotspot;
- line-weight none -> transparent pen, thin -> hairline, normal 1, hight 2, eleve 5;
- arc start/angle are Qt degrees: 0 at three o'clock, positive is counter-clockwise on screen;
- static <text> x,y is the baseline-left point; font is "Family,pointSize,...";
- line ends follow PartLine::path() (simple/triangle/circle/diamond, `length` per end).

Dropped on purpose: <terminal> (connection points, kept as metadata), <dynamic_text>
with no literal text (label/info placeholders filled in per diagram) and, by product
decision (2026-10-20), the "Form 1 / Forme 1" captions QElectroTech draws on EN 60617
variants: they name the variant on QET's sheet and are not part of the symbol. Each
removed caption is reported, so the import records it as a conversion step.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

CONVERTER_NAME = "symgov_backend.services.qet_elmt_svg.convert"
CONVERTER_VERSION = "1"
VARIANT_LABEL = re.compile(r"^\s*Form\s+\d+\s*/\s*Forme\s+\d+\s*$")
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
HAIRLINE = 0.5  # QET "thin" is a cosmetic 1-device-pixel pen; this is its SVG stand-in
WEIGHTS = {"none": None, "thin": HAIRLINE, "normal": 1.0, "hight": 2.0, "eleve": 5.0}
DASHES = {"dashed": "4 2", "dotted": "1 2", "dashdotted": "4 2 1 2"}
NAMED = {
    "black": "#000000", "white": "#ffffff", "green": "#00ff00", "red": "#ff0000",
    "blue": "#0000ff", "gray": "#a0a0a4", "brun": "#612c00", "yellow": "#ffff00",
    "cyan": "#00ffff", "magenta": "#ff00ff", "lightgray": "#c0c0c0", "orange": "#ff8000",
    "purple": "#881ca8", "none": "none",
}
HTML_FILLS = {
    "HTMLOrangeTomato": "#ff6347", "HTMLGrayGainsboro": "#dcdcdc", "HTMLBlueDeepSkyBlue": "#00bfff",
    "HTMLBlueCornflowerBlue": "#6495ed", "HTMLBlueDodgerBlue": "#1e90ff", "HTMLWhiteAliceBlue": "#f0f8ff",
}
HATCHES = {"hor", "ver", "bdiag", "fdiag"}
TYPOS = {"ncrmal": "normal", "ncne": "none"}  # seen in the corpus; QET falls back to the default


@dataclass
class Result:
    svg: str
    bbox: tuple[float, float, float, float]
    terminals: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    removed_labels: list[str] = field(default_factory=list)


class ElementDefinitionError(ValueError):
    """The bytes are not an .elmt definition this converter will read."""


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _n(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _style(el, warnings, hatches_used):
    raw = {}
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            raw[k.strip()] = TYPOS.get(v.strip(), v.strip())
    weight = raw.get("line-weight", "normal")
    color = raw.get("color", "black")
    stroke = NAMED.get(color)
    if stroke is None:
        warnings.append(f"unknown color {color!r}")
        stroke = "#000000"
    width = WEIGHTS.get(weight, 1.0)
    attrs = {}
    if width is None:
        attrs["stroke"] = "none"
    else:
        attrs["stroke"] = stroke
        attrs["stroke-width"] = _n(width)
        dash = DASHES.get(raw.get("line-style", "normal"))
        if dash:
            attrs["stroke-dasharray"] = dash
    fill = raw.get("filling", "none")
    if fill in HATCHES:
        hatches_used.add((fill, stroke))
        attrs["fill"] = f"url(#h-{fill}-{stroke.lstrip('#')})"
    elif fill in NAMED:
        attrs["fill"] = NAMED[fill]
    elif fill in HTML_FILLS:
        attrs["fill"] = HTML_FILLS[fill]
    else:
        warnings.append(f"unknown filling {fill!r}")
        attrs["fill"] = "none"
    return attrs, (width or 0.0)


def _attrs(d):
    return " ".join(f'{k}="{escape(str(v), {chr(34): "&quot;"})}"' for k, v in d.items())


class _Box:
    def __init__(self):
        self.x0 = self.y0 = math.inf
        self.x1 = self.y1 = -math.inf

    def add(self, x, y, pad=0.0):
        self.x0, self.y0 = min(self.x0, x - pad), min(self.y0, y - pad)
        self.x1, self.y1 = max(self.x1, x + pad), max(self.y1, y + pad)

    @property
    def empty(self):
        return self.x0 == math.inf


def _end_points(end, other, length):
    """PartLine::fourEndPoints: O, A on the line; B, C either side of O."""
    vx, vy = other[0] - end[0], other[1] - end[1]
    ln = math.hypot(vx, vy) or 1.0
    ux, uy = vx / ln * length, vy / ln * length
    o = (end[0] + ux, end[1] + uy)
    a = (end[0] + 2 * ux, end[1] + 2 * uy)
    b = (o[0] - uy, o[1] + ux)
    c = (o[0] + uy, o[1] - ux)
    return o, a, b, c


REQUIRED = {"circle": 2, "diamond": 2, "simple": 1, "triangle": 1}


def _line(el, style, sw, box):
    p1 = (_f(el.get("x1")), _f(el.get("y1")))
    p2 = (_f(el.get("x2")), _f(el.get("y2")))
    out = []
    ends = [(TYPOS.get(el.get("end1"), el.get("end1")) or "none", _f(el.get("length1"), 1.5)),
            (TYPOS.get(el.get("end2"), el.get("end2")) or "none", _f(el.get("length2"), 1.5))]
    length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    start, stop = p1, p2
    remaining = length
    for idx, (kind, ln) in enumerate(ends):
        need = REQUIRED.get(kind, 0)
        if not need or remaining - ln * need < 0:
            continue
        remaining -= ln * need
        tip, other = (p1, p2) if idx == 0 else (p2, p1)
        o, a, b, c = _end_points(tip, other, ln)
        if kind == "circle":
            out.append(f'<circle {_attrs({"cx": _n(o[0]), "cy": _n(o[1]), "r": _n(ln), **style})}/>')
            box.add(o[0], o[1], ln + sw / 2)
            new = a
        elif kind == "diamond":
            pts = [a, b, tip, c]
            out.append(f'<polygon {_attrs({"points": " ".join(f"{_n(x)},{_n(y)}" for x, y in pts), **style})}/>')
            new = a
        elif kind == "simple":
            pts = [c, tip, b]
            out.append(f'<polyline {_attrs({"points": " ".join(f"{_n(x)},{_n(y)}" for x, y in pts), **{**style, "fill": "none"}})}/>')
            new = tip
        else:  # triangle
            pts = [o, b, tip, c]
            out.append(f'<polygon {_attrs({"points": " ".join(f"{_n(x)},{_n(y)}" for x, y in pts), **style})}/>')
            new = o
        for x, y in (b, c, tip):
            box.add(x, y, sw / 2)
        if idx == 0:
            start = new
        else:
            stop = new
    line_style = {**style, "fill": "none"}
    out.insert(0, f'<line {_attrs({"x1": _n(start[0]), "y1": _n(start[1]), "x2": _n(stop[0]), "y2": _n(stop[1]), **line_style})}/>')
    box.add(*p1, sw / 2)
    box.add(*p2, sw / 2)
    return out


def _arc(el, style, sw, box):
    x, y, w, h = (_f(el.get(k)) for k in ("x", "y", "width", "height"))
    start, span = _f(el.get("start")), _f(el.get("angle"), -90)
    rx, ry = w / 2, h / 2
    cx, cy = x + rx, y + ry
    if abs(span) >= 360:
        box.add(x, y, sw / 2)
        box.add(x + w, y + h, sw / 2)
        return [f'<ellipse {_attrs({"cx": _n(cx), "cy": _n(cy), "rx": _n(rx), "ry": _n(ry), **style})}/>']

    def pt(deg):
        r = math.radians(deg)
        return cx + rx * math.cos(r), cy - ry * math.sin(r)

    p0, p1 = pt(start), pt(start + span)
    large = 1 if abs(span) > 180 else 0
    sweep = 0 if span > 0 else 1  # Qt positive = counter-clockwise on screen = SVG sweep 0
    d = f"M{_n(p0[0])},{_n(p0[1])} A{_n(rx)},{_n(ry)} 0 {large} {sweep} {_n(p1[0])},{_n(p1[1])}"
    steps = max(8, int(abs(span) / 5))
    for i in range(steps + 1):
        box.add(*pt(start + span * i / steps), sw / 2)
    return [f'<path {_attrs({"d": d, **style})}/>']


def _font(el):
    parts = (el.get("font") or "").split(",")
    family = parts[0] or "Liberation Sans"
    size = _f(parts[1], 9) if len(parts) > 1 else _f(el.get("size"), 9)
    bold = len(parts) > 4 and _f(parts[4], 50) >= 63
    italic = len(parts) > 5 and parts[5] == "1"
    return family, size, bold, italic


def _text(el, literal, warnings, box):
    family, size, bold, italic = _font(el)
    x, y = _f(el.get("x")), _f(el.get("y"))
    rot = _f(el.get("rotation"))
    raw_color = el.get("color") or "black"
    color = NAMED.get(raw_color) or (raw_color if HEX_COLOR.match(raw_color) else "#000000")
    attrs = {"x": _n(x), "y": _n(y), "font-family": f"{family}, Arial, Helvetica, sans-serif", "font-size": _n(size), "fill": color}
    if bold:
        attrs["font-weight"] = "bold"
    if italic:
        attrs["font-style"] = "italic"
    if rot:
        attrs["transform"] = f"rotate({_n(rot)} {_n(x)} {_n(y)})"
    # approximate extent for the viewBox: 0.6em per glyph, ascent ~0.8em
    lines = literal.split("\n")
    wdt = max(len(s) for s in lines) * size * 0.6
    hgt = size * (0.8 + 1.2 * (len(lines) - 1))
    corners = [(0, -size * 0.8), (wdt, -size * 0.8), (0, hgt - size * 0.8 + size * 0.25), (wdt, hgt - size * 0.8 + size * 0.25)]
    r = math.radians(rot)
    for dx, dy in corners:
        box.add(x + dx * math.cos(r) - dy * math.sin(r), y + dx * math.sin(r) + dy * math.cos(r))
    if len(lines) == 1:
        body = escape(literal)
    else:
        body = "".join(f'<tspan x="{_n(x)}" dy="{_n(0 if i == 0 else size * 1.2)}">{escape(s)}</tspan>' for i, s in enumerate(lines))
    return [f"<text {_attrs(attrs)}>{body}</text>"]


def convert(xml_bytes: bytes, title: str = "", desc: str = "") -> Result:
    if b"<!DOCTYPE" in xml_bytes or b"<!ENTITY" in xml_bytes:
        raise ElementDefinitionError("a DOCTYPE or entity declaration is not read")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ElementDefinitionError(f"not well-formed XML: {exc}") from exc
    if root.tag != "definition":
        raise ElementDefinitionError(f"root element is <{root.tag}>, not <definition>")
    description = root.find("description")
    warnings: list[str] = []
    hatches: set = set()
    box = _Box()
    body: list[str] = []
    terminals: list[dict] = []
    texts: list[str] = []
    removed: list[str] = []
    for el in description if description is not None else []:
        tag = el.tag
        if tag == "terminal":
            terminals.append({k: el.get(k) for k in ("x", "y", "orientation", "name", "type") if el.get(k) is not None})
            continue
        if tag == "dynamic_text":
            literal = (el.findtext("text") or "").strip()
            if el.get("text_from") == "UserText" and literal:
                texts.append(literal)
                body += _text(el, literal, warnings, box)
            continue
        if tag == "text":
            literal = el.get("text") or ""
            if VARIANT_LABEL.match(literal):
                removed.append(literal.strip())
                continue
            if literal.strip():
                texts.append(literal)
                body += _text(el, literal, warnings, box)
            continue
        style, sw = _style(el, warnings, hatches)
        if tag == "line":
            body += _line(el, style, sw, box)
        elif tag == "rect":
            x, y, w, h = (_f(el.get(k)) for k in ("x", "y", "width", "height"))
            a = {"x": _n(x), "y": _n(y), "width": _n(w), "height": _n(h)}
            rx, ry = _f(el.get("rx")), _f(el.get("ry"))
            if rx or ry:
                # QET stores rounding as a percentage-like radius in scene units since 0.8
                a["rx"], a["ry"] = _n(rx), _n(ry)
            body.append(f"<rect {_attrs({**a, **style})}/>")
            box.add(x, y, sw / 2)
            box.add(x + w, y + h, sw / 2)
        elif tag == "ellipse":
            x, y, w, h = (_f(el.get(k)) for k in ("x", "y", "width", "height"))
            body.append(f'<ellipse {_attrs({"cx": _n(x + w / 2), "cy": _n(y + h / 2), "rx": _n(w / 2), "ry": _n(h / 2), **style})}/>')
            box.add(x, y, sw / 2)
            box.add(x + w, y + h, sw / 2)
        elif tag == "circle":
            x, y, dia = _f(el.get("x")), _f(el.get("y")), _f(el.get("diameter"))
            body.append(f'<circle {_attrs({"cx": _n(x + dia / 2), "cy": _n(y + dia / 2), "r": _n(dia / 2), **style})}/>')
            box.add(x, y, sw / 2)
            box.add(x + dia, y + dia, sw / 2)
        elif tag == "arc":
            body += _arc(el, style, sw, box)
        elif tag == "polygon":
            pts = []
            i = 1
            while el.get(f"x{i}") is not None:
                pts.append((_f(el.get(f"x{i}")), _f(el.get(f"y{i}"))))
                i += 1
            for p in pts:
                box.add(*p, sw / 2)
            closed = el.get("closed", "true") != "false"
            name = "polygon" if closed else "polyline"
            st = style if closed else {**style, "fill": style.get("fill", "none")}
            body.append(f'<{name} {_attrs({"points": " ".join(f"{_n(x)},{_n(y)}" for x, y in pts), **st})}/>')
        else:
            warnings.append(f"unsupported part <{tag}>")

    if box.empty:
        box.add(0, 0)
        warnings.append("no drawable parts")
    pad = 1.0
    vx, vy = box.x0 - pad, box.y0 - pad
    vw, vh = (box.x1 - box.x0) + 2 * pad, (box.y1 - box.y0) + 2 * pad
    defs = []
    for kind, color in sorted(hatches):
        line = {"hor": "M0,2 H4", "ver": "M2,0 V4", "bdiag": "M0,4 L4,0", "fdiag": "M0,0 L4,4"}[kind]
        defs.append(
            f'<pattern id="h-{kind}-{color.lstrip("#")}" width="4" height="4" patternUnits="userSpaceOnUse">'
            f'<path d="{line}" stroke="{color}" stroke-width="0.5"/></pattern>'
        )
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_n(vx)} {_n(vy)} {_n(vw)} {_n(vh)}" '
        f'width="{_n(vw)}" height="{_n(vh)}" stroke-linecap="square" stroke-linejoin="miter" fill="none">'
    )
    meta = ""
    if title:
        meta += f"<title>{escape(title)}</title>"
    if desc:
        meta += f"<desc>{escape(desc)}</desc>"
    svg = head + meta + (f"<defs>{''.join(defs)}</defs>" if defs else "") + "".join(body) + "</svg>\n"
    return Result(svg=svg, bbox=(vx, vy, vw, vh), terminals=terminals, warnings=warnings, texts=texts, removed_labels=removed)
