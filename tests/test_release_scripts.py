"""The release scripts in .github/scripts, which decide what every release is called and says.

A mistake here is published: a tag and a GitHub release are effectively permanent, and HACS
offers them to everyone. So the rules are pinned down here rather than discovered on main —
a hand-set manifest version is released as written, an unchanged one is bumped, no tag ever
carries a `v`, and the changelog's Unreleased entries become the release notes.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / ".github" / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bump = _load("bump_manifest_version")
changelog = _load("update_changelog")
notes = _load("generate_release_notes")

TAGS = ["0.26", "0.27", "0.28"]


# --------------------------------------------------------------------------- #
# Which version
# --------------------------------------------------------------------------- #


def test_a_hand_set_version_is_released_exactly_as_written():
    """Setting 1.0 by hand publishes 1.0 — until 2026-10-08 it published the next one."""
    assert bump.settle("1.0", TAGS, "auto") == "1.0"


def test_an_unchanged_version_is_bumped_to_the_next_minor():
    """The manifest still says the last release, so nobody chose a number: bump it."""
    assert bump.settle("0.28", TAGS, "auto") == "0.29"


def test_a_manifest_behind_the_tags_bumps_past_the_newest_release():
    """Bumping 0.26 would give 0.27, which is already out — a revert must not reuse it."""
    assert bump.settle("0.26", TAGS, "auto") == "0.29"
    assert bump.settle("0.26", TAGS, "minor") == "0.29"


def test_a_hand_set_version_older_than_the_last_release_stops_the_release():
    """Publishing 0.25 after 0.28 would look like a downgrade to everyone who updates."""
    with pytest.raises(ValueError, match=r"not newer than the latest release 0\.28"):
        bump.settle("0.25.1", TAGS, "auto")


@pytest.mark.parametrize(
    ("current", "kind", "expected"),
    [
        ("0.28", "minor", "0.29"),
        ("0.99", "minor", "1.00"),
        ("1.0", "minor", "1.01"),
        ("0.28", "major", "1.00"),
        ("0.28", "none", "0.28"),
    ],
)
def test_explicit_bumps(current, kind, expected):
    """The manual dispatch's choices. The minor is two digits, as every tag so far."""
    assert bump.settle(current, TAGS, kind) == expected


def test_the_first_release_of_a_repository_keeps_its_version():
    assert bump.settle("0.1", [], "auto") == "0.1"


@pytest.mark.parametrize("written", ["v1.2", "V1.2", " 1.2 "])
def test_a_leading_v_is_dropped(written):
    """Tags and release names are bare versions; a `v` typed into the manifest is removed."""
    assert bump._normalize(written) == "1.2"


@pytest.mark.parametrize("written", ["1", "1.2-beta", "vv1.2", "latest", ""])
def test_a_version_that_is_not_plain_dotted_numbers_is_refused(written):
    with pytest.raises(ValueError, match="not a plain dotted version"):
        bump._normalize(written)


def test_the_shipped_manifest_holds_a_bare_version():
    """What the next release starts from must already satisfy the rule."""
    manifest = json.loads(
        (ROOT / "custom_components" / "kohler_konnect" / "manifest.json").read_text()
    )
    assert bump._normalize(manifest["version"]) == manifest["version"]


# --------------------------------------------------------------------------- #
# The script end to end, against a real git repository
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with releases 0.27 and 0.28 tagged, and a manifest saying 0.28."""
    _git(tmp_path, "init", "-q")
    manifest = tmp_path / "manifest.json"
    for version in ("0.27", "0.28"):
        manifest.write_text(json.dumps({"domain": "x", "version": version}, indent=2))
        _git(tmp_path, "add", "manifest.json")
        _git(tmp_path, "commit", "-q", "-m", f"chore(release): {version} [skip ci]")
        _git(tmp_path, "tag", version)
    return tmp_path


def _run_bump(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "bump_manifest_version.py"),
            "manifest.json",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _manifest_version(repo: Path) -> str:
    return json.loads((repo / "manifest.json").read_text())["version"]


def test_the_script_bumps_an_already_tagged_manifest_and_writes_it_back(repo):
    assert _run_bump(repo, "--bump", "auto") == "0.29"
    assert _manifest_version(repo) == "0.29"


def test_the_script_releases_a_pushed_version_change_untouched(repo):
    (repo / "manifest.json").write_text('{\n  "domain": "x",\n  "version": "1.0"\n}\n')
    before = (repo / "manifest.json").read_text()
    assert _run_bump(repo, "--bump", "auto") == "1.0"
    assert (repo / "manifest.json").read_text() == before


def test_the_script_writes_a_v_prefixed_version_back_without_it(repo):
    (repo / "manifest.json").write_text('{"domain": "x", "version": "v1.1"}')
    assert _run_bump(repo, "--bump", "auto") == "1.1"
    assert _manifest_version(repo) == "1.1"


def test_the_script_fails_loudly_rather_than_publishing_an_older_version(repo):
    (repo / "manifest.json").write_text('{"domain": "x", "version": "0.20.5"}')
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "bump_manifest_version.py"), "manifest.json"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "not newer than the latest release 0.28" in result.stderr


# --------------------------------------------------------------------------- #
# The changelog becomes the release notes
# --------------------------------------------------------------------------- #

CHANGELOG = """# Changelog

Add entries under **Unreleased** with each change.

## Unreleased

**Fixed**

- **Something** works now.

## 0.28 — 2026-10-08

- Older entry.
"""


def test_unreleased_entries_move_under_the_new_version():
    updated, outcome = changelog.rotate(CHANGELOG, "1.0", "2026-10-09")
    assert outcome == "rotated"
    assert notes.changelog_section(updated, "1.0") == (
        "**Fixed**\n\n- **Something** works now."
    )
    assert notes.changelog_section(updated, "0.28") == "- Older entry."


def test_an_empty_unreleased_heading_is_left_for_the_next_change():
    """So the next change has somewhere to go, and the release after it has notes."""
    updated, _ = changelog.rotate(CHANGELOG, "1.0", "2026-10-09")
    assert "## Unreleased\n\n## 1.0 — 2026-10-09\n" in updated
    again, outcome = changelog.rotate(updated, "1.01", "2026-10-10")
    assert outcome == "skipped"
    assert again == updated


def test_nothing_unreleased_leaves_the_changelog_alone():
    text = CHANGELOG.replace("**Fixed**\n\n- **Something** works now.\n", "")
    assert changelog.rotate(text, "1.0", "2026-10-09") == (text, "skipped")


def test_a_retried_release_adds_to_its_section_rather_than_making_a_second():
    """A release committed but never published is retried with the same version."""
    first, _ = changelog.rotate(CHANGELOG, "1.0", "2026-10-09")
    later = first.replace("## Unreleased\n\n", "## Unreleased\n\n- Late fix.\n\n", 1)
    updated, outcome = changelog.rotate(later, "1.0", "2026-10-09")
    assert outcome == "merged"
    assert updated.count("## 1.0 ") == 1
    section = notes.changelog_section(updated, "1.0")
    assert section.startswith("- Late fix.")
    assert "- **Something** works now." in section


def test_a_heading_mentioning_unreleased_is_not_mistaken_for_the_section():
    text = CHANGELOG.replace("# Changelog", "# Changelog\n\n### Unreleased features")
    updated, _ = changelog.rotate(text, "1.0", "2026-10-09")
    assert "### Unreleased features" in updated


def test_version_sections_are_matched_exactly():
    """1.0's notes must not be 1.0.1's, nor 1.01's."""
    text = "## 1.0.1 — x\n\n- patch\n\n## 1.01 — y\n\n- next\n\n## 1.0 — z\n\n- one\n"
    assert notes.changelog_section(text, "1.0") == "- one"
    assert notes.changelog_section(text, "1.01") == "- next"
    assert notes.changelog_section(text, "2.0") == ""


def test_the_real_changelog_rotates_into_notes():
    """The project's own CHANGELOG.md has the shape the scripts expect."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    pending = changelog._UNRELEASED.search(text)
    assert pending is not None, "CHANGELOG.md needs a '## Unreleased' heading"
    updated, outcome = changelog.rotate(text, "99.0", "2099-01-01")
    if outcome == "skipped":
        assert not pending.group("body").strip()
    else:
        assert notes.changelog_section(updated, "99.0") == pending.group("body").strip()


def test_release_notes_carry_the_changelog_commits_and_compare_link(repo, monkeypatch):
    (repo / "CHANGELOG.md").write_text(changelog.rotate(CHANGELOG, "0.29", "d")[0])
    (repo / "feature.txt").write_text("x")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "Add the feature")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    out = repo / "notes.md"
    subprocess.run(
        [sys.executable, str(SCRIPTS / "generate_release_notes.py"), "0.29", str(out)],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    body = out.read_text()
    assert body.startswith("## Highlights\n\n**Fixed**\n\n- **Something** works now.")
    assert "## Commits\n\n- Add the feature (" in body
    # Release bookkeeping is not a change anyone needs to read about.
    assert "chore(release)" not in body
    assert body.rstrip().endswith("compare/0.28...0.29")
    assert "/v0." not in body and "...v" not in body


def test_release_notes_without_a_changelog_entry_warn_in_the_workflow_log(repo):
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "generate_release_notes.py"), "0.29", "n.md"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.startswith("::warning title=No changelog entry::")


def test_the_previous_release_is_the_newest_older_tag(repo, monkeypatch):
    """Re-publishing an old version compares against the one before it, not the newest.

    Compared as numbers, not text: 0.9 is older than 0.27.
    """
    _git(repo, "tag", "0.9")
    monkeypatch.setattr(notes, "_git", lambda *args: _git(repo, *args))
    assert notes._previous_tag("0.29") == "0.28"
    assert notes._previous_tag("0.28") == "0.27"
    assert notes._previous_tag("0.27") == "0.9"


def test_changelog_links_into_the_repository_survive_on_the_release_page():
    """A release body resolves relative links under /releases/, where they 404.

    They point at the released tag, so they show the docs as they were at that release.
    """
    text = (
        "See [Upgrading](docs/user_guide.md#upgrading), ![logo](./docs/x.png), "
        "[#4](https://github.com/o/r/pull/4), [above](#fixed) and "
        "[mail](mailto:a@b.c)."
    )
    assert notes.absolute_links(text, "o/r", "1.0") == (
        "See [Upgrading](https://github.com/o/r/blob/1.0/docs/user_guide.md#upgrading), "
        "![logo](https://github.com/o/r/blob/1.0/docs/x.png), "
        "[#4](https://github.com/o/r/pull/4), [above](#fixed) and "
        "[mail](mailto:a@b.c)."
    )
