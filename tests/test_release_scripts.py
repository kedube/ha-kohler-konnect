"""The release scripts in .github/scripts, which decide what every release is called and says.

A mistake here is published: a tag and a GitHub release are effectively permanent, and HACS
offers them to everyone. So the rules are pinned down here rather than discovered on main —
a push is released only if it set a new version, which is released as written; a manual run
bumps a version nobody set; no tag ever carries a `v`; and the changelog's Unreleased entries
become the release notes.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

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


@pytest.mark.parametrize(
    ("version", "answer"),
    [("0.28", "false"), ("0.27", "false"), ("0.29", "true"), ("v1.0", "true")],
)
def test_the_script_says_whether_a_version_was_set_by_hand(repo, version, answer):
    (repo / "manifest.json").write_text(json.dumps({"version": version}))
    before = (repo / "manifest.json").read_text()
    assert _run_bump(repo, "--hand-set") == answer
    assert (repo / "manifest.json").read_text() == before


# --------------------------------------------------------------------------- #
# The workflow's steps: what a push releases, and what a manual run does
# --------------------------------------------------------------------------- #

WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text())
STEPS = {
    step["id"]: step["run"]
    for step in WORKFLOW["jobs"]["release"]["steps"]
    if "id" in step and "run" in step
}
MANIFEST = "custom_components/kohler_konnect/manifest.json"
GREEN = "completed success"
RED = "completed failure"
# A stand-in for `gh api`: logs which runs it was asked about, and answers for them.
FAKE_GH = """#!/bin/sh
echo "$2" >> "$GH_LOG"
case "$2" in
  *validate.yml*) echo "$VALIDATE_RUN" ;;
  *) echo "$TEST_RUN" ;;
esac
"""


def _set_version(repo: Path, version: str) -> None:
    (repo / MANIFEST).write_text(json.dumps({"domain": "x", "version": version}))


def _commit(repo: Path, subject: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", subject)
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def main(tmp_path: Path) -> Path:
    """Main as a run checks it out: laid out like this repository, its tip released as 2.04."""
    repo = tmp_path / "repo"
    (repo / MANIFEST).parent.mkdir(parents=True)
    shutil.copytree(SCRIPTS, repo / ".github" / "scripts")
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n- A change.\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    (bin_dir / "python").symlink_to(sys.executable)
    (bin_dir / "python3").symlink_to(sys.executable)
    _git(repo.parent, "init", "-q", str(repo))
    _set_version(repo, "2.03")
    _commit(repo, "Earlier work")
    _set_version(repo, "2.04")
    _commit(repo, "chore(release): 2.04 [skip ci]")
    _git(repo, "tag", "2.04")
    return repo


def _step(
    repo: Path, step: str, env: dict[str, str] | None = None, **run
) -> dict[str, str]:
    """Run one of the workflow's steps as the runner would; returns its outputs.

    Steps are run through `bash -e`, GitHub's default, so one that fails raises here.
    """
    output = repo.parent / "github_output"
    output.write_text("")
    subprocess.run(
        ["bash", "-e", "-c", STEPS[step]],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{repo.parent / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_OUTPUT": str(output),
            "GITHUB_REPOSITORY": "o/r",
            "GH_LOG": str(repo.parent / "gh.log"),
            "TEST_RUN": run.get("test", GREEN),
            "VALIDATE_RUN": run.get("validate", GREEN),
            **(env or {}),
        },
    )
    lines = output.read_text().splitlines()
    return dict(line.split("=", 1) for line in lines)


def _asked_about(repo: Path) -> str:
    """Every run the workflow looked up, or nothing if it never looked."""
    log = repo.parent / "gh.log"
    return log.read_text() if log.exists() else ""


def _on_push(repo: Path, sha: str | None = None, **run) -> dict[str, str]:
    """The steps a green Test on main starts. ``sha`` is the commit Test ran on."""
    sha = sha or _git(repo, "rev-parse", "HEAD")
    hand_set = _step(repo, "hand_set")["hand_set"]
    # The checks' `if:`: a push that set no version doesn't wait for them.
    checks = (
        _step(repo, "checks", {"HEAD_SHA": sha}, **run) if hand_set == "true" else {}
    )
    return _step(
        repo,
        "head_check",
        {
            "EVENT_NAME": "workflow_run",
            "EXPECTED_SHA": sha,
            "HAND_SET": hand_set,
            "CHECKS_GREEN": checks.get("green", ""),
        },
    )


def _by_hand(
    repo: Path, bump_input: str = "auto", ignore_checks: bool = False, **run
) -> dict[str, str]:
    """The steps a manual run goes through, up to deciding what to release."""
    # The checks' `if:`: ticking ignore_checks skips them.
    checks = _step(repo, "checks", {"HEAD_SHA": ""}, **run) if not ignore_checks else {}
    return _step(
        repo,
        "head_check",
        {
            "EVENT_NAME": "workflow_dispatch",
            "EXPECTED_SHA": "",
            "BUMP_INPUT": bump_input,
            "IGNORE_CHECKS": str(ignore_checks).lower(),
            "HAND_SET": "",
            "CHECKS_GREEN": checks.get("green", ""),
        },
    )


def _settled_version(repo: Path, decision: dict[str, str], bump_input: str = "") -> str:
    return _step(
        repo,
        "bump",
        {"RELEASE_ACTION": decision["release_action"], "BUMP_INPUT": bump_input},
    )["new_version"]


def test_a_push_that_leaves_the_version_alone_is_not_released(main):
    _commit(main, "Fix the thing")
    assert _on_push(main)["release_action"] == "skip"
    assert _asked_about(main) == ""  # nor does it wait on the other checks


def test_a_push_that_sets_a_version_releases_exactly_that_version(main):
    _set_version(main, "3.0")
    sha = _commit(main, "Big change; version 3.0")
    decision = _on_push(main)
    assert decision == {"release_action": "existing", "release_target": sha}
    assert _settled_version(main, decision) == "3.0"
    assert json.loads((main / MANIFEST).read_text())["version"] == "3.0"


def test_a_version_set_earlier_in_the_push_still_counts(main):
    _set_version(main, "3.0")
    _commit(main, "Version 3.0")
    _commit(main, "One more fix")
    assert _on_push(main)["release_action"] == "existing"


@pytest.mark.parametrize(
    "run", [{"validate": RED}, {"test": RED}, {"validate": "completed cancelled"}]
)
def test_a_set_version_waits_for_a_commit_that_passes(main, run):
    _set_version(main, "3.0")
    _commit(main, "Version 3.0")
    assert _on_push(main, **run)["release_action"] == "skip"


def test_a_push_setting_a_version_older_than_the_last_release_fails(main):
    """A red run, rather than quietly ignoring the version somebody typed."""
    _set_version(main, "2.03.5")
    _commit(main, "Oops")
    with pytest.raises(subprocess.CalledProcessError) as failure:
        _on_push(main)
    assert "not newer than the latest release 2.04" in failure.value.stderr


def test_a_run_for_a_commit_main_has_moved_past_leaves_it_to_the_newer_one(main):
    _set_version(main, "3.0")
    old = _commit(main, "Version 3.0")
    _commit(main, "One more fix")
    assert _on_push(main, sha=old)["release_action"] == "skip"


def test_a_release_committed_but_never_published_is_retried(main):
    _set_version(main, "3.0")
    pushed = _commit(main, "Version 3.0")
    release = _commit(main, "chore(release): 3.0 [skip ci]")
    assert _on_push(main, sha=pushed) == {
        "release_action": "existing",
        "release_target": release,
    }


def test_test_re_run_after_the_release_does_not_release_again(main):
    _set_version(main, "3.0")
    sha = _commit(main, "Version 3.0")
    _git(main, "tag", "3.0")
    assert _on_push(main, sha=sha)["release_action"] == "skip"


def test_a_manual_run_releases_the_next_minor_version_by_default(main):
    assert WORKFLOW[True]["workflow_dispatch"]["inputs"]["bump"]["default"] == "auto"
    sha = _commit(main, "Fix the thing")
    decision = _by_hand(main)
    assert decision == {"release_action": "bump", "release_target": sha}
    assert _settled_version(main, decision, "auto") == "2.05"


def test_a_manual_run_keeps_a_version_set_by_hand(main):
    _set_version(main, "3.0")
    _commit(main, "Version 3.0")
    assert _settled_version(main, _by_hand(main), "auto") == "3.0"


def test_a_manual_run_with_nothing_new_makes_no_empty_release(main):
    assert _by_hand(main)["release_action"] == "skip"


@pytest.mark.parametrize(("choice", "version"), [("minor", "2.05"), ("major", "3.00")])
def test_a_manual_bump_can_be_asked_for_on_a_released_commit(main, choice, version):
    decision = _by_hand(main, choice)
    assert decision["release_action"] == "bump"
    assert _settled_version(main, decision, choice) == version


def test_a_manual_none_publishes_the_manifest_version_as_it_stands(main):
    decision = _by_hand(main, "none")
    assert decision["release_action"] == "existing"
    assert _settled_version(main, decision, "none") == "2.04"


@pytest.mark.parametrize("run", [{"test": RED}, {"validate": RED}])
def test_a_manual_run_refuses_a_commit_that_failed_its_checks(main, run):
    _commit(main, "Fix the thing")
    with pytest.raises(subprocess.CalledProcessError) as failure:
        _by_hand(main, **run)
    assert "ignore_checks" in failure.value.stdout


def test_a_manual_run_can_be_told_to_ignore_the_checks(main):
    _commit(main, "Fix the thing")
    assert _by_hand(main, ignore_checks=True, test=RED)["release_action"] == "bump"
    assert _asked_about(main) == ""


def test_a_manual_run_on_a_release_commit_checks_the_commit_before_it(main):
    """A release commit carries `[skip ci]`, so it has no runs of its own to look up."""
    code = _commit(main, "Fix the thing")
    release = _commit(main, "chore(release): 2.05 [skip ci]")
    _by_hand(main, "minor")
    asked = _asked_about(main)
    assert f"head_sha={code}" in asked
    assert release not in asked


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
