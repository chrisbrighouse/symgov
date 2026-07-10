from pathlib import Path


APP_JSX = Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.jsx"
API_JS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "api.js"


def test_submission_file_picker_accepts_zip_packages():
    source = APP_JSX.read_text(encoding="utf-8")

    assert 'accept=".svg,.png,.jpg,.jpeg,.json,.dxf,.btx,.zip"' in source
    assert "Accepted: SVG, PNG, JPG, JPEG, JSON, DXF, BTX, ZIP" in source
    assert "Bluebeam BTX tool sets are safely unpacked" in source


def test_submission_page_has_multiline_source_field():
    source = APP_JSX.read_text(encoding="utf-8")

    assert '<span>Source</span>' in source
    assert "placeholder=\"Optional: website URL, datasheet, drawing pack, contractor note, or other source context\"" in source
    assert "value={formState.source}" in source
    assert "updateField('source', event.target.value)" in source


def test_submission_api_sends_source_notes_payload_property():
    source = API_JS.read_text(encoding="utf-8")

    assert "source_notes: (formState.source || '').trim()" in source
