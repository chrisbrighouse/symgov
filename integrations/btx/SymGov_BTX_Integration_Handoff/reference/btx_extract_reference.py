#!/usr/bin/env python3
"""Extract vector symbols from a Bluebeam Revu .btx Tool Set.

Reference implementation reverse-engineered against BluebeamRevuToolSet Version=1 files whose values are
hex-encoded zlib streams and whose symbol appearance is a PDF Form XObject.

Outputs per symbol:
  * SVG (vector, original PDF-point dimensions by default)
  * DXF (ASCII, LINE entities; configurable units)
  * PDF content-stream text for inspection
and a manifest.json for the tool set.

No third-party Python packages are required.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
import zlib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence

NUM_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"


@dataclass(frozen=True)
class Matrix:
    """PDF affine matrix [a b c d e f], using column vectors."""
    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return (self.a * x + self.c * y + self.e,
                self.b * x + self.d * y + self.f)

    def concat(self, rhs: "Matrix") -> "Matrix":
        """Return self × rhs, matching PDF CTM concatenation for `cm`."""
        return Matrix(
            self.a * rhs.a + self.c * rhs.b,
            self.b * rhs.a + self.d * rhs.b,
            self.a * rhs.c + self.c * rhs.d,
            self.b * rhs.c + self.d * rhs.d,
            self.a * rhs.e + self.c * rhs.f + self.e,
            self.b * rhs.e + self.d * rhs.f + self.f,
        )

    def scale_factor(self) -> float:
        # Area-preserving scalar; exact for uniform scaling and good for strokes.
        return math.sqrt(abs(self.a * self.d - self.b * self.c))


@dataclass
class GraphicsState:
    ctm: Matrix = field(default_factory=Matrix)
    line_width: float = 1.0
    line_cap: int = 0
    line_join: int = 0
    stroke_rgb: tuple[float, float, float] = (0.0, 0.0, 0.0)
    fill_rgb: tuple[float, float, float] = (0.0, 0.0, 0.0)
    dash: tuple[list[float], float] | None = None

    def copy(self) -> "GraphicsState":
        return GraphicsState(
            ctm=self.ctm,
            line_width=self.line_width,
            line_cap=self.line_cap,
            line_join=self.line_join,
            stroke_rgb=self.stroke_rgb,
            fill_rgb=self.fill_rgb,
            dash=(list(self.dash[0]), self.dash[1]) if self.dash else None,
        )


@dataclass
class Segment:
    kind: str  # M, L, C, Z
    pts: tuple[float, ...] = ()


@dataclass
class PaintedPath:
    segments: list[Segment]
    stroke: bool
    fill: bool
    evenodd: bool
    state: GraphicsState


@dataclass
class Symbol:
    ordinal: int
    subject: str
    internal_name: str
    annotation_type: str
    rect: tuple[float, float, float, float]
    bbox: tuple[float, float, float, float]
    form_matrix: Matrix
    raw_annotation: str
    content_stream: bytes
    paths: list[PaintedPath]

    @property
    def width(self) -> float:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> float:
        return self.rect[3] - self.rect[1]


def inflate_hex(text: str) -> bytes:
    return zlib.decompress(bytes.fromhex(text.strip()))


def parse_numbers(text: str) -> list[float]:
    return [float(v) for v in re.findall(NUM_RE, text)]


def parse_pdf_array_after_key(text: str, key: str, n: int) -> tuple[float, ...]:
    m = re.search(rf"/{re.escape(key)}\s*\[([^\]]+)\]", text)
    if not m:
        raise ValueError(f"Missing /{key} array")
    nums = parse_numbers(m.group(1))
    if len(nums) != n:
        raise ValueError(f"/{key} expected {n} numbers, found {len(nums)}")
    return tuple(nums)


def parse_pdf_name_or_string(text: str, key: str) -> str:
    # Parenthesized PDF string, sufficient for Bluebeam's /Subj here.
    m = re.search(rf"/{re.escape(key)}\s*\((.*?)\)", text, re.S)
    if m:
        return m.group(1).replace(r"\(", "(").replace(r"\)", ")")
    m = re.search(rf"/{re.escape(key)}\s*/([^/<>\[\]()\s]+)", text)
    return m.group(1) if m else ""


def parse_normal_appearance_resource_id(text: str) -> str:
    """Return the decoded Resource ID named by /AP << /N /BBObjPtr_ID >>."""
    m = re.search(
        r"/AP\s*<<(?:(?!>>).)*?/N\s*/BBObjPtr_([^/<>\[\]()\s]+)(?:(?!>>).)*?>>",
        text,
        re.S,
    )
    if not m:
        raise ValueError("Missing /AP /N appearance resource pointer")
    return m.group(1)


def extract_pdf_stream(resource: bytes) -> tuple[str, bytes]:
    """Split a serialized PDF stream object and inflate /FlateDecode content."""
    m = re.search(rb"(?:\r\n|\n)stream(?:\r\n|\n)", resource)
    if not m:
        raise ValueError("Resource has no PDF stream")
    header = resource[:m.start()].decode("latin-1")
    remainder = resource[m.end():]
    end = remainder.rfind(b"endstream")
    if end < 0:
        raise ValueError("Resource stream has no endstream")
    encoded = remainder[:end].rstrip(b"\r\n")
    if "/FlateDecode" in header:
        decoded = zlib.decompress(encoded)
    else:
        decoded = encoded
    return header, decoded


def tokenize_pdf_content(data: bytes) -> Iterator[object]:
    """Small PDF content tokenizer for numeric graphics streams.

    Supports numbers, names, arrays and operators. It deliberately rejects
    inline images and complex strings rather than silently misinterpreting them.
    """
    s = data.decode("latin-1")
    i, n = 0, len(s)
    delimiters = "()<>[]{}/%"
    whitespace = "\x00\t\n\x0c\r "

    def skip_ws_comments(pos: int) -> int:
        while pos < n:
            if s[pos] in whitespace:
                pos += 1
            elif s[pos] == "%":
                while pos < n and s[pos] not in "\r\n":
                    pos += 1
            else:
                break
        return pos

    while True:
        i = skip_ws_comments(i)
        if i >= n:
            return
        ch = s[i]
        if ch == "[":
            i += 1
            arr: list[object] = []
            while True:
                i = skip_ws_comments(i)
                if i >= n:
                    raise ValueError("Unterminated PDF array")
                if s[i] == "]":
                    i += 1
                    break
                j = i
                while j < n and s[j] not in whitespace + "[]":
                    j += 1
                tok = s[i:j]
                try:
                    arr.append(float(tok))
                except ValueError:
                    arr.append(tok)
                i = j
            yield arr
            continue
        if ch == "/":
            j = i + 1
            while j < n and s[j] not in whitespace + delimiters:
                j += 1
            yield ("NAME", s[i + 1:j])
            i = j
            continue
        if ch in "()<>{}":
            raise ValueError(f"Unsupported PDF token {ch!r} at offset {i}")
        j = i
        while j < n and s[j] not in whitespace + delimiters:
            j += 1
        tok = s[i:j]
        if not tok:
            raise ValueError(f"Could not tokenize PDF stream at offset {i}")
        try:
            yield float(tok)
        except ValueError:
            yield tok
        i = j


def parse_content_stream(data: bytes, initial_ctm: Matrix) -> list[PaintedPath]:
    state = GraphicsState(ctm=initial_ctm)
    states: list[GraphicsState] = []
    operands: list[object] = []
    current: list[Segment] = []
    output: list[PaintedPath] = []
    current_point: tuple[float, float] | None = None
    subpath_start: tuple[float, float] | None = None
    pending_clip = False

    operators = {
        "q", "Q", "cm", "w", "J", "j", "M", "d", "ri", "i", "gs",
        "m", "l", "c", "v", "y", "h", "re",
        "S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n", "W", "W*",
        "G", "g", "RG", "rg", "K", "k",
    }

    def nums(count: int) -> list[float]:
        if len(operands) < count:
            raise ValueError(f"Operator needs {count} operands, got {operands}")
        vals = operands[-count:]
        if not all(isinstance(v, (float, int)) for v in vals):
            raise ValueError(f"Expected numeric operands, got {vals}")
        return [float(v) for v in vals]

    def paint(stroke: bool, fill: bool, evenodd: bool = False, close: bool = False) -> None:
        nonlocal current, current_point, subpath_start, pending_clip
        if close and current and (not current or current[-1].kind != "Z"):
            current.append(Segment("Z"))
        if current and (stroke or fill):
            output.append(PaintedPath(list(current), stroke, fill, evenodd, state.copy()))
        current = []
        current_point = None
        subpath_start = None
        pending_clip = False

    for tok in tokenize_pdf_content(data):
        if not isinstance(tok, str) or tok not in operators:
            operands.append(tok)
            continue
        op = tok
        if op == "q":
            states.append(state.copy())
        elif op == "Q":
            if not states:
                raise ValueError("Unbalanced Q operator")
            state = states.pop()
        elif op == "cm":
            a, b, c, d, e, f = nums(6)
            state.ctm = state.ctm.concat(Matrix(a, b, c, d, e, f))
        elif op == "w":
            state.line_width = nums(1)[0]
        elif op == "J":
            state.line_cap = int(nums(1)[0])
        elif op == "j":
            state.line_join = int(nums(1)[0])
        elif op == "d":
            if len(operands) >= 2 and isinstance(operands[-2], list):
                state.dash = ([float(v) for v in operands[-2]], float(operands[-1]))
        elif op == "RG":
            state.stroke_rgb = tuple(nums(3))  # type: ignore[assignment]
        elif op == "rg":
            state.fill_rgb = tuple(nums(3))  # type: ignore[assignment]
        elif op == "G":
            g = nums(1)[0]; state.stroke_rgb = (g, g, g)
        elif op == "g":
            g = nums(1)[0]; state.fill_rgb = (g, g, g)
        elif op in ("K", "k"):
            c, m, y, k = nums(4)
            rgb = (1 - min(1, c + k), 1 - min(1, m + k), 1 - min(1, y + k))
            if op == "K": state.stroke_rgb = rgb
            else: state.fill_rgb = rgb
        elif op == "m":
            x, y = nums(2); p = state.ctm.apply(x, y)
            current.append(Segment("M", p)); current_point = p; subpath_start = p
        elif op == "l":
            x, y = nums(2); p = state.ctm.apply(x, y)
            current.append(Segment("L", p)); current_point = p
        elif op == "c":
            x1, y1, x2, y2, x3, y3 = nums(6)
            p1 = state.ctm.apply(x1, y1); p2 = state.ctm.apply(x2, y2); p3 = state.ctm.apply(x3, y3)
            current.append(Segment("C", p1 + p2 + p3)); current_point = p3
        elif op == "v":
            if current_point is None: raise ValueError("v without current point")
            x2, y2, x3, y3 = nums(4)
            p2 = state.ctm.apply(x2, y2); p3 = state.ctm.apply(x3, y3)
            current.append(Segment("C", current_point + p2 + p3)); current_point = p3
        elif op == "y":
            x1, y1, x3, y3 = nums(4)
            p1 = state.ctm.apply(x1, y1); p3 = state.ctm.apply(x3, y3)
            current.append(Segment("C", p1 + p3 + p3)); current_point = p3
        elif op == "h":
            current.append(Segment("Z")); current_point = subpath_start
        elif op == "re":
            x, y, w, h = nums(4)
            pts = [state.ctm.apply(x, y), state.ctm.apply(x + w, y),
                   state.ctm.apply(x + w, y + h), state.ctm.apply(x, y + h)]
            current.extend([Segment("M", pts[0]), Segment("L", pts[1]),
                            Segment("L", pts[2]), Segment("L", pts[3]), Segment("Z")])
            current_point = pts[0]; subpath_start = pts[0]
        elif op in ("W", "W*"):
            pending_clip = True  # Clip path is discarded at n/paint; viewBox supplies main clip.
        elif op == "S": paint(True, False)
        elif op == "s": paint(True, False, close=True)
        elif op in ("f", "F"): paint(False, True)
        elif op == "f*": paint(False, True, evenodd=True)
        elif op == "B": paint(True, True)
        elif op == "B*": paint(True, True, evenodd=True)
        elif op == "b": paint(True, True, close=True)
        elif op == "b*": paint(True, True, evenodd=True, close=True)
        elif op == "n": paint(False, False)
        # M, ri, i and gs have no geometric effect needed by this extractor.
        operands.clear()

    if current:
        # An unpainted path has no visible result.
        current = []
    return output


def clean_filename(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip()).strip("._")
    return s or "symbol"


def rgb_css(rgb: tuple[float, float, float]) -> str:
    vals = [max(0, min(255, round(v * 255))) for v in rgb]
    return "#%02x%02x%02x" % tuple(vals)


def fmt(v: float) -> str:
    if abs(v) < 5e-9: v = 0.0
    return f"{v:.6f}".rstrip("0").rstrip(".") or "0"


def path_to_svg_d(path: PaintedPath) -> str:
    chunks: list[str] = []
    for seg in path.segments:
        if seg.kind in ("M", "L"):
            chunks.append(f"{seg.kind}{fmt(seg.pts[0])},{fmt(seg.pts[1])}")
        elif seg.kind == "C":
            chunks.append("C" + " ".join(f"{fmt(seg.pts[i])},{fmt(seg.pts[i+1])}" for i in range(0, 6, 2)))
        elif seg.kind == "Z":
            chunks.append("Z")
    return " ".join(chunks)


def write_svg(symbol: Symbol, path: Path) -> None:
    width, height = symbol.width, symbol.height
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{fmt(width)}pt" height="{fmt(height)}pt" viewBox="0 0 {fmt(width)} {fmt(height)}">',
        f'  <title>{xml_escape(symbol.subject)}</title>',
        f'  <desc>Extracted from Bluebeam BTX; source dimensions {fmt(width)} × {fmt(height)} PDF points.</desc>',
        f'  <clipPath id="symbol-clip"><rect x="0" y="0" width="{fmt(width)}" height="{fmt(height)}"/></clipPath>',
        f'  <g clip-path="url(#symbol-clip)" transform="translate(0 {fmt(height)}) scale(1 -1)">',
    ]
    caps = {0: "butt", 1: "round", 2: "square"}
    joins = {0: "miter", 1: "round", 2: "bevel"}
    for p in symbol.paths:
        stroke = rgb_css(p.state.stroke_rgb) if p.stroke else "none"
        fill = rgb_css(p.state.fill_rgb) if p.fill else "none"
        sw = p.state.line_width * p.state.ctm.scale_factor()
        attrs = [
            f'd="{path_to_svg_d(p)}"', f'stroke="{stroke}"', f'fill="{fill}"',
            f'stroke-width="{fmt(sw)}"', f'stroke-linecap="{caps.get(p.state.line_cap, "butt")}"',
            f'stroke-linejoin="{joins.get(p.state.line_join, "miter")}"',
        ]
        if p.evenodd: attrs.append('fill-rule="evenodd"')
        if p.state.dash:
            arr, phase = p.state.dash
            sf = p.state.ctm.scale_factor()
            attrs.append('stroke-dasharray="' + " ".join(fmt(x * sf) for x in arr) + '"')
            attrs.append(f'stroke-dashoffset="{fmt(phase * sf)}"')
        lines.append("    <path " + " ".join(attrs) + "/>")
    lines.extend(["  </g>", "</svg>", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def cubic_point(p0, p1, p2, p3, t: float):
    mt = 1 - t
    return (
        mt**3*p0[0] + 3*mt**2*t*p1[0] + 3*mt*t**2*p2[0] + t**3*p3[0],
        mt**3*p0[1] + 3*mt**2*t*p1[1] + 3*mt*t**2*p2[1] + t**3*p3[1],
    )


def paths_to_lines(paths: Sequence[PaintedPath], curve_steps: int = 24) -> list[tuple[tuple[float,float], tuple[float,float], PaintedPath]]:
    lines = []
    for p in paths:
        cur = None; start = None
        for seg in p.segments:
            if seg.kind == "M": cur = (seg.pts[0], seg.pts[1]); start = cur
            elif seg.kind == "L" and cur is not None:
                nxt = (seg.pts[0], seg.pts[1]); lines.append((cur, nxt, p)); cur = nxt
            elif seg.kind == "C" and cur is not None:
                p1=(seg.pts[0],seg.pts[1]); p2=(seg.pts[2],seg.pts[3]); p3=(seg.pts[4],seg.pts[5])
                prev=cur
                for k in range(1, curve_steps+1):
                    nxt=cubic_point(cur,p1,p2,p3,k/curve_steps); lines.append((prev,nxt,p)); prev=nxt
                cur=p3
            elif seg.kind == "Z" and cur is not None and start is not None:
                if math.dist(cur,start) > 1e-9: lines.append((cur,start,p))
                cur=start
    return lines


def write_dxf(symbol: Symbol, path: Path, units: str) -> None:
    factors = {"pt": 1.0, "mm": 25.4/72.0, "in": 1.0/72.0}
    insunits = {"pt": 0, "mm": 4, "in": 1}
    f = factors[units]
    # ASCII DXF R2000. Each visible path segment becomes a LINE entity.
    out: list[str] = []
    def pair(code, value): out.extend([str(code), str(value)])
    pair(0,"SECTION"); pair(2,"HEADER")
    pair(9,"$ACADVER"); pair(1,"AC1015")
    pair(9,"$INSUNITS"); pair(70,insunits[units])
    pair(9,"$EXTMIN"); pair(10,fmt(0)); pair(20,fmt(0)); pair(30,fmt(0))
    pair(9,"$EXTMAX"); pair(10,fmt(symbol.width*f)); pair(20,fmt(symbol.height*f)); pair(30,fmt(0))
    pair(0,"ENDSEC")
    pair(0,"SECTION"); pair(2,"ENTITIES")
    for a,b,p in paths_to_lines(symbol.paths):
        pair(0,"LINE"); pair(8,"BTX_SYMBOL")
        pair(10,fmt(a[0]*f)); pair(20,fmt(a[1]*f)); pair(30,"0")
        pair(11,fmt(b[0]*f)); pair(21,fmt(b[1]*f)); pair(31,"0")
    pair(0,"ENDSEC"); pair(0,"EOF")
    path.write_text("\n".join(out)+"\n", encoding="ascii")


def read_btx_bytes(path: Path) -> tuple[bytes, str]:
    """Read a .btx directly, or the single .btx contained in a .zip."""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".btx") and not n.endswith("/")]
            if len(names) != 1:
                raise ValueError(f"ZIP must contain exactly one .btx file; found {len(names)}")
            return zf.read(names[0]), f"{path}!/{names[0]}"
    return path.read_bytes(), str(path)


def load_btx(path: Path) -> tuple[str, str, list[Symbol]]:
    xml_bytes, source_label = read_btx_bytes(path)
    root = ET.fromstring(xml_bytes)
    if root.tag != "BluebeamRevuToolSet":
        raise ValueError(f"Unexpected root element: {root.tag}")
    title_hex = root.findtext("Title") or ""
    title = inflate_hex(title_hex).decode("utf-8", errors="replace") if title_hex else path.stem
    symbols: list[Symbol] = []
    for ordinal, item in enumerate(root.findall("ToolChestItem")):
        raw = inflate_hex(item.findtext("Raw") or "").decode("latin-1")
        subject = parse_pdf_name_or_string(raw, "Subj") or f"Symbol {ordinal+1}"
        rect = parse_pdf_array_after_key(raw, "Rect", 4)
        appearance_id = parse_normal_appearance_resource_id(raw)
        resources = item.findall("Resources")
        resource_map: dict[str, bytes] = {}
        for r in resources:
            rid = inflate_hex(r.findtext("ID") or "").decode("latin-1")
            resource_map[rid] = inflate_hex(r.findtext("Data") or "")
        form_resource = resource_map.get(appearance_id)
        if form_resource is None:
            raise ValueError(
                f"{subject}: /AP /N points to missing resource {appearance_id!r}"
            )
        if b"/Subtype/Form" not in form_resource or b"stream" not in form_resource:
            raise ValueError(
                f"{subject}: appearance resource {appearance_id!r} is not a PDF Form XObject"
            )
        header, stream = extract_pdf_stream(form_resource)
        bbox = parse_pdf_array_after_key(header, "BBox", 4)
        matrix_vals = parse_pdf_array_after_key(header, "Matrix", 6)
        form_matrix = Matrix(*matrix_vals)
        paths = parse_content_stream(stream, form_matrix)
        symbols.append(Symbol(
            ordinal=ordinal,
            subject=subject,
            internal_name=item.findtext("Name") or "",
            annotation_type=item.findtext("Type") or "",
            rect=rect,
            bbox=bbox,
            form_matrix=form_matrix,
            raw_annotation=raw,
            content_stream=stream,
            paths=paths,
        ))
    return source_label, title, symbols


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path, help="Bluebeam .btx file, or a ZIP containing one .btx")
    ap.add_argument("-o", "--output", type=Path, default=Path("btx_output"))
    ap.add_argument("--dxf-units", choices=("pt","mm","in"), default="mm")
    args = ap.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    source_label, title, symbols = load_btx(args.input)
    manifest = {
        "source": source_label, "tool_set_title": title,
        "format_observation": "XML with hex-encoded zlib payloads containing PDF annotation dictionaries and Form XObject content streams",
        "dxf_units": args.dxf_units,
        "symbols": [],
    }
    used: dict[str,int] = {}
    for s in symbols:
        base = clean_filename(s.subject)
        used[base] = used.get(base,0)+1
        if used[base] > 1: base += f"_{used[base]}"
        write_svg(s, args.output / f"{base}.svg")
        write_dxf(s, args.output / f"{base}.dxf", args.dxf_units)
        (args.output / f"{base}.pdfops.txt").write_bytes(s.content_stream)
        manifest["symbols"].append({
            "subject": s.subject,
            "internal_name": s.internal_name,
            "annotation_type": s.annotation_type,
            "rect_points": list(s.rect),
            "width_points": s.width,
            "height_points": s.height,
            "bbox": list(s.bbox),
            "form_matrix": [s.form_matrix.a,s.form_matrix.b,s.form_matrix.c,s.form_matrix.d,s.form_matrix.e,s.form_matrix.f],
            "painted_paths": len(s.paths),
            "svg": f"{base}.svg", "dxf": f"{base}.dxf", "pdf_operators": f"{base}.pdfops.txt",
        })
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Extracted {len(symbols)} symbols from {title!r} to {args.output}")
    for rec in manifest["symbols"]:
        print(f"  {rec['subject']}: {rec['width_points']:.4g} × {rec['height_points']:.4g} pt")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise
