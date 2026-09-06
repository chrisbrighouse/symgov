#!/usr/bin/env python3
"""Stage 11 WP11.2 — repository-owned added-line secret-scan gate.

Per programme plan line 328: "Create and review a repository-owned
added-line secret-scan script or pin an installed scanner and exact
invocation before Stage 11. Do not call an unspecified 'static check' or
'secret scan' a passing gate." No scanner (gitleaks/detect-secrets/
trufflehog) is installed anywhere in this environment, so this is a small,
dependency-free, fully auditable script rather than a pinned external tool
(Stage 11 plan §2 WP11.2, decided 2026-09-06).

Scope: only lines *added* by a diff are checked, never the whole tree —
this is a pre-merge/pre-release gate against the change being introduced,
not a full-repository audit. Pattern list is kept in this file so it can
be extended without redesigning the mechanism.

Invocation (from the repository root):

    python3 scripts/secret_scan_added_lines.py [--base <ref>] [--candidate <ref>]

`--base` defaults to `origin/main`; `--candidate` defaults to the current
working tree (staged + unstaged changes against `--base`). Pass an actual
ref (branch/tag/sha) for `--candidate` to check a specific commit instead.
Exit code 0 means no finding; exit code 1 means at least one finding, with
each finding printed as `<path>:<line>: <pattern name>` plus a redacted
excerpt (never the full matched secret).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    path: str
    line_number: int
    pattern_name: str
    excerpt: str


# Each pattern is checked independently against every added line. Keep this
# list reviewable and extend it here rather than building a second mechanism.
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("AWS access key ID", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("AWS secret access key (heuristic)", re.compile(
        r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}['\"]?"
    )),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("Private key PEM header", re.compile(
        r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"
    )),
    ("JSON Web Token", re.compile(
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    )),
    ("Database URL with embedded credentials", re.compile(
        r"(?i)\b(postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^\s'\"@/]+:[^\s'\"@/]+@"
    )),
    ("Generic API key/token/secret assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*"
        r"['\"](?P<value>[A-Za-z0-9+/_\-\.]{16,})['\"]"
    )),
]

# Placeholder-looking values that would otherwise match the generic
# assignment pattern but are not real secrets.
_PLACEHOLDER_VALUES = re.compile(
    r"(?i)^(changeme|placeholder|example|xxx+|redacted|\.\.\.|"
    r"your[_-]?(api[_-]?key|secret|token|password)|secret[_-]?value)$"
)


def _is_added_line(diff_line: str) -> bool:
    return diff_line.startswith("+") and not diff_line.startswith("+++")


_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_FILE_HEADER = re.compile(r"^\+\+\+ b/(.+)$")


def scan_diff_text(diff_text: str) -> list[Finding]:
    """Scan unified diff text for secrets on added lines only.

    Tracks the current file path (`+++ b/<path>`) and the current line
    number in the new file (from `@@ -a,b +c,d @@` hunk headers, advanced
    per added/context line) so findings can be reported as `path:line`.
    """
    findings: list[Finding] = []
    current_path = "<unknown>"
    current_line = 0
    for raw_line in diff_text.splitlines():
        file_match = _FILE_HEADER.match(raw_line)
        if file_match:
            current_path = file_match.group(1)
            continue
        hunk_match = _HUNK_HEADER.match(raw_line)
        if hunk_match:
            current_line = int(hunk_match.group(1))
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            content = raw_line[1:]
            for pattern_name, pattern in PATTERNS:
                match = pattern.search(content)
                if not match:
                    continue
                value = match.groupdict().get("value")
                if value is not None and _PLACEHOLDER_VALUES.match(value):
                    continue
                redacted = content.strip()
                if len(redacted) > 80:
                    redacted = redacted[:77] + "..."
                findings.append(Finding(current_path, current_line, pattern_name, redacted))
            current_line += 1
        elif not raw_line.startswith("-"):
            current_line += 1
    return findings


def _git_diff(base: str, candidate: str | None) -> str:
    args = ["git", "diff", "--unified=0", base]
    if candidate:
        args.append(candidate)
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    return result.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="Base ref to diff against (default: origin/main).")
    parser.add_argument("--candidate", default=None, help="Candidate ref (default: current working tree).")
    args = parser.parse_args(argv)

    diff_text = _git_diff(args.base, args.candidate)
    findings = scan_diff_text(diff_text)

    if not findings:
        print("secret-scan-added-lines: clean (no findings).")
        return 0

    print(f"secret-scan-added-lines: {len(findings)} finding(s):", file=sys.stderr)
    for finding in findings:
        print(f"  {finding.path}:{finding.line_number}: {finding.pattern_name}: {finding.excerpt}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
