"""Rotate the CHANGELOG.md "Unreleased" section into a released version section.

Used by .github/workflows/release.yml. If CHANGELOG.md has a non-empty "## Unreleased"
section, its entries move under a new "## <version> — <date>" heading, and an empty
"## Unreleased" heading is left above it for the next change. If the version already has a
section — a release that was committed but never published, being retried — the entries
are added to the top of that section instead of creating a second one.

With no Unreleased content the changelog is left untouched, and the release notes fall back
to the commit list. Prints "rotated", "merged" or "skipped".
"""

from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

_UNRELEASED = re.compile(
    r"^## Unreleased[ \t]*\n(?P<body>.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL
)


def _version_heading(version: str) -> re.Pattern[str]:
    """The heading of ``version``'s own section: ``1.0`` must not match ``1.0.1``."""
    return re.compile(rf"^## {re.escape(version)}(?=[ \t]|$)[^\n]*\n", re.MULTILINE)


def rotate(text: str, version: str, today: str) -> tuple[str, str]:
    """``text`` with the Unreleased entries released as ``version``, and what was done."""
    match = _UNRELEASED.search(text)
    if match is None or not match.group("body").strip():
        return text, "skipped"
    entries = match.group("body").strip()
    emptied = text[: match.start()] + "## Unreleased\n\n" + text[match.end() :]

    existing = _version_heading(version).search(emptied)
    if existing is not None:
        insert_at = existing.end()
        updated = f"{emptied[:insert_at]}\n{entries}\n{emptied[insert_at:]}"
        return updated, "merged"

    section = f"## {version} — {today}\n\n{entries}\n\n"
    at = match.start() + len("## Unreleased\n\n")
    return emptied[:at] + section + emptied[at:], "rotated"


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: update_changelog.py <version> [changelog_path]")
    version = sys.argv[1]
    changelog_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("CHANGELOG.md")

    text = changelog_path.read_text(encoding="utf-8")
    updated, outcome = rotate(text, version, datetime.date.today().isoformat())
    if updated != text:
        changelog_path.write_text(updated, encoding="utf-8")
    print(outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
