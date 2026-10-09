"""Generate GitHub release notes markdown for a version.

Used by .github/workflows/release.yml. Builds the release body from:
  1. The CHANGELOG.md section for the version (rotated there from
     "Unreleased" by update_changelog.py), shown as Highlights.
  2. The commit subjects since the previous release tag (release
     bookkeeping commits are excluded).
  3. A GitHub compare link between the previous tag and the new one.

A release with no CHANGELOG.md section still publishes, with the commit list only, but
the workflow log carries a warning saying so. Relative links in the changelog are made
absolute, pointing at the released tag: on a release page they would otherwise resolve
under /releases/ and 404.

Usage: generate_release_notes.py <version> [output_path]
The repository slug is taken from $GITHUB_REPOSITORY when set.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

RELEASE_TAG_RE = re.compile(r"^\d+(?:\.\d+)+$")
# A Markdown link or image target that is a path in the repository: not a URL, not an
# in-page anchor, not an email address.
_RELATIVE_LINK = re.compile(
    r"(\]\()(?![a-z][a-z0-9+.-]*:|#|/)([^)\s]+)(\))", re.IGNORECASE
)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _previous_tag(version: str) -> str | None:
    """The newest release tag older than the version being published."""
    older = [
        tag
        for tag in _git("tag", "--list").splitlines()
        if RELEASE_TAG_RE.match(tag) and _key(tag) < _key(version)
    ]
    return max(older, key=_key, default=None)


def changelog_section(text: str, version: str) -> str:
    """``version``'s section of the changelog, without its heading. ``1.0`` is not ``1.0.1``."""
    match = re.search(
        rf"^## {re.escape(version)}(?=[ \t]|$)[^\n]*\n(.*?)(?=^## |\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def absolute_links(markdown: str, repo: str, ref: str) -> str:
    """``markdown`` with repository-relative link targets pointing at ``ref`` on GitHub."""
    return _RELATIVE_LINK.sub(
        lambda m: (
            f"{m[1]}https://github.com/{repo}/blob/{ref}/{m[2].removeprefix('./')}{m[3]}"
        ),
        markdown,
    )


def _commit_lines(previous_tag: str | None) -> list[str]:
    log_range = f"{previous_tag}..HEAD" if previous_tag else "HEAD"
    subjects = _git("log", "--no-merges", "--format=%s (%h)", log_range).splitlines()
    return [
        f"- {subject}"
        for subject in subjects
        if subject and not subject.startswith("chore(release):")
    ]


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: generate_release_notes.py <version> [output_path]")
    version = sys.argv[1]
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("release_notes.md")
    repo = os.environ.get("GITHUB_REPOSITORY", "kedube/ha-kohler-konnect")

    previous_tag = _previous_tag(version)
    sections: list[str] = []

    changelog = Path("CHANGELOG.md")
    text = changelog.read_text(encoding="utf-8") if changelog.exists() else ""
    highlights = absolute_links(changelog_section(text, version), repo, version)
    if highlights:
        sections.append(f"## Highlights\n\n{highlights}")
    else:
        # A workflow command: GitHub shows it as an annotation on the run.
        print(
            f"::warning title=No changelog entry::CHANGELOG.md has no section for "
            f"{version}, so the release notes list commits only. Add entries under "
            f"'## Unreleased' with each change."
        )

    commits = _commit_lines(previous_tag)
    if commits:
        sections.append("## Commits\n\n" + "\n".join(commits))

    if previous_tag:
        sections.append(
            f"**Full Changelog**: https://github.com/{repo}/compare/{previous_tag}...{version}"
        )

    body = "\n\n".join(sections) if sections else f"Release {version}."
    output_path.write_text(body + "\n", encoding="utf-8")
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
