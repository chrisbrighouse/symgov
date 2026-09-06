"""Stage 11 WP11.2 — proves the repository-owned added-line secret-scan
gate actually detects an injected fixture secret, ignores unrelated
diff noise (removed/context lines, placeholders), and passes clean on
a diff with none, plus one true end-to-end CLI invocation against a
disposable git repository."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from secret_scan_added_lines import scan_diff_text  # noqa: E402


def test_detects_an_injected_aws_access_key_on_an_added_line():
    diff_text = (
        "diff --git a/config.py b/config.py\n"
        "--- a/config.py\n"
        "+++ b/config.py\n"
        "@@ -1,0 +2,1 @@\n"
        "+AWS_ACCESS_KEY_ID = \"AKIAABCDEFGHIJKLMNOP\"\n"
    )
    findings = scan_diff_text(diff_text)
    assert len(findings) == 1
    assert findings[0].path == "config.py"
    assert findings[0].line_number == 2
    assert findings[0].pattern_name == "AWS access key ID"


def test_detects_a_private_key_pem_header_and_a_database_url_with_credentials():
    diff_text = (
        "diff --git a/notes.txt b/notes.txt\n"
        "--- a/notes.txt\n"
        "+++ b/notes.txt\n"
        "@@ -0,0 +1,2 @@\n"
        "+-----BEGIN RSA PRIVATE KEY-----\n"
        "+DATABASE_URL=postgresql://admin:hunter2@db.example.com/prod\n"
    )
    findings = scan_diff_text(diff_text)
    names = {finding.pattern_name for finding in findings}
    assert "Private key PEM header" in names
    assert "Database URL with embedded credentials" in names


def test_ignores_removed_and_context_lines():
    diff_text = (
        "diff --git a/config.py b/config.py\n"
        "--- a/config.py\n"
        "+++ b/config.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-AWS_ACCESS_KEY_ID = \"AKIAABCDEFGHIJKLMNOP\"\n"
        " unrelated_context_line = 1\n"
        "+AWS_ACCESS_KEY_ID = None\n"
    )
    assert scan_diff_text(diff_text) == []


def test_ignores_placeholder_values_in_the_generic_pattern():
    diff_text = (
        "diff --git a/settings.example.py b/settings.example.py\n"
        "--- a/settings.example.py\n"
        "+++ b/settings.example.py\n"
        "@@ -0,0 +1,1 @@\n"
        "+api_key = \"changeme\"\n"
    )
    assert scan_diff_text(diff_text) == []


def test_clean_diff_has_no_findings():
    diff_text = (
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1,1 +1,1 @@\n"
        "-# Old title\n"
        "+# New title\n"
    )
    assert scan_diff_text(diff_text) == []


def test_cli_end_to_end_exit_codes_against_a_disposable_git_repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
    subprocess.run(["git", "branch", "base"], cwd=repo, check=True)

    clean_result = subprocess.run(
        [sys.executable, str(SCRIPTS / "secret_scan_added_lines.py"), "--base", "base"],
        cwd=repo, capture_output=True, text=True,
    )
    assert clean_result.returncode == 0
    assert "clean" in clean_result.stdout

    (repo / "app.py").write_text("value = 1\nAWS_ACCESS_KEY_ID = \"AKIAABCDEFGHIJKLMNOP\"\n")
    dirty_result = subprocess.run(
        [sys.executable, str(SCRIPTS / "secret_scan_added_lines.py"), "--base", "base"],
        cwd=repo, capture_output=True, text=True,
    )
    assert dirty_result.returncode == 1
    assert "AWS access key ID" in dirty_result.stderr


def test_the_actual_stage11_wp11_1_diff_against_origin_main_is_clean():
    """The gate's real job: prove it stays clean against the exact release
    candidate diff, not a synthetic fixture. Skips (not fails) when
    `origin/main` isn't reachable in this checkout, e.g. a shallow clone."""
    import pytest

    repo_root = Path(__file__).resolve().parents[1]
    check = subprocess.run(
        ["git", "rev-parse", "--verify", "origin/main"],
        cwd=repo_root, capture_output=True, text=True,
    )
    if check.returncode != 0:
        pytest.skip("origin/main is not reachable in this checkout.")

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "secret_scan_added_lines.py"), "--base", "origin/main"],
        cwd=repo_root, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
