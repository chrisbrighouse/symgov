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
NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"
TOKEN = re.compile(r"\[[^]]*\]|/[^\s<>{}\[\]()/%]+|" + NUMBER + r"|[^\s<>{}\[\]()/%]+")


class BtxConversionError(ValueError):
    """Expected, structured BTX conversion failure."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _warning(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _inflate(data: bytes, *, label: str, maximum: int = MAX_EXPANDED_BYTES) -> bytes:
    try:
        inflater = zlib.decompressobj()
        output = inflater.decompress(data, maximum + 1)
        if len(output) > maximum or inflater.unconsumed_tail:
            raise BtxConversionError("btx_expanded_size_limit", f"{label} exceeds the expanded size limit.")
        output += inflater.flush(maximum + 1 - len(output))
    except zlib.error as exc:
        raise BtxConversionError("invalid_zlib_payload", f"{label} is not a valid zlib payload.") from exc
    if len(output) > maximum:
        raise BtxConversionError("btx_expanded_size_limit", f"{label} exceeds the expanded size limit.")
    return output


def _inflate_hex(value: str | None, *, label: str) -> bytes:
    text = (value or "").strip()
    if not text or len(text) % 2 or re.search(r"[^0-9a-fA-F]", text):
        raise BtxConversionError("invalid_hex_payload", f"{label} is not hexadecimal data.")
    return _inflate(bytes.fromhex(text), label=label)


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
    values = tuple(float(item) for item in re.findall(NUMBER, value))
    if len(values) != count:
        raise BtxConversionError("invalid_pdf_dictionary", f"{name} must contain {count} numbers.")
    return values


def _pdf_array(text: str, name: str, count: int) -> tuple[float, ...]:
    match = re.search(rf"/{re.escape(name)}\s*\[([^]]+)\]", text)
    if not match:
        raise BtxConversionError("invalid_pdf_dictionary", f"Missing /{name}.")
    return _numbers(match.group(1), count, f"/{name}")


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
    return a * x + c * y + e, b * x + d * y + f


def _matrix_concat(left: tuple[float, ...], right: tuple[float, ...]) -> tuple[float, ...]:
    a, b, c, d, e, f = left; g, h, i, j, k, l = right
    return (a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j, a*k+c*l+e, b*k+d*l+f)


def _parse_paths(stream: bytes, form_matrix: tuple[float, ...]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    words = TOKEN.findall(stream.decode("latin-1"))
    ctm = form_matrix
    states: list[tuple[tuple[float, ...], dict[str, Any]]] = []
    style: dict[str, Any] = {"stroke": "#000000", "fill": "none", "width": 1.0, "cap": 0, "join": 0}
    operands: list[str] = []
    current: list[tuple[str, tuple[float, ...]]] = []
    paths: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    known = {"q", "Q", "cm", "w", "J", "j", "d", "G", "g", "RG", "rg", "K", "k", "gs", "m", "l", "c", "v", "y", "h", "re", "S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n", "W", "W*", "M", "ri", "i"}
    paints = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*"}

    def consume(count: int) -> list[float]:
        if len(operands) < count:
            raise BtxConversionError("invalid_pdf_operators", "PDF graphics operator has too few operands.")
        values = operands[-count:]; del operands[-count:]
        try:
            return [float(value) for value in values]
        except ValueError as exc:
            raise BtxConversionError("invalid_pdf_operators", "PDF graphics operand is not numeric.") from exc

    def append(kind: str, values: list[float]) -> None:
        if len(current) > MAX_PATHS_PER_SYMBOL:
            raise BtxConversionError("path_limit", "Symbol contains too many path segments.")
        transformed: list[float] = []
        for index in range(0, len(values), 2):
            transformed.extend(_matrix_apply(ctm, values[index], values[index + 1]))
        current.append((kind, tuple(transformed)))

    for word in words:
        operator = word if word in known else None
        if operator is None:
            if word in {"BT", "ET", "Tf", "Tj", "TJ", "Do", "BI", "ID", "EI"}:
                warnings.append(_warning("unsupported_pdf_operator", f"Unsupported PDF operator {word}."))
            operands.append(word)
            continue
        if operator == "q":
            states.append((ctm, dict(style))); operands.clear()
        elif operator == "Q":
            if not states: raise BtxConversionError("invalid_pdf_operators", "Unbalanced PDF graphics state.")
            ctm, style = states.pop(); operands.clear()
        elif operator == "cm": ctm = _matrix_concat(ctm, tuple(consume(6)))
        elif operator == "w": style["width"] = consume(1)[0]
        elif operator == "J": style["cap"] = int(consume(1)[0])
        elif operator == "j": style["join"] = int(consume(1)[0])
        elif operator in {"G", "g"}:
            value = max(0, min(1, consume(1)[0])); channel = round(value * 255)
            style["stroke" if operator == "G" else "fill"] = f"#{channel:02x}{channel:02x}{channel:02x}"
        elif operator in {"RG", "rg"}:
            red, green, blue = (max(0, min(1, value)) for value in consume(3))
            style["stroke" if operator == "RG" else "fill"] = "#%02x%02x%02x" % (round(red*255), round(green*255), round(blue*255))
        elif operator in {"K", "k"}:
            c, m, y, k = consume(4); red, green, blue = (1-min(1, c+k), 1-min(1, m+k), 1-min(1, y+k))
            style["stroke" if operator == "K" else "fill"] = "#%02x%02x%02x" % (round(red*255), round(green*255), round(blue*255))
        elif operator == "m": append("M", consume(2))
        elif operator == "l": append("L", consume(2))
        elif operator == "c": append("C", consume(6))
        elif operator == "v":
            if not current: raise BtxConversionError("invalid_pdf_operators", "PDF v operator has no current point.")
            point = current[-1][1][-2:]; append("C", [*point, *consume(4)])
        elif operator == "y":
            values = consume(4); append("C", [*values, values[-2], values[-1]])
        elif operator == "h": current.append(("Z", ()))
        elif operator == "re":
            x, y, width, height = consume(4); append("M", [x, y]); append("L", [x+width, y]); append("L", [x+width, y+height]); append("L", [x, y+height]); current.append(("Z", ()))
        elif operator in paints:
            painted = dict(style)
            painted["segments"] = list(current)
            painted["stroke_enabled"] = operator in {"S", "s", "B", "B*", "b", "b*"}
            painted["fill_enabled"] = operator in {"f", "F", "f*", "B", "B*", "b", "b*"}
            painted["evenodd"] = operator in {"f*", "B*", "b*"}
            paths.append(painted); current.clear(); operands.clear()
        else:
            operands.clear()
    return paths, warnings


def _svg_path(segments: list[tuple[str, tuple[float, ...]]]) -> str:
    return " ".join(kind if not values else kind + " " + " ".join(f"{value:.6g}" for value in values) for kind, values in segments)


def _safe_name(value: str, ordinal: int) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._")
    return cleaned or f"symbol_{ordinal + 1:03d}"


def _emit_svg(symbol: dict[str, Any], path: Path) -> None:
    width, height = symbol["width_points"], symbol["height_points"]
    title = html.escape(symbol["subject"] or symbol["file_stem"])
    body = []
    for item in symbol["paths"]:
        if not item["segments"]: continue
        attrs = [f'd="{_svg_path(item["segments"])}"', f'stroke="{item["stroke"] if item["stroke_enabled"] else "none"}"', f'fill="{item["fill"] if item["fill_enabled"] else "none"}"', f'stroke-width="{item["width"]:.6g}"']
        if item["evenodd"]: attrs.append('fill-rule="evenodd"')
        body.append("<path " + " ".join(attrs) + "/>")
    payload = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.6g}pt" height="{height:.6g}pt" viewBox="0 0 {width:.6g} {height:.6g}" role="img" aria-labelledby="title desc"><title id="title">{title}</title><desc id="desc">Bluebeam BTX symbol converted to SVG.</desc><g transform="translate(0 {height:.6g}) scale(1 -1)">' + "".join(body) + "</g></svg>"
    path.write_text(payload, encoding="utf-8")


def _line_points(segments: list[tuple[str, tuple[float, ...]]]) -> list[tuple[float, float]]:
    output: list[tuple[float, float]] = []; current: tuple[float, float] | None = None
    for kind, values in segments:
        if kind == "M": current = values[-2:]; output.append(current)
        elif kind == "L": current = values[-2:]; output.append(current)
        elif kind == "C":
            if current is None: continue
            x0, y0 = current; x1, y1, x2, y2, x3, y3 = values
            for step in range(1, 13):
                t = step / 12; u = 1-t
                output.append((u**3*x0 + 3*u*u*t*x1 + 3*u*t*t*x2 + t**3*x3, u**3*y0 + 3*u*u*t*y1 + 3*u*t*t*y2 + t**3*y3))
            current = (x3, y3)
    return output


def _emit_dxf(symbol: dict[str, Any], path: Path, units: str) -> None:
    factors = {"pt": (1.0, 0), "mm": (25.4 / 72, 4), "in": (1 / 72, 1)}
    if units not in factors: raise BtxConversionError("invalid_dxf_units", "DXF units must be pt, mm, or in.")
    factor, insunits = factors[units]
    lines = ["0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1015", "9", "$INSUNITS", "70", str(insunits), "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
    for item in symbol["paths"]:
        points = _line_points(item["segments"])
        for start, end in zip(points, points[1:]):
            lines += ["0", "LINE", "8", "BTX_SYMBOL", "10", f"{start[0]*factor:.8g}", "20", f"{start[1]*factor:.8g}", "11", f"{end[0]*factor:.8g}", "21", f"{end[1]*factor:.8g}"]
    lines += ["0", "ENDSEC", "0", "EOF"]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def _emit_png(symbol: dict[str, Any], path: Path) -> None:
    if Image is None: raise BtxConversionError("png_renderer_unavailable", "Pillow is required for PNG previews.")
    width, height = symbol["width_points"], symbol["height_points"]
    scale = min(4.0, max(1.0, 512 / max(width, height, 1)))
    image = Image.new("RGBA", (max(1, round(width*scale)+8), max(1, round(height*scale)+8)), "white")
    draw = ImageDraw.Draw(image)
    for item in symbol["paths"]:
        points = _line_points(item["segments"])
        if len(points) > 1 and item["stroke_enabled"]:
            draw.line([(4+x*scale, 4+(height-y)*scale) for x, y in points], fill=item["stroke"], width=max(1, round(item["width"]*scale)))
    image.save(path, format="PNG", optimize=True)


def convert_btx(input_path: str | Path, output_dir: str | Path, *, formats: tuple[str, ...] = ("svg", "dxf", "png"), dxf_units: str = "mm") -> dict[str, Any]:
    """Convert a direct BTX or ZIP-with-one-BTX into isolated symbol assets."""
    source = Path(input_path); output = Path(output_dir); data, source_name = _read_input(source)
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise BtxConversionError("unsafe_xml", "BTX XML may not declare DTDs or entities.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise BtxConversionError("malformed_xml", "BTX XML could not be parsed.") from exc
    if root.tag.rsplit("}", 1)[-1] != "BluebeamRevuToolSet":
        raise BtxConversionError("unexpected_xml_root", "Expected BluebeamRevuToolSet XML root.")
    version = root.attrib.get("Version")
    warnings = [] if version == "1" else [_warning("unverified_btx_version", f"BTX Version {version or 'unknown'} is not verified.")]
    title = _inflate_hex(root.findtext("Title"), label="tool-set title").decode("utf-8", "replace")
    items = root.findall("ToolChestItem")
    if len(items) > MAX_SYMBOLS: raise BtxConversionError("symbol_limit", "BTX contains too many symbols.")
    output.mkdir(parents=True, exist_ok=True)
    result_symbols = []
    used_stems: dict[str, int] = {}
    for ordinal, item in enumerate(items):
        try:
            raw = _inflate_hex(item.findtext("Raw"), label=f"symbol {ordinal + 1} annotation").decode("latin-1")
            subject = _pdf_string(raw, "Subj") or f"Symbol {ordinal + 1}"
            rect = _pdf_array(raw, "Rect", 4); pointer_match = re.search(r"/AP\s*<<(?:(?!>>).)*?/N\s*/BBObjPtr_([^/<>\[\]()\s]+)", raw, re.S)
            if not pointer_match: raise BtxConversionError("missing_appearance_pointer", "Annotation has no /AP /N appearance pointer.")
            pointer = pointer_match.group(1)
            resources: dict[str, bytes] = {}
            for resource in item.findall("Resources"):
                identifier = _inflate_hex(resource.findtext("ID"), label="resource ID").decode("latin-1")
                resources[identifier.removeprefix("BBObjPtr_")] = _inflate_hex(resource.findtext("Data"), label=f"resource {identifier}")
            resource = resources.get(pointer)
            if resource is None: raise BtxConversionError("missing_appearance_resource", f"No resource matches appearance pointer {pointer}.")
            stream_match = re.search(rb"(?:\r?\n)stream\r?\n", resource)
            if not stream_match: raise BtxConversionError("invalid_form_xobject", "Appearance resource has no stream.")
            header = resource[:stream_match.start()].decode("latin-1")
            if "/Subtype/Form" not in header: raise BtxConversionError("invalid_form_xobject", "Appearance resource is not a Form XObject.")
            encoded = resource[stream_match.end():resource.rfind(b"endstream")].rstrip(b"\r\n")
            if "/FlateDecode" not in header: raise BtxConversionError("unsupported_pdf_filter", "Only FlateDecode appearance streams are supported.")
            stream = _inflate(encoded, label=f"symbol {ordinal + 1} appearance stream")
            bbox = _pdf_array(header, "BBox", 4); matrix_match = re.search(r"/Matrix\s*\[([^]]+)\]", header)
            matrix = _numbers(matrix_match.group(1), 6, "/Matrix") if matrix_match else (1, 0, 0, 1, 0, 0)
            paths, symbol_warnings = _parse_paths(stream, matrix)
            file_stem = _safe_name(subject, ordinal)
            used_stems[file_stem] = used_stems.get(file_stem, 0) + 1
            if used_stems[file_stem] > 1:
                file_stem = f"{file_stem}_{used_stems[file_stem]}"
            symbol = {"ordinal": ordinal, "subject": subject, "internal_name": item.findtext("Name") or None, "annotation_type": item.findtext("Type") or None, "rect_points": list(rect), "width_points": rect[2]-rect[0], "height_points": rect[3]-rect[1], "bbox": list(bbox), "form_matrix": list(matrix), "paths": paths, "file_stem": file_stem, "warnings": symbol_warnings}
            files = {}
            if "svg" in formats: _emit_svg(symbol, output / f"{file_stem}.svg"); files["svg"] = f"{file_stem}.svg"
            if "dxf" in formats: _emit_dxf(symbol, output / f"{file_stem}.dxf", dxf_units); files["dxf"] = f"{file_stem}.dxf"
            if "png" in formats: _emit_png(symbol, output / f"{file_stem}.png"); files["png"] = f"{file_stem}.png"
            result_symbols.append({key: value for key, value in symbol.items() if key not in {"paths", "file_stem"}} | files)
        except BtxConversionError as exc:
            result_symbols.append({"ordinal": ordinal, "subject": item.findtext("Name") or f"Symbol {ordinal + 1}", "status": "failed", "warnings": [_warning(exc.code, exc.detail)]})
    manifest = {"schema_version": "1.0", "source_filename": source_name, "source_sha256": hashlib.sha256(data).hexdigest(), "tool_set_title": title, "btx_version": version, "dxf_units": dxf_units, "formats": list(formats), "warnings": warnings, "symbols": result_symbols, "successful_symbol_count": sum("status" not in symbol for symbol in result_symbols), "failed_symbol_count": sum(symbol.get("status") == "failed" for symbol in result_symbols)}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
