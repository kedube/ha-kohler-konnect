"""Settle the version to release, update the manifest to match, and print it.

Used by .github/workflows/release.yml. ``--bump`` chooses how:

* ``auto`` (the automatic release path): a manifest version with no git tag yet was set by
  hand and is released exactly as written. A version that is already tagged — the manifest
  still says a past release — is bumped to the next minor version after the newest tag.
* ``minor`` / ``major``: always bump, from the newest of the manifest and the tags.
* ``none``: never bump; release the manifest version as it stands.

Versions are plain dotted numbers, like ``0.28`` or ``1.0``. A leading ``v`` in the
manifest is removed — and written back — so the manifest, the git tag and the release name
always agree and never carry one.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

_VERSION = re.compile(r"^\d+(?:\.\d+)+$")


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _tags() -> list[str]:
    """Every release tag in the repository: plain dotted numbers, like ``0.24``."""
    out = subprocess.run(
        ["git", "tag", "--list"], check=True, capture_output=True, text=True
    ).stdout
    return [tag for tag in out.split() if _VERSION.match(tag)]


def _normalize(version: str) -> str:
    """``version`` without a leading ``v``, refused unless it is plain dotted numbers."""
    cleaned = version.strip()
    if cleaned[:1] in ("v", "V"):
        cleaned = cleaned[1:]
    if not _VERSION.match(cleaned):
        raise ValueError(
            f"manifest.json version {version!r} is not a plain dotted version like 1.2"
        )
    return cleaned


def _newest(tags: list[str]) -> str | None:
    return max(tags, key=_key, default=None)


def _unreleased(version: str, tags: list[str]) -> bool:
    """True when ``version`` has no tag yet and is newer than every tag.

    A hand-set version that is not newer than the last release is a mistake — releasing it
    would publish an older number after a newer one — so it stops the release instead.
    """
    if version in tags:
        return False
    newest = _newest(tags)
    if newest is not None and _key(version) <= _key(newest):
        raise ValueError(
            f"manifest.json says {version}, which is not newer than the latest release "
            f"{newest}. Set a higher version, or put back {newest} to have it bumped."
        )
    return True


def _bump_minor(version: str) -> str:
    """Return the next major.minor version, ignoring any patch segment.

    The minor segment supports 00-99 and is zero-padded to two digits (e.g. ``0.89``).
    Bumping ``0.99`` rolls the minor over to ``00`` and increments the major, yielding
    ``1.00``.
    """
    parts = version.split(".")
    if len(parts) < 2:
        raise ValueError(
            f"Expected semantic version with at least major.minor parts, got: {version}"
        )

    major, minor = (int(part) for part in parts[:2])
    if minor >= 99:
        major += 1
        minor = 0
    else:
        minor += 1
    return f"{major}.{minor:02d}"


def _bump_major(version: str) -> str:
    """Return the next major version with the minor reset to ``00``.

    Used for deliberate breaking-change releases via the release workflow's manual
    dispatch (``bump: major``); the automatic release-on-green path always bumps minor.
    """
    return f"{_key(version)[0] + 1}.00"


def settle(current: str, tags: list[str], bump: str) -> str:
    """The version to release, given the manifest's ``current`` and the existing tags."""
    if bump == "none":
        return current
    if bump == "auto":
        if _unreleased(current, tags):
            return current
        bump = "minor"
    # Bump from the newest known version, not the manifest's: a manifest that has fallen
    # behind the tags (a revert, a bad merge) would otherwise bump onto a number that has
    # already been released.
    newest = _newest(tags)
    base = current if newest is None or _key(current) >= _key(newest) else newest
    next_version = (_bump_major if bump == "major" else _bump_minor)(base)
    if next_version in tags:
        raise ValueError(f"{next_version} is already released; refusing to reuse it")
    return next_version


def main() -> int:
    """Update the manifest file in place and print the version to release."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        default=Path("custom_components/kohler_konnect/manifest.json"),
    )
    parser.add_argument(
        "--bump",
        choices=("auto", "minor", "major", "none"),
        default="auto",
        help="auto (default, the automatic release path), minor, major, or none",
    )
    args = parser.parse_args()
    manifest_path = args.manifest
    manifest_text = manifest_path.read_text(encoding="utf-8")
    written = json.loads(manifest_text)["version"]
    version = settle(_normalize(written), _tags(), args.bump)
    if version != written:
        updated_text, replacements = re.subn(
            r'("version"\s*:\s*")([^"]+)(")',
            rf"\g<1>{version}\g<3>",
            manifest_text,
            count=1,
        )
        if replacements != 1:
            raise ValueError("Could not locate the manifest version field")
        manifest_path.write_text(updated_text, encoding="utf-8")
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
