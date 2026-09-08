#!/usr/bin/env python3
"""Fail safely when meeting artifacts are tracked by Git."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REQUIRED_IGNORE_PATTERNS = {
    ".hermes-qa-*",
    "meeting-artifacts/",
    "recordings/",
    "*.m4a",
    "*.mp4",
    "*.mp3",
    "*.ogg",
    "*.wav",
    "*.webm",
}
ARTIFACT_DIRECTORIES = ("meeting-artifacts/", "recordings/")
RECORDING_SUFFIXES = {".m4a", ".mp3", ".mp4", ".ogg", ".wav", ".webm"}
GENERATED_ARTIFACT_FILENAMES = {
    "acta-approved.json",
    "acta-draft.json",
    "digest.md",
    "extractions.json",
    "manifest.json",
    "review.yaml",
    "transcript-chunks.json",
    "transcript.json",
    "transcript.md",
    "validation-report.json",
}
MEETING_DIRECTORY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _git(root: Path, *args: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        message = result.stderr.decode().strip() or "git command failed"
        raise RuntimeError(message)
    return [item for item in result.stdout.decode().split("\0") if item]


def _is_meeting_artifact(path: str) -> bool:
    artifact_path = Path(path)
    return (
        path.startswith(ARTIFACT_DIRECTORIES)
        or artifact_path.name.startswith(".hermes-qa-")
        or artifact_path.suffix.lower() in RECORDING_SUFFIXES
        or artifact_path.name in GENERATED_ARTIFACT_FILENAMES
        or any(MEETING_DIRECTORY.fullmatch(part) for part in artifact_path.parts[:-1])
    )


def check_repository(root: Path) -> list[str]:
    ignore_file = root / ".gitignore"
    if not ignore_file.is_file():
        return ["missing .gitignore"]

    ignored_patterns = set(ignore_file.read_text(encoding="utf-8").splitlines())
    errors = [
        f"missing required ignore rule: {pattern}"
        for pattern in sorted(REQUIRED_IGNORE_PATTERNS - ignored_patterns)
    ]
    for path in _git(root, "ls-files", "-z"):
        if _is_meeting_artifact(path):
            errors.append(f"tracked meeting artifact: {path}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root to inspect")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()

    try:
        errors = check_repository(root)
    except RuntimeError as exc:
        print(f"repository hygiene: unable to inspect {root}: {exc}", file=sys.stderr)
        return 2

    if errors:
        print("repository hygiene: failed", file=sys.stderr)
        print(*errors, sep="\n", file=sys.stderr)
        return 1

    print("repository hygiene: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
