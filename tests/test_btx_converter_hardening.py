"""Content and resource-bound regressions for the deliberately limited BTX renderer."""
import hashlib
import re
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import pytest
from PIL import Image
from symgov_backend.services import btx_converter as c

IDENTITY = (1, 0, 0, 1, 0, 0)
FIXTURE = Path(__file__).resolve().parents[1] / 'integrations/btx/SymGov_BTX_Integration_Handoff/fixtures/Doors.btx'


def paths(data):
    return c._parse_paths(data, IDENTITY)[0]


def symbol(data):
    return {'width_points': 10, 'height_points': 10, 'paths': paths(data), 'subject': 'test', 'file_stem': 'test'}


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16', 'utf-16-le', 'utf-16-be'])
def test_dtd_rejected_before_entity_expansion(tmp_path, encoding):
    p = tmp_path / 'evil.btx'
    p.write_bytes('<!DOCTYPE BluebeamRevuToolSet [<!ENTITY a "boom">]><BluebeamRevuToolSet>&a;</BluebeamRevuToolSet>'.encode(encoding))
    with pytest.raises(c.BtxConversionError) as exc:
        c.convert_btx(p, tmp_path / 'out')
    assert exc.value.code == 'unsafe_xml'


def test_incomplete_zlib_is_not_success():
    with pytest.raises(c.BtxConversionError) as exc:
        c._inflate(zlib.compress(b'valid output')[:-2], label='truncated')
    assert exc.value.code == 'invalid_zlib_payload'


def test_cumulative_segment_limit(monkeypatch):
    monkeypatch.setattr(c, 'MAX_PATHS_PER_SYMBOL', 3)
    with pytest.raises(c.BtxConversionError, match='segments'):
        paths(b'0 0 m 1 1 l S 2 2 m 3 3 l S')


@pytest.mark.parametrize('data', [b'1e999 w', b'1e999 0 m S', b'-1 w', b'99 J', b'0 0 0 -1 k', b'1000000000 0 0 1 0 0 cm'])
def test_numeric_guards_are_structured(data):
    with pytest.raises(c.BtxConversionError):
        paths(data)


@pytest.mark.parametrize('size', [float('inf'), float('nan'), -1, 0, 1e100])
def test_png_rejects_bad_size_before_allocation(tmp_path, monkeypatch, size):
    def allocate(*a, **kw):
        pytest.fail('Image.new reached for invalid geometry')
    monkeypatch.setattr(c.Image, 'new', allocate)
    with pytest.raises(c.BtxConversionError):
        c._emit_png({'width_points': size, 'height_points': 10, 'paths': []}, tmp_path / 'bad.png')


def test_v_first_control_not_transformed_twice():
    p = c._parse_paths(b'2 0 0 2 3 4 cm 1 1 m 2 2 3 3 v S', IDENTITY)[0][0]
    assert p['segments'][1] == ('C', (5, 6, 7, 8, 9, 10))


def test_n_discards_path():
    p = paths(b'0 0 m 9 9 l n 1 1 m 2 2 l S')
    assert p[0]['segments'] == [('M', (1, 1)), ('L', (2, 2))]


@pytest.mark.parametrize('paint', [b's', b'b', b'b*'])
def test_close_paint_closes_last_subpath(paint):
    assert paths(b'1 1 m 8 1 l 8 8 l ' + paint)[0]['segments'][-1] == ('Z', ())


def test_dxf_disconnected_and_closed_geometry(tmp_path):
    p = tmp_path / 'test.dxf'
    c._emit_dxf(symbol(b'1 1 2 2 re 7 7 m 8 7 l S'), p, 'mm')
    text = p.read_text()
    assert text.count('\nLINE\n') == 5
    assert '$INSUNITS\n70\n4\n' in text
    assert '0.35277778' in text


def test_png_has_fill_and_no_disconnected_bridge(tmp_path):
    p = tmp_path / 'test.png'
    c._emit_png(symbol(b'0 g 1 1 2 2 re f 1 7 m 3 7 l 7 3 m 9 3 l S'), p)
    with Image.open(p) as im:
        assert im.getpixel((12, 36))[:3] == (0, 0, 0)
        assert im.getpixel((24, 24))[:3] == (255, 255, 255)


def test_compound_fill_handled_or_explicitly_rejected(tmp_path):
    p = tmp_path / 'hole.png'
    try:
        c._emit_png(symbol(b'0 g 1 1 8 8 re 3 3 4 4 re f*'), p)
    except c.BtxConversionError as exc:
        assert exc.code == 'unsupported_compound_fill'
    else:
        with Image.open(p) as im:
            assert im.getpixel((24, 24))[:3] == (255, 255, 255)
            assert im.getpixel((12, 36))[:3] == (0, 0, 0)


@pytest.mark.parametrize('op', [b'W n', b'/GS0 gs', b'2 M', b'/RelativeColorimetric ri', b'1 i'])
def test_graphics_effects_not_silently_omitted(op):
    try:
        _, warnings = c._parse_paths(b'0 0 m 1 1 l ' + op, IDENTITY)
    except c.BtxConversionError:
        return
    assert any('unsupported' in w['code'] for w in warnings)


def test_svg_cap_join_dash_style(tmp_path):
    p = tmp_path / 'styled.svg'
    c._emit_svg(symbol(b'1 J 2 j [2 3] 1 d 1 1 m 9 9 l S'), p)
    text = p.read_text()
    assert 'stroke-linecap="round"' in text
    assert 'stroke-linejoin="bevel"' in text
    assert 'stroke-dasharray="2 3"' in text
    assert 'stroke-dashoffset="1"' in text


def test_global_casefold_filenames_and_provenance(tmp_path):
    root = ET.parse(FIXTURE).getroot()
    for item, name in zip(root.findall('ToolChestItem'), ['A', 'A', 'A_2', 'a', 'a_2']):
        raw = zlib.decompress(bytes.fromhex(item.findtext('Raw'))).decode('latin-1')
        item.find('Raw').text = zlib.compress(re.sub(r'/Subj\([^)]*\)', '/Subj(' + name + ')', raw, count=1).encode('latin-1')).hex()
    p = tmp_path / 'names.btx'
    ET.ElementTree(root).write(p, encoding='utf-8')
    manifest = c.convert_btx(p, tmp_path / 'out')
    assert manifest['successful_symbol_count'] == 5
    assert manifest['source_sha256'] == hashlib.sha256(p.read_bytes()).hexdigest()
    assert manifest['symbols'][2]['svg'] == 'A_2.svg'
    for fmt in ['svg', 'dxf', 'png']:
        names = [s[fmt].casefold() for s in manifest['symbols']]
        assert len(set(names)) == 5


@pytest.mark.parametrize('value', ['NaN 0 0 10 10', '0 0 1e999 10', '0 0 1000001 10'])
def test_dictionary_numbers_cannot_hide_nonfinite_tokens(value):
    with pytest.raises(c.BtxConversionError):
        c._numbers(value, 4, '/Rect')


@pytest.mark.parametrize('rect', ['0 0 0 10', '10 0 0 10', '0 0 1e999 10', '0 0 1000001 10'])
def test_bad_rect_is_manifest_failure_not_exception(tmp_path, rect):
    root = ET.parse(FIXTURE).getroot()
    item = root.findall('ToolChestItem')[0]
    raw = zlib.decompress(bytes.fromhex(item.findtext('Raw'))).decode('latin-1')
    item.find('Raw').text = zlib.compress(re.sub(r'/Rect\s*\[[^]]+\]', '/Rect [' + rect + ']', raw).encode('latin-1')).hex()
    p = tmp_path / 'bad-rect.btx'
    ET.ElementTree(root).write(p, encoding='utf-8')
    manifest = c.convert_btx(p, tmp_path / 'out')
    assert (manifest['successful_symbol_count'], manifest['failed_symbol_count']) == (4, 1)
    assert manifest['symbols'][0]['status'] == 'failed'
    assert manifest['symbols'][0]['warnings'][0]['code'] in {'invalid_geometry', 'geometry_limit'}


def test_png_allocation_bound_for_large_but_valid_geometry(tmp_path, monkeypatch):
    allocate = c.Image.new
    def bounded_allocate(mode, size, *args, **kwargs):
        assert max(size) <= 520  # Fail the old implementation before allocating.
        return allocate(mode, size, *args, **kwargs)
    monkeypatch.setattr(c.Image, 'new', bounded_allocate)
    p = tmp_path / 'large.png'
    c._emit_png({'width_points': 1_000_000, 'height_points': 500_000, 'paths': []}, p)
    with Image.open(p) as im:
        assert max(im.size) <= 520
        assert im.width * im.height <= c.MAX_PNG_PIXELS


def test_closure_and_moves_are_exact_dxf_edges(tmp_path):
    p = tmp_path / 'geometry.dxf'
    c._emit_dxf(symbol(b'1 1 m 3 1 l 3 3 l h 7 7 m 8 7 l S'), p, 'pt')
    records = p.read_text().split('\n0\nLINE\n')[1:]
    edges = []
    for record in records:
        values = record.splitlines()
        fields = dict(zip(values[::2], values[1::2]))
        edges.append(tuple(float(fields[key]) for key in ['10', '20', '11', '21']))
    assert edges == [(1, 1, 3, 1), (3, 1, 3, 3), (3, 3, 1, 1), (7, 7, 8, 7)]


def test_close_resets_current_point_for_v():
    p = paths(b'2 2 m 8 2 l h 4 4 5 5 v S')[0]
    assert p['segments'][-1] == ('C', (2, 2, 4, 4, 5, 5))


def test_png_close_paint_draws_closing_edge(tmp_path):
    p = tmp_path / 'triangle.png'
    c._emit_png(symbol(b'1 1 m 9 1 l 9 9 l s'), p)
    with Image.open(p) as im:
        assert im.getpixel((24, 24))[:3] == (0, 0, 0)


def test_q_Q_restores_style_not_path():
    p = paths(b'2 w 1 1 m q 3 w 3 1 l Q 3 3 l S')[0]
    assert p['width'] == 2
    assert p['segments'] == [('M', (1, 1)), ('L', (3, 1)), ('L', (3, 3))]


@pytest.mark.parametrize('data', [b'[0 0] 0 d', b'[-1 2] 0 d', b'[1e999 2] 0 d', b'1e999 J', b'1e999 0 0 1 0 0 cm'])
def test_bad_styles_remain_structured(data):
    with pytest.raises(c.BtxConversionError):
        paths(data)


def test_empty_paints_do_not_allocate_path_records():
    assert paths(b'S ' * 1000) == []


def test_state_depth_bounded():
    with pytest.raises(c.BtxConversionError) as exc:
        paths(b'q ' * 65)
    assert exc.value.code == 'graphics_state_limit'


def test_format_loss_is_auditable_in_manifest(tmp_path):
    manifest = c.convert_btx(FIXTURE, tmp_path)
    codes = {w['code'] for w in manifest['symbols'][0]['warnings']}
    assert {'png_preview_approximation', 'dxf_geometry_only'} <= codes


def revision2_input(tmp_path, stream=b'0 0 m 1 1 l S', *, extras=(), length=False, copies=1):
    def hx(value):
        return zlib.compress(value).hex()
    packed = zlib.compress(stream)
    header = b'<</Subtype/Form /BBox [0 0 10 10] /Filter/FlateDecode'
    if length:
        header += b' /Length ' + str(len(packed)).encode()
    form = header + b'>>\nstream\n' + packed + b'\nendstream'
    root = ET.Element('BluebeamRevuToolSet', Version='1')
    ET.SubElement(root, 'Title').text = hx(b'test')
    for _ in range(copies):
        item = ET.SubElement(root, 'ToolChestItem')
        ET.SubElement(item, 'Raw').text = hx(b'<</Subj(test)/Rect [0 0 10 10]/AP <</N /BBObjPtr_A>>>>')
        for key, value in [('A', form), *extras]:
            resource = ET.SubElement(item, 'Resources')
            ET.SubElement(resource, 'ID').text = hx(key.encode())
            ET.SubElement(resource, 'Data').text = hx(value)
    p = tmp_path / 'revision2.btx'
    ET.ElementTree(root).write(p, encoding='utf-8')
    return p


def revision2_failed(tmp_path, p, code):
    out = tmp_path / 'out'
    m = c.convert_btx(p, out, formats=('svg',))
    assert m['failed_symbol_count'] >= 1
    assert any(w['code'] == code for s in m['symbols'] for w in s['warnings'])
    assert not list(out.glob('*.svg'))
    assert m['source_sha256'] == hashlib.sha256(p.read_bytes()).hexdigest()
    return m


def test_revision2_dash_malformed_prompt_rejection():
    import signal
    def expired(*args):
        raise TimeoutError('dash parsing exceeded 1 second')
    old = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 1)
    try:
        with pytest.raises(c.BtxConversionError) as exc:
            paths(b'[' + b'1' * 30 + b'X] 0 d')
        assert exc.value.code == 'invalid_pdf_style'
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def test_revision2_dash_element_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(c, 'MAX_DASH_ELEMENTS', 3, raising=False)
    p = revision2_input(tmp_path, b'[1 1 1 1] 0 d 0 0 m 1 1 l S')
    revision2_failed(tmp_path, p, 'dash_limit')


def test_revision2_dash_cumulative_work(tmp_path, monkeypatch):
    monkeypatch.setattr(c, 'MAX_STYLE_WORK_PER_SYMBOL', 15, raising=False)
    p = revision2_input(tmp_path, b'[1 2] 0 d ' + b'0 0 m 1 1 l S ' * 2)
    revision2_failed(tmp_path, p, 'style_work_limit')


@pytest.mark.parametrize('length', [False, True])
@pytest.mark.parametrize('suffix,tail', [(b'x' * 540, 13), (b',', 10)])
def test_revision2_binary_checksum_preserved(tmp_path, length, suffix, tail):
    stream = b'0 0 m 1 1 l S\n%' + suffix
    assert zlib.compress(stream)[-1] == tail
    p = revision2_input(tmp_path, stream, length=length)
    m = c.convert_btx(p, tmp_path / 'out', formats=('svg',))
    assert m['successful_symbol_count'] == 1


def test_revision2_unused_resource_data_never_inflated(tmp_path, monkeypatch):
    p = revision2_input(tmp_path, extras=[(str(i), b'unused' * 1000) for i in range(24)])
    original = c._inflate_hex
    calls = []
    def checked(value, **kwargs):
        calls.append(kwargs['label'])
        assert not kwargs['label'].startswith('resource ') or kwargs['label'] in {'resource A', 'resource ID'}
        return original(value, **kwargs)
    monkeypatch.setattr(c, '_inflate_hex', checked)
    assert c.convert_btx(p, tmp_path / 'out', formats=('svg',))['successful_symbol_count'] == 1
    assert calls.count('resource A') == 1


def test_revision2_resource_count_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(c, 'MAX_RESOURCES_PER_SYMBOL', 2, raising=False)
    p = revision2_input(tmp_path, extras=[('B', b'unused'), ('C', b'unused')])
    revision2_failed(tmp_path, p, 'resource_count_limit')


def test_revision2_library_resource_count_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(c, 'MAX_RESOURCES_PER_LIBRARY', 1, raising=False)
    p = revision2_input(tmp_path, copies=2)
    with pytest.raises(c.BtxConversionError) as exc:
        c.convert_btx(p, tmp_path / 'out', formats=('svg',))
    assert exc.value.code == 'resource_count_limit'
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('limit,code', [('MAX_DECOMPRESSIONS_PER_SYMBOL', 'decompression_count_limit'), ('MAX_DECOMPRESSIONS_PER_LIBRARY', 'decompression_count_limit'), ('MAX_EXPANDED_BYTES_PER_SYMBOL', 'btx_cumulative_expanded_size_limit'), ('MAX_EXPANDED_BYTES_PER_LIBRARY', 'btx_cumulative_expanded_size_limit')])
def test_revision2_decompression_budgets_cover_prepass(tmp_path, monkeypatch, limit, code):
    p = revision2_input(tmp_path)
    # Title + prepass + main annotation + ID + form + appearance.
    monkeypatch.setattr(c, limit, 2 if 'DECOMPRESSIONS' in limit else 100, raising=False)
    revision2_failed(tmp_path, p, code)


def test_revision2_duplicate_resource_alias_rejected(tmp_path):
    p = revision2_input(tmp_path, extras=[('BBObjPtr_A', b'bad')])
    revision2_failed(tmp_path, p, 'duplicate_resource_id')


def test_revision2_unknown_xml_encoding_structured(tmp_path):
    p = tmp_path / 'bad.btx'
    p.write_bytes(b'<?xml version="1.0" encoding="no-such-encoding"?><BluebeamRevuToolSet/>')
    with pytest.raises(c.BtxConversionError) as exc:
        c.convert_btx(p, tmp_path / 'out')
    assert exc.value.code == 'malformed_xml'


@pytest.mark.parametrize('constant,stream,code', [
    ('MAX_TOKENS_PER_SYMBOL', b'noop ' * 12, 'token_limit'),
    ('MAX_OPERANDS', b'1 ' * 12, 'operand_limit'),
    ('MAX_WARNINGS_PER_SYMBOL', b'aa bb cc dd ee ff gg hh ii jj kk ll', 'warning_limit'),
])
def test_revision2_parser_growth_bounds(monkeypatch, constant, stream, code):
    monkeypatch.setattr(c, constant, 10, raising=False)
    with pytest.raises(c.BtxConversionError) as exc:
        paths(stream)
    assert exc.value.code == code


def test_revision2_repeated_warning_deduplicated():
    _, warnings = c._parse_paths(b'noop ' * 1000, IDENTITY)
    assert len(warnings) == 1


@pytest.mark.parametrize('constant,code', [('MAX_TOKENS_PER_LIBRARY', 'token_limit'), ('MAX_STYLE_WORK_PER_LIBRARY', 'style_work_limit')])
def test_revision2_library_parser_work(tmp_path, monkeypatch, constant, code):
    monkeypatch.setattr(c, constant, 1, raising=False)
    revision2_failed(tmp_path, revision2_input(tmp_path), code)


def test_revision2_unit_scale_reuses_immutable_dash():
    p = paths(b'[1 2] 0 d ' + b'0 0 m 1 1 l S ' * 2)
    assert p[0]['dash'] is p[1]['dash']


@pytest.mark.parametrize('length', [False, True])
def test_revision2_binary_endstream_inside_payload(tmp_path, length):
    p = revision2_input(tmp_path, length=length)
    root = ET.parse(p).getroot()
    data = root.find('ToolChestItem/Resources/Data')
    assert data is not None and data.text is not None
    form = zlib.decompress(bytes.fromhex(data.text))
    header = form.split(b'\nstream\n')[0]
    stream = b'0 0 m 1 1 l S\n%literal endstream within stored deflate block'
    packed = zlib.compress(stream, level=0)
    assert b'endstream' in packed
    if length:
        header = re.sub(rb'/Length \d+', b'/Length ' + str(len(packed)).encode(), header)
    data.text = zlib.compress(header + b'\nstream\n' + packed + b'\nendstream').hex()
    ET.ElementTree(root).write(p, encoding='utf-8')
    assert c.convert_btx(p, tmp_path / 'out', formats=('svg',))['successful_symbol_count'] == 1


@pytest.mark.parametrize('bad_length', ['1', '99999999999999999999', '5 0 R'])
def test_revision2_wrong_stream_length_rejected(tmp_path, bad_length):
    p = revision2_input(tmp_path, length=True)
    root = ET.parse(p).getroot()
    data = root.find('ToolChestItem/Resources/Data')
    assert data is not None and data.text is not None
    form = zlib.decompress(bytes.fromhex(data.text))
    data.text = zlib.compress(re.sub(rb'/Length \d+', b'/Length ' + bad_length.encode(), form)).hex()
    ET.ElementTree(root).write(p, encoding='utf-8')
    revision2_failed(tmp_path, p, 'invalid_form_xobject')


def test_revision2_late_format_failure_removes_partial_assets(tmp_path):
    p = revision2_input(tmp_path, b'0 0 10 10 re 2 2 3 3 re f*')
    out = tmp_path / 'out'
    m = c.convert_btx(p, out)
    assert m['failed_symbol_count'] == 1
    assert m['symbols'][0]['warnings'][0]['code'] == 'unsupported_compound_fill'
    assert {f.name for f in out.iterdir()} == {'manifest.json'}


def test_revision2_expanded_budget_exact_boundary_and_failed_attempt_charge(monkeypatch):
    monkeypatch.setattr(c, 'MAX_EXPANDED_BYTES_PER_LIBRARY', 5)
    budget = c._Budget()
    assert c._inflate(zlib.compress(b'abcde'), label='exact', budget=budget) == b'abcde'
    with pytest.raises(c.BtxConversionError) as exc:
        c._inflate(zlib.compress(b'x'), label='over', budget=budget)
    assert exc.value.code == 'btx_cumulative_expanded_size_limit'
    assert budget.used == {'DECOMPRESSIONS': 2, 'EXPANDED_BYTES': 5}


def test_revision2_library_budget_shared_between_symbols(tmp_path, monkeypatch):
    monkeypatch.setattr(c, 'MAX_DECOMPRESSIONS_PER_LIBRARY', 7)
    p = revision2_input(tmp_path, copies=2)
    m = c.convert_btx(p, tmp_path / 'out', formats=('svg',))
    assert (m['successful_symbol_count'], m['failed_symbol_count']) == (1, 1)
    assert m['symbols'][1]['warnings'][0]['code'] == 'decompression_count_limit'


@pytest.fixture
def dictionary_deadline():
    """Bound hostile scans without leaving a runaway subprocess behind."""
    import signal
    def expired(*args):
        raise TimeoutError('PDF dictionary scan exceeded 1 second')
    old = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 1)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def revision3_dictionary_input(tmp_path, *, raw=None, header=None):
    p = revision2_input(tmp_path)
    root = ET.parse(p).getroot()
    if raw is not None:
        annotation = root.find('ToolChestItem/Raw')
        assert annotation is not None
        annotation.text = zlib.compress(raw).hex()
    if header is not None:
        data = root.find('ToolChestItem/Resources/Data')
        assert data is not None and data.text is not None
        form = zlib.decompress(bytes.fromhex(data.text))
        encoded = form.split(b'\nstream\n', 1)[1]
        data.text = zlib.compress(header + b'\nstream\n' + encoded).hex()
    ET.ElementTree(root).write(p, encoding='utf-8')
    return p


@pytest.mark.parametrize('field', ['Rect', 'BBox', 'Matrix', 'AP'])
def test_revision3_repeated_malformed_dictionary_selectors_bounded(tmp_path, field, dictionary_deadline):
    # Tiny compressed BTX; all work is reached through the public entrypoint,
    # before graphics token limits. Old unanchored searches rescan each suffix.
    repeated = (('/' + field + (' <<' if field == 'AP' else ' [')).encode()) * 32_000
    if field in {'Rect', 'AP'}:
        raw = b'<</Subj(test)' + (b'/Rect [0 0 10 10]' if field == 'AP' else b'') + repeated
        p = revision3_dictionary_input(tmp_path, raw=raw)
    else:
        header = b'<</Subtype/Form /Filter/FlateDecode ' + (b'/BBox [0 0 10 10]' if field == 'Matrix' else b'') + repeated
        p = revision3_dictionary_input(tmp_path, header=header)
    code = 'missing_appearance_pointer' if field == 'AP' else 'invalid_pdf_dictionary'
    revision2_failed(tmp_path, p, code)


@pytest.mark.parametrize('field', ['Rect', 'BBox', 'Matrix'])
@pytest.mark.parametrize('malformed', [b'[', b'[]', b'[0 [1] 0 10 10]', b'not-an-array'])
def test_revision3_first_array_cannot_be_bypassed(tmp_path, field, malformed):
    # A later valid duplicate must not turn malformed input into success.
    numbers = b'1 0 0 1 0 0' if field == 'Matrix' else b'0 0 10 10'
    value = b'/' + field.encode() + b' ' + malformed + b' /' + field.encode() + b' [' + numbers + b']'
    if field == 'Rect':
        p = revision3_dictionary_input(tmp_path, raw=b'<</Subj(test)' + value + b'/AP <</N /BBObjPtr_A>>>>')
    else:
        header = b'<</Subtype/Form /Filter/FlateDecode ' + (b'/BBox [0 0 10 10]' if field == 'Matrix' else b'')
        p = revision3_dictionary_input(tmp_path, header=header + value + b'>>')
    revision2_failed(tmp_path, p, 'invalid_pdf_dictionary')


def test_revision3_pdf_array_whitespace_numbers_and_name_boundaries(tmp_path):
    p = revision3_dictionary_input(
        tmp_path,
        raw=b'<</Subj(test)/Rectangle [bad]/Rect\t\r\n[+0 -.0 1e1 10.]/AP\t<</N\n/BBObjPtr_A>>>>',
        header=b'<</Subtype/Form /Filter/FlateDecode /BBox\f[0 0 10 10]/MatrixOther [bad]/Matrix\r\n[1 0 .0 +1 0e0 -0]>>',
    )
    m = c.convert_btx(p, tmp_path / 'out', formats=('svg',))
    assert m['successful_symbol_count'] == 1
    assert m['symbols'][0]['rect_points'] == [0, 0, 10, 10]
    assert m['symbols'][0]['form_matrix'] == list(IDENTITY)


@pytest.mark.parametrize('appearance', [
    b'/AP <<>> /N /BBObjPtr_A',
    b'/AP <<>> /AP <</N /BBObjPtr_A>>',
    b'/AP << /AP <</N /BBObjPtr_A>>',
    b'/AP not-a-dictionary /AP <</N /BBObjPtr_A>>',
    b'/APOther <</N /BBObjPtr_A>>',
])
def test_revision3_appearance_pointer_stays_in_first_exact_ap(tmp_path, appearance):
    raw = b'<</Subj(test)/Rect [0 0 10 10]' + appearance + b'>>'
    p = revision3_dictionary_input(tmp_path, raw=raw)
    revision2_failed(tmp_path, p, 'missing_appearance_pointer')


@pytest.mark.parametrize('selector', ['stream', 'Length'])
def test_revision3_stream_header_selectors_bounded(tmp_path, selector, dictionary_deadline):
    header = b'<</Subtype/Form /Filter/FlateDecode /BBox [0 0 10 10] '
    if selector == 'Length':
        header += b'/Length ' * 32_000 + b'>>'
        p = revision3_dictionary_input(tmp_path, header=header)
    else:
        # No complete stream delimiter: near matches must scan only once.
        p = revision2_input(tmp_path)
        root = ET.parse(p).getroot()
        data = root.find('ToolChestItem/Resources/Data')
        assert data is not None
        data.text = zlib.compress(header + b'\nstreamX\n' * 32_000).hex()
        ET.ElementTree(root).write(p, encoding='utf-8')
    revision2_failed(tmp_path, p, 'invalid_form_xobject')
