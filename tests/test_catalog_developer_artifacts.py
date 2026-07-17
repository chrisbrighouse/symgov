import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "catalog-api"


def test_catalog_api_documentation_artifacts_cover_the_first_milestone():
    expected = {
        "README.md",
        "quickstart.md",
        "integration-recipes.md",
        "errors-and-security.md",
        "CHANGELOG.md",
        "symgov-catalog-api.postman_collection.json",
    }
    assert expected.issubset({path.name for path in DOCS.iterdir()})

    combined = "\n".join((DOCS / name).read_text(encoding="utf-8") for name in expected if name.endswith(".md"))
    for marker in (
        "Authorization: Bearer",
        "catalog.read",
        "catalog.ed.query",
        "catalog.feedback.write",
        "/api/v1/catalog/symbols",
        "/api/v1/catalog/search",
        "/api/v1/catalog/ed/query",
        "0003-12",
        "JavaScript",
        "TypeScript",
        "Python",
        "Apryse",
        "Downloads are not available",
        "sandbox",
        "/support",
    ):
        assert marker.lower() in combined.lower()


def test_catalog_api_docs_do_not_present_future_features_as_current():
    combined = "\n".join(path.read_text(encoding="utf-8") for path in DOCS.glob("*.md"))
    lowered = combined.lower()

    assert "conversation history is not persisted" in lowered
    assert "self-service registration is planned" in lowered
    assert "cors" in lowered and "deployment-dependent" in lowered
    assert "rate limits are not currently published" in lowered


def test_postman_collection_is_valid_and_contains_only_catalog_integration_paths():
    collection = json.loads((DOCS / "symgov-catalog-api.postman_collection.json").read_text(encoding="utf-8"))

    assert collection["info"]["schema"].endswith("collection.json")
    assert collection["variable"]
    requests = [item["request"] for item in collection["item"]]
    urls = [request["url"]["raw"] for request in requests]
    assert any("/catalog/capabilities" in url for url in urls)
    assert any("/catalog/search" in url for url in urls)
    assert all("/api/v1/catalog/" in url for url in urls)
    assert all("/admin/" not in url and "/workspace/" not in url and "/published/" not in url for url in urls)
