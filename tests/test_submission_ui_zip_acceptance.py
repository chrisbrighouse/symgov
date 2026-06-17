from pathlib import Path


APP_JSX = Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.jsx"


def test_submission_file_picker_accepts_zip_packages():
    source = APP_JSX.read_text(encoding="utf-8")

    assert 'accept=".svg,.png,.jpg,.jpeg,.json,.dxf,.zip"' in source
    assert "Accepted: SVG, PNG, JPG, JPEG, JSON, DXF, ZIP" in source
