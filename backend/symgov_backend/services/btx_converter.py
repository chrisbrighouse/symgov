"""Bounded Bluebeam BTX v1 stamp-snapshot conversion.

The converter deliberately supports the BTX form verified by Symgov's fixture:
hex encoded zlib payloads containing PDF Stamp annotations and Form XObject
appearance streams.  It is a pure file service: callers own queues, storage,
and access control.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import re
import struct
import zlib
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - deployment dependency
    Image = ImageDraw = None


MAX_INPUT_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 16 * 1024 * 1024
MAX_SYMBOLS = 500
MAX_PATHS_PER_SYMBOL = 10_000
MAX_GEOMETRY = 1_000_000
MAX_PNG_SIDE = 2048
MAX_PNG_PIXELS = 4_194_304
MAX_GRAPHICS_STATE_DEPTH = 64
# Conservative service policy for the verified stamp subset, not BTX format maxima.
MAX_DASH_ELEMENTS = 128
MAX_STYLE_WORK_PER_SYMBOL = 100_000
MAX_STYLE_WORK_PER_LIBRARY = 1_000_000
MAX_TOKENS_PER_SYMBOL = 100_000
MAX_TOKENS_PER_LIBRARY = 2_000_000
MAX_OPERANDS = 64
MAX_WARNINGS_PER_SYMBOL = 64
MAX_RESOURCES_PER_SYMBOL = 128
MAX_RESOURCES_PER_LIBRARY = 4096
MAX_DECOMPRESSIONS_PER_SYMBOL = 256
MAX_DECOMPRESSIONS_PER_LIBRARY = 8192
MAX_EXPANDED_BYTES_PER_SYMBOL = 32 * 1024 * 1024
MAX_EXPANDED_BYTES_PER_LIBRARY = 64 * 1024 * 1024
STREAM_TAIL = re.compile(rb"[\r\n]*endstream\s*(?:endobj\s*)?")
NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"
TOKEN = re.compile(r"\[[^\[\]]*\]|/[^\s<>{}\[\]()/%]+|" + NUMBER + r"|[^\s<>{}\[\]()/%]+")


class BtxConversionError(ValueError):
    """Expected, structured BTX conversion failure."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _warning(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


class _Budget:
    """Monotone work accounting, including failed/duplicate annotation passes."""

    def __init__(self, parent: _Budget | None = None):
        self.parent = parent
        self.used: dict[str, int] = {}

    def remaining(self, kind: str) -> int:
        scope = 'SYMBOL' if self.parent else 'LIBRARY'
        limit = globals()[f'MAX_{kind}_PER_{scope}']
        available = limit - self.used.get(kind, 0)
        return min(available, self.parent.remaining(kind)) if self.parent else available

    def charge(self, kind: str, amount: int, code: str) -> None:
        if amount > self.remaining(kind):
            raise BtxConversionError(code, f'Cumulative {kind.lower()} budget exceeded.')
        self.used[kind] = self.used.get(kind, 0) + amount
        if self.parent:
            self.parent.charge(kind, amount, code)


def _inflate(data: bytes, *, label: str, maximum: int | None = None, budget: _Budget | None = None, stream_tail: bool = False) -> bytes:
    maximum = MAX_EXPANDED_BYTES if maximum is None else maximum
    limited_by_budget = False
    if budget:
        budget.charge('DECOMPRESSIONS', 1, 'decompression_count_limit')
        remaining = budget.remaining('EXPANDED_BYTES')
        limited_by_budget = remaining < maximum
        maximum = min(maximum, remaining)
    size_code = 'btx_cumulative_expanded_size_limit' if limited_by_budget else 'btx_expanded_size_limit'
    try:
        inflater = zlib.decompressobj()
        output = inflater.decompress(data, maximum + 1)
        if budget:
            budget.charge('EXPANDED_BYTES', min(len(output), maximum), 'btx_cumulative_expanded_size_limit')
        if len(output) > maximum or inflater.unconsumed_tail:
            raise BtxConversionError(size_code, f'{label} exceeds the expanded size limit.')
        # decompress with an explicit cap already drains output at zlib EOF;
        # do not flush and allocate output outside the cumulative budget.
    except zlib.error as exc:
        if budget:
            budget.charge('EXPANDED_BYTES', maximum, 'btx_cumulative_expanded_size_limit')
        raise BtxConversionError('invalid_zlib_payload', f'{label} is not a valid zlib payload.') from exc
    valid_tail = STREAM_TAIL.fullmatch(inflater.unused_data) if stream_tail else not inflater.unused_data
    if not inflater.eof or not valid_tail:
        raise BtxConversionError('invalid_zlib_payload', f'{label} is incomplete or has trailing data.')
    return output


def _inflate_hex(value: str | None, *, label: str, budget: _Budget | None = None) -> bytes:
    text = (value or '').strip()
    if not text or len(text) % 2 or re.search(r'[^0-9a-fA-F]', text):
        raise BtxConversionError('invalid_hex_payload', f'{label} is not hexadecimal data.')
    return _inflate(bytes.fromhex(text), label=label, budget=budget)


def _read_input(path: Path) -> tuple[bytes, str]:
    if not path.exists() or not path.is_file():
        raise BtxConversionError("missing_input", "BTX input does not exist.")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise BtxConversionError("input_size_limit", "BTX input exceeds the size limit.")
    if path.suffix.lower() != ".zip":
        return path.read_bytes(), path.name
    try:
        with zipfile.ZipFile(path) as archive:
            if any(info.flag_bits & 1 for info in archive.infolist()):
                raise BtxConversionError("encrypted_zip", "Encrypted ZIP files are not supported.")
            candidates = []
            for info in archive.infolist():
                member = PurePosixPath(info.filename.replace("\\", "/"))
                if member.is_absolute() or ".." in member.parts or info.is_dir():
                    raise BtxConversionError("unsafe_zip_member", "ZIP contains an unsafe member path.")
                if member.suffix.lower() == ".btx":
                    candidates.append(info)
            if len(candidates) != 1:
                raise BtxConversionError("zip_btx_count", "ZIP must contain exactly one BTX file.")
            info = candidates[0]
            if info.file_size > MAX_INPUT_BYTES:
                raise BtxConversionError("input_size_limit", "BTX member exceeds the size limit.")
            return archive.read(info), Path(info.filename).name
    except zipfile.BadZipFile as exc:
        raise BtxConversionError("bad_zip_file", "Input ZIP could not be opened.") from exc


def _numbers(value: str, count: int, name: str) -> tuple[float, ...]:
    tokens = value.split()
    if len(tokens) != count or any(not re.fullmatch(NUMBER, token) for token in tokens):
        raise BtxConversionError("invalid_pdf_dictionary", f"{name} must contain {count} numbers.")
    values = tuple(float(item) for item in tokens)
    return _bounded(values, name)


def _bounded(values: tuple[float, ...], label: str) -> tuple[float, ...]:
    if any(not math.isfinite(value) or abs(value) > MAX_GEOMETRY for value in values):
        raise BtxConversionError("geometry_limit", f"{label} contains nonfinite or out-of-range geometry.")
    return values


def _dimensions(width: float, height: float) -> None:
    _bounded((width, height), "Symbol dimensions")
    if width <= 0 or height <= 0:
        raise BtxConversionError("invalid_geometry", "Symbol dimensions must be positive.")


def _pdf_field_start(text: str, name: str) -> int | None:
    # Select the first exact key, not the first key whose entire value matches.
    # Retrying a missing closing delimiter at every repeated key is quadratic.
    match = re.search(rf"/{re.escape(name)}(?=[\s<>{{}}\[\]()/%]|$)", text)
    if not match:
        return None
    start = match.end()
    while start < len(text) and text[start].isspace():
        start += 1
    return start


def _pdf_array(text: str, name: str, count: int, *, default: tuple[float, ...] | None = None) -> tuple[float, ...]:
    start = _pdf_field_start(text, name)
    if start is None:
        if default is not None:
            return default
        raise BtxConversionError("invalid_pdf_dictionary", f"Missing /{name}.")
    # Anchored at one value; exclude nested arrays so malformed repeated
    # openers fail immediately, without scanning/backtracking over every suffix.
    match = re.compile(r"\[([^\[\]]*)\]").match(text, start)
    if not match:
        raise BtxConversionError("invalid_pdf_dictionary", f"Invalid /{name} array.")
    return _numbers(match.group(1), count, f"/{name}")


def _appearance_pointer(text: str) -> str | None:
    start = _pdf_field_start(text, 'AP')
    if start is None or not text.startswith('<<', start):
        return None
    # The supported /AP is a flat dictionary. Never retry at a nested or later
    # /AP when the first dictionary is malformed or lacks its exact /N pointer.
    end = re.compile(r'<<|>>').search(text, start + 2)
    if end is None or end.group() != '>>':
        return None
    match = re.search(r'/N\s*/BBObjPtr_([^/<>\[\]()\s]+)', text[start + 2:end.start()])
    return match.group(1) if match else None


def _pdf_string(text: str, name: str) -> str:
    match = re.search(rf"/{re.escape(name)}\s*\(", text)
    if not match:
        return ""
    out, escaped, depth = [], False, 0
    for char in text[match.end():]:
        if escaped:
            out.append(char); escaped = False
        elif char == "\\":
            escaped = True
        elif char == "(":
            depth += 1; out.append(char)
        elif char == ")" and depth:
            depth -= 1; out.append(char)
        elif char == ")":
            return "".join(out)
        else:
            out.append(char)
    raise BtxConversionError("invalid_pdf_dictionary", f"Unterminated /{name} string.")


def _matrix_apply(matrix: tuple[float, float, float, float, float, float], x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return _bounded((a * x + c * y + e, b * x + d * y + f), "Transformed point")


def _matrix_concat(left: tuple[float, ...], right: tuple[float, ...]) -> tuple[float, ...]:
    a, b, c, d, e, f = left; g, h, i, j, k, l = right
    return _bounded((a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j, a*k+c*l+e, b*k+d*l+f), "Composed matrix")


def _parse_paths(stream: bytes, form_matrix: tuple[float, ...], *, budget: _Budget | None = None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    # Iterate rather than allocating a token list before any work checks.
    words = TOKEN.finditer(re.sub(r"%[^\r\n]*", "", stream.decode("latin-1")))
    ctm = _bounded(form_matrix, "Form matrix")
    states: list[tuple[tuple[float, ...], dict[str, Any]]] = []
    style: dict[str, Any] = {"stroke": "#000000", "fill": "#000000", "width": 1.0, "cap": 0, "join": 0, "dash": (), "dash_phase": 0}
    operands: list[str] = []
    current: list[tuple[str, tuple[float, ...]]] = []
    paths: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    warning_keys: set[tuple[str, str]] = set()
    style_work = 0
    segment_count = 0
    known = {"q", "Q", "cm", "w", "J", "j", "d", "G", "g", "RG", "rg", "K", "k", "gs", "m", "l", "c", "v", "y", "h", "re", "S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n", "W", "W*", "M", "ri", "i"}
    paints = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*"}

    def warn(code: str, detail: str) -> None:
        key = (code, detail)
        if key in warning_keys:
            return
        if len(warnings) >= MAX_WARNINGS_PER_SYMBOL:
            raise BtxConversionError('warning_limit', 'Symbol contains too many distinct unsupported effects.')
        warning_keys.add(key)
        warnings.append(_warning(code, detail))

    def style_charge(amount: int) -> None:
        nonlocal style_work
        style_work += amount
        if style_work > MAX_STYLE_WORK_PER_SYMBOL:
            raise BtxConversionError('style_work_limit', 'Symbol style/output work exceeds the limit.')
        if budget:
            budget.charge('STYLE_WORK', amount, 'style_work_limit')

    def consume(count: int) -> list[float]:
        if len(operands) < count:
            raise BtxConversionError("invalid_pdf_operators", "PDF graphics operator has too few operands.")
        values = operands[-count:]; del operands[-count:]
        try:
            return list(_bounded(tuple(float(value) for value in values), "PDF operand"))
        except ValueError as exc:
            raise BtxConversionError("invalid_pdf_operators", "PDF graphics operand is not numeric.") from exc

    def append(kind: str, values: list[float]) -> None:
        nonlocal segment_count
        segment_count += 1
        if segment_count > MAX_PATHS_PER_SYMBOL:
            raise BtxConversionError("path_limit", "Symbol contains too many path segments.")
        transformed: list[float] = []
        for index in range(0, len(values), 2):
            transformed.extend(_matrix_apply(ctm, values[index], values[index + 1]))
        current.append((kind, tuple(transformed)))

    def point() -> tuple[float, ...]:
        if not current:
            raise BtxConversionError("invalid_pdf_operators", "Path operator has no current point.")
        if current[-1][0] == "Z":
            return next(values[-2:] for kind, values in reversed(current) if kind == "M")
        return current[-1][1][-2:]

    def color(count: int) -> list[float]:
        values = consume(count)
        if any(value < 0 or value > 1 for value in values):
            raise BtxConversionError("invalid_pdf_style", "Color components must be in [0, 1].")
        return values

    for token_count, match in enumerate(words, 1):
        if token_count > MAX_TOKENS_PER_SYMBOL:
            raise BtxConversionError('token_limit', 'Symbol contains too many graphics tokens.')
        if budget:
            budget.charge('TOKENS', 1, 'token_limit')
        word = match.group()
        operator = word if word in known else None
        if operator is None:
            if not (re.fullmatch(NUMBER, word) or word.startswith(("[", "/"))):
                warn("unsupported_pdf_operator", f"Unsupported PDF operator {word[:80]}.")
                operands.clear()
                continue
            if len(operands) >= MAX_OPERANDS:
                raise BtxConversionError('operand_limit', 'PDF operand stack exceeds the limit.')
            operands.append(word)
            continue
        if operator == "q":
            if len(states) >= MAX_GRAPHICS_STATE_DEPTH:
                raise BtxConversionError("graphics_state_limit", "PDF graphics state nesting exceeds the limit.")
            states.append((ctm, dict(style))); operands.clear()
        elif operator == "Q":
            if not states: raise BtxConversionError("invalid_pdf_operators", "Unbalanced PDF graphics state.")
            ctm, style = states.pop(); operands.clear()
        elif operator == "cm": ctm = _matrix_concat(ctm, tuple(consume(6)))
        elif operator == "w":
            value = consume(1)[0]
            if value <= 0:
                raise BtxConversionError("invalid_pdf_style", "Only positive line widths are supported.")
            style["width"] = value
        elif operator in {"J", "j"}:
            value = consume(1)[0]
            if value not in (0, 1, 2):
                raise BtxConversionError("invalid_pdf_style", "Invalid line cap/join.")
            style["cap" if operator == "J" else "join"] = int(value)
        elif operator == "d":
            if len(operands) != 2 or not (operands[0].startswith('[') and operands[0].endswith(']')):
                raise BtxConversionError("invalid_pdf_style", "Invalid dash pattern.")
            # Split with a bound; never repeat a variable-length numeric regex.
            entries = operands[0][1:-1].split(None, MAX_DASH_ELEMENTS)
            if len(entries) > MAX_DASH_ELEMENTS:
                raise BtxConversionError('dash_limit', 'Dash pattern contains too many elements.')
            if any(not re.fullmatch(NUMBER, entry) for entry in entries):
                raise BtxConversionError('invalid_pdf_style', 'Invalid dash pattern.')
            style_charge(len(entries))
            phase = consume(1)[0]
            operands.pop()
            dash = _bounded(tuple(float(v) for v in entries), "Dash pattern")
            if any(v < 0 for v in dash) or (dash and not any(dash)):
                raise BtxConversionError("invalid_pdf_style", "Dash lengths must be nonnegative and not all zero.")
            style["dash"], style["dash_phase"] = dash, phase
        elif operator in {"G", "g"}:
            value = color(1)[0]; channel = round(value * 255)
            style["stroke" if operator == "G" else "fill"] = f"#{channel:02x}{channel:02x}{channel:02x}"
        elif operator in {"RG", "rg"}:
            red, green, blue = color(3)
            style["stroke" if operator == "RG" else "fill"] = "#%02x%02x%02x" % (round(red*255), round(green*255), round(blue*255))
        elif operator in {"K", "k"}:
            c, m, y, k = color(4); red, green, blue = (1-min(1, c+k), 1-min(1, m+k), 1-min(1, y+k))
            style["stroke" if operator == "K" else "fill"] = "#%02x%02x%02x" % (round(red*255), round(green*255), round(blue*255))
        elif operator == "m": append("M", consume(2))
        elif operator == "l": point(); append("L", consume(2))
        elif operator == "c": point(); append("C", consume(6))
        elif operator == "v":
            first = point(); append("C", consume(4))
            current[-1] = ("C", (*first, *current[-1][1]))
        elif operator == "y":
            point(); values = consume(4); append("C", [*values, values[-2], values[-1]])
        elif operator == "h": point(); append("Z", [])
        elif operator == "re":
            x, y, width, height = consume(4); append("M", [x, y]); append("L", [x+width, y]); append("L", [x+width, y+height]); append("L", [x, y+height]); append("Z", [])
        elif operator in paints:
            if not current:
                operands.clear()
                continue
            if operator in {"s", "b", "b*"} and current:
                append("Z", [])
            # Charge serialized style entries, not just geometry or tuple storage.
            style_charge(8 + len(style['dash']))
            painted = dict(style)
            # Coordinates have been flattened; only similarity transforms admit
            # a single scalar stroke width/dash. Other transforms are auditable.
            a, b, c, d, _, _ = ctm
            sx, sy = math.hypot(a, b), math.hypot(c, d)
            if not math.isclose(sx, sy) or not math.isclose(a*c+b*d, 0, abs_tol=1e-9):
                warn("unsupported_stroke_transform", "Nonuniform/sheared stroke style is approximated after coordinate transformation.")
            painted["width"] = _bounded((style["width"] * sx,), "Transformed stroke width")[0]
            painted["dash"] = style['dash'] if sx == 1 else _bounded(tuple(v * sx for v in style["dash"]), "Transformed dash")
            painted["dash_phase"] = _bounded((style["dash_phase"] * sx,), "Transformed dash phase")[0]
            painted["segments"] = list(current)
            painted["stroke_enabled"] = operator in {"S", "s", "B", "B*", "b", "b*"}
            painted["fill_enabled"] = operator in {"f", "F", "f*", "B", "B*", "b", "b*"}
            painted["evenodd"] = operator in {"f*", "B*", "b*"}
            paths.append(painted); current.clear(); operands.clear()
        elif operator == "n":
            current.clear(); operands.clear()
        else:
            warn("unsupported_pdf_operator", f"Unsupported PDF graphics effect {operator}.")
            operands.clear()
    if states:
        raise BtxConversionError("invalid_pdf_operators", "Unbalanced PDF graphics state.")
    return paths, warnings


def _svg_path(segments: list[tuple[str, tuple[float, ...]]]) -> str:
    return " ".join(kind if not values else kind + " " + " ".join(f"{value:.6g}" for value in values) for kind, values in segments)


def _safe_name(value: str, ordinal: int) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._")[:120]
    return cleaned or f"symbol_{ordinal + 1:03d}"


def _emit_svg(symbol: dict[str, Any], path: Path) -> None:
    width, height = symbol["width_points"], symbol["height_points"]
    _dimensions(width, height)
    title = html.escape(symbol["subject"] or symbol["file_stem"])
    body = []
    for item in symbol["paths"]:
        if not item["segments"]: continue
        attrs = [f'd="{_svg_path(item["segments"])}"', f'stroke="{item["stroke"] if item["stroke_enabled"] else "none"}"', f'fill="{item["fill"] if item["fill_enabled"] else "none"}"', f'stroke-width="{item["width"]:.6g}"']
        attrs += [f'stroke-linecap="{("butt", "round", "square")[item["cap"]]}"', f'stroke-linejoin="{("miter", "round", "bevel")[item["join"]]}"', 'stroke-miterlimit="10"']
        if item["dash"]:
            attrs += ['stroke-dasharray="' + " ".join(f"{v:.6g}" for v in item["dash"]) + '"', f'stroke-dashoffset="{item["dash_phase"]:.6g}"']
        if item["evenodd"]: attrs.append('fill-rule="evenodd"')
        body.append("<path " + " ".join(attrs) + "/>")
    payload = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.6g}pt" height="{height:.6g}pt" viewBox="0 0 {width:.6g} {height:.6g}" role="img" aria-labelledby="title desc"><title id="title">{title}</title><desc id="desc">Bluebeam BTX symbol converted to SVG.</desc><g transform="translate(0 {height:.6g}) scale(1 -1)">' + "".join(body) + "</g></svg>"
    path.write_text(payload, encoding="utf-8")


def _line_subpaths(segments: list[tuple[str, tuple[float, ...]]]) -> list[list[tuple[float, float]]]:
    """Flatten curves without inventing edges between move-to subpaths."""
    output: list[list[tuple[float, float]]] = []
    points: list[tuple[float, float]] = []
    current: tuple[float, float] | None = None
    for kind, values in segments:
        if kind == "M":
            current = (values[-2], values[-1]); points = [current]; output.append(points)
        elif kind == "L": current = (values[-2], values[-1]); points.append(current)
        elif kind == "C":
            if current is None: continue
            x0, y0 = current; x1, y1, x2, y2, x3, y3 = values
            for step in range(1, 13):
                t = step / 12; u = 1-t
                points.append((u**3*x0 + 3*u*u*t*x1 + 3*u*t*t*x2 + t**3*x3, u**3*y0 + 3*u*u*t*y1 + 3*u*t*t*y2 + t**3*y3))
            current = (x3, y3)
        elif kind == "Z" and points:
            if points[-1] != points[0]: points.append(points[0])
            current = points[0]
    return output


def _emit_dxf(symbol: dict[str, Any], path: Path, units: str) -> None:
    factors = {"pt": (1.0, 0), "mm": (25.4 / 72, 4), "in": (1 / 72, 1)}
    if units not in factors: raise BtxConversionError("invalid_dxf_units", "DXF units must be pt, mm, or in.")
    factor, insunits = factors[units]
    lines = ["0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1015", "9", "$INSUNITS", "70", str(insunits), "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
    for item in symbol["paths"]:
        for points in _line_subpaths(item["segments"]):
            if item["fill_enabled"] and points and points[0] != points[-1]:
                points = [*points, points[0]]
            for start, end in zip(points, points[1:]):
                lines += ["0", "LINE", "8", "BTX_SYMBOL", "10", f"{start[0]*factor:.8g}", "20", f"{start[1]*factor:.8g}", "11", f"{end[0]*factor:.8g}", "21", f"{end[1]*factor:.8g}"]
    lines += ["0", "ENDSEC", "0", "EOF"]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def _emit_png(symbol: dict[str, Any], path: Path) -> None:
    if Image is None: raise BtxConversionError("png_renderer_unavailable", "Pillow is required for PNG previews.")
    width, height = symbol["width_points"], symbol["height_points"]
    _dimensions(width, height)
    scale = min(4.0, 512 / max(width, height))
    size = (max(1, round(width*scale))+8, max(1, round(height*scale))+8)
    if max(size) > MAX_PNG_SIDE or size[0]*size[1] > MAX_PNG_PIXELS:
        raise BtxConversionError("png_size_limit", "PNG preview exceeds the allocation limit.")
    flattened = [(item, _line_subpaths(item["segments"])) for item in symbol["paths"]]
    if any(item["fill_enabled"] and len(subpaths) > 1 for item, subpaths in flattened):
        raise BtxConversionError("unsupported_compound_fill", "PNG compound fills/holes are not supported; use SVG for the fill rule.")
    image = Image.new("RGBA", size, "white")
    draw = ImageDraw.Draw(image)
    for item, subpaths in flattened:
        for points in subpaths:
            pixels = [(4+x*scale, 4+(height-y)*scale) for x, y in points]
            if len(pixels) > 2 and item["fill_enabled"]:
                draw.polygon(pixels, fill=item["fill"])
            if len(pixels) > 1 and item["stroke_enabled"]:
                # Raster strokes are a bounded preview, not a PDF rasterizer.
                stroke_width = min(MAX_PNG_SIDE, max(1, round(item["width"]*scale)))
                draw.line(pixels, fill=item["stroke"], width=stroke_width)
    image.save(path, format="PNG", optimize=True)


def convert_btx(input_path: str | Path, output_dir: str | Path, *, formats: tuple[str, ...] = ("svg", "dxf", "png"), dxf_units: str = "mm") -> dict[str, Any]:
    """Convert a direct BTX or ZIP-with-one-BTX into isolated symbol assets."""
    source = Path(input_path); output = Path(output_dir); data, source_name = _read_input(source)
    # XML's UTF-16/32 encodings interleave ASCII markup with NUL bytes.
    # Scan before parsing so ElementTree never expands internal entities.
    markup = data.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in markup or b"<!ENTITY" in markup:
        raise BtxConversionError("unsafe_xml", "BTX XML may not declare DTDs or entities.")
    if not formats or any(fmt not in {"svg", "dxf", "png"} for fmt in formats):
        raise BtxConversionError("invalid_formats", "Formats must be svg, dxf, or png.")
    if "dxf" in formats and dxf_units not in {"pt", "mm", "in"}:
        raise BtxConversionError("invalid_dxf_units", "DXF units must be pt, mm, or in.")
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, LookupError, UnicodeError) as exc:
        raise BtxConversionError("malformed_xml", "BTX XML could not be parsed.") from exc
    if root.tag.rsplit("}", 1)[-1] != "BluebeamRevuToolSet":
        raise BtxConversionError("unexpected_xml_root", "Expected BluebeamRevuToolSet XML root.")
    version = root.attrib.get("Version")
    warnings = [] if version == "1" else [_warning("unverified_btx_version", f"BTX Version {version or 'unknown'} is not verified.")]
    items = root.findall("ToolChestItem")
    if len(items) > MAX_SYMBOLS: raise BtxConversionError("symbol_limit", "BTX contains too many symbols.")
    if sum(len(item.findall('Resources')) for item in items) > MAX_RESOURCES_PER_LIBRARY:
        raise BtxConversionError('resource_count_limit', 'Library contains too many resources.')
    library_budget = _Budget()
    symbol_budgets = [_Budget(library_budget) for _ in items]
    title = _inflate_hex(root.findtext("Title"), label="tool-set title", budget=library_budget).decode("utf-8", "replace")
    output.mkdir(parents=True, exist_ok=True)
    result_symbols = []
    used_stems: set[str] = set()
    reserved_stems: set[str] = set()
    prepass_errors: dict[int, BtxConversionError] = {}
    for ordinal, item in enumerate(items):
        try:
            if len(item.findall('Resources')) > MAX_RESOURCES_PER_SYMBOL:
                raise BtxConversionError('resource_count_limit', 'Symbol contains too many resources.')
            raw = _inflate_hex(item.findtext("Raw"), label="annotation name", budget=symbol_budgets[ordinal]).decode("latin-1")
            reserved_stems.add(_safe_name(_pdf_string(raw, "Subj") or f"Symbol {ordinal + 1}", ordinal).casefold())
        except BtxConversionError as exc:
            prepass_errors[ordinal] = exc
    for ordinal, item in enumerate(items):
        budget = symbol_budgets[ordinal]
        written: list[Path] = []
        try:
            if ordinal in prepass_errors:
                raise prepass_errors[ordinal]
            raw = _inflate_hex(item.findtext("Raw"), label=f"symbol {ordinal + 1} annotation", budget=budget).decode("latin-1")
            subject = _pdf_string(raw, "Subj") or f"Symbol {ordinal + 1}"
            rect = _pdf_array(raw, "Rect", 4)
            pointer = _appearance_pointer(raw)
            _dimensions(rect[2]-rect[0], rect[3]-rect[1])
            if not pointer: raise BtxConversionError("missing_appearance_pointer", "Annotation has no /AP /N appearance pointer.")
            # Resolve exact normalized IDs first, rejecting ambiguous aliases.
            # Only the selected Form body is ever decompressed or retained.
            resources: dict[str, ET.Element] = {}
            for resource in item.findall("Resources"):
                identifier = _inflate_hex(resource.findtext("ID"), label="resource ID", budget=budget).decode("latin-1").removeprefix('BBObjPtr_')
                if identifier in resources:
                    raise BtxConversionError('duplicate_resource_id', 'Resource IDs/aliases must be unique within a symbol.')
                resources[identifier] = resource
            selected = resources.get(pointer)
            if selected is None: raise BtxConversionError("missing_appearance_resource", f"No resource matches appearance pointer {pointer}.")
            resource = _inflate_hex(selected.findtext('Data'), label=f'resource {pointer}', budget=budget)
            stream_match = re.search(rb"(?:\r?\n)stream\r?\n", resource)
            if not stream_match: raise BtxConversionError("invalid_form_xobject", "Appearance resource has no stream.")
            header = resource[:stream_match.start()].decode("latin-1")
            if "/Subtype/Form" not in header: raise BtxConversionError("invalid_form_xobject", "Appearance resource is not a Form XObject.")
            if "/FlateDecode" not in header: raise BtxConversionError("unsupported_pdf_filter", "Only FlateDecode appearance streams are supported.")
            encoded = resource[stream_match.end():]
            length_match = re.search(r'/Length\s+(\d+)\b', header)
            if '/Length' in header:
                if not length_match or re.match(r'\s+\d+\s+R\b', header[length_match.end():]):
                    raise BtxConversionError('invalid_form_xobject', 'Only direct stream /Length is supported.')
                length = int(length_match.group(1)) if len(length_match.group(1)) <= 10 else len(encoded) + 1
                if length > len(encoded) or not STREAM_TAIL.fullmatch(encoded[length:]):
                    raise BtxConversionError('invalid_form_xobject', 'Stream /Length does not match its endstream boundary.')
                stream = _inflate(encoded[:length], label=f'symbol {ordinal + 1} appearance stream', budget=budget)
            else:
                # zlib EOF, not a binary substring search or rstrip, determines
                # the payload boundary. Validate the entire remaining suffix.
                stream = _inflate(encoded, label=f'symbol {ordinal + 1} appearance stream', budget=budget, stream_tail=True)
            bbox = _pdf_array(header, "BBox", 4)
            _dimensions(bbox[2]-bbox[0], bbox[3]-bbox[1])
            matrix = _pdf_array(header, "Matrix", 6, default=(1, 0, 0, 1, 0, 0))
            paths, symbol_warnings = _parse_paths(stream, matrix, budget=budget)
            if "png" in formats:
                symbol_warnings.append(_warning("png_preview_approximation", "PNG curves use 12 chords; stroke caps/joins/dashes and self-intersecting fill rules are approximate, not PDF-faithful."))
            if "dxf" in formats:
                symbol_warnings.append(_warning("dxf_geometry_only", "DXF exports 12-chord curve outlines only; fills, colors, stroke widths, caps, joins and dashes are not represented."))
            file_stem = _safe_name(subject, ordinal)
            base_stem = file_stem
            if file_stem.casefold() in used_stems:
                suffix = 2
                while True:
                    file_stem = f"{base_stem}_{suffix}"
                    suffix += 1
                    if file_stem.casefold() not in used_stems | reserved_stems:
                        break
            used_stems.add(file_stem.casefold())
            symbol = {"ordinal": ordinal, "subject": subject, "internal_name": item.findtext("Name") or None, "annotation_type": item.findtext("Type") or None, "rect_points": list(rect), "width_points": rect[2]-rect[0], "height_points": rect[3]-rect[1], "bbox": list(bbox), "form_matrix": list(matrix), "paths": paths, "file_stem": file_stem, "warnings": symbol_warnings}
            files = {}
            for fmt in dict.fromkeys(formats):
                target = output / f'{file_stem}.{fmt}'
                written.append(target)
                if fmt == 'svg': _emit_svg(symbol, target)
                elif fmt == 'dxf': _emit_dxf(symbol, target, dxf_units)
                else: _emit_png(symbol, target)
                files[fmt] = target.name
            result_symbols.append({key: value for key, value in symbol.items() if key not in {"paths", "file_stem"}} | files)
        except BtxConversionError as exc:
            for target in written:
                target.unlink(missing_ok=True)
            result_symbols.append({"ordinal": ordinal, "subject": item.findtext("Name") or f"Symbol {ordinal + 1}", "status": "failed", "warnings": [_warning(exc.code, exc.detail)]})
    manifest = {"schema_version": "1.0", "source_filename": source_name, "source_sha256": hashlib.sha256(data).hexdigest(), "tool_set_title": title, "btx_version": version, "dxf_units": dxf_units, "formats": list(formats), "warnings": warnings, "symbols": result_symbols, "successful_symbol_count": sum("status" not in symbol for symbol in result_symbols), "failed_symbol_count": sum(symbol.get("status") == "failed" for symbol in result_symbols)}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
