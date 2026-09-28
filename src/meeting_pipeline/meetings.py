"""Identity and bounded discovery for legacy and UUID meeting directories."""

from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID

from .errors import IngestError, PipelineError
from .manifest import load_manifest
from .models import PipelineManifest

DIRECTORY_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}(?:--[0-9a-f-]{36})?")


def meeting_records(root: Path) -> list[tuple[Path, PipelineManifest]]:
    records = []
    for path in sorted(root.iterdir()) if root.is_dir() else []:
        if not DIRECTORY_PATTERN.fullmatch(path.name) or path.is_symlink():
            continue
        try:
            records.append((path, load_manifest(path / "manifest.json")))
        except PipelineError:
            continue
    return records


def meeting_key(manifest: PipelineManifest) -> str:
    return str(manifest.meeting_id or manifest.meeting_date.isoformat())


def find_meeting(root: Path, key: str) -> Path:
    # Only an exact legacy date or UUID is accepted, never arbitrary filesystem paths.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", key):
        try:
            if str(UUID(key)) != key:
                raise ValueError
        except ValueError as exc:
            raise IngestError("invalid meeting identifier") from exc
    matches = [path for path, manifest in meeting_records(root) if meeting_key(manifest) == key]
    if len(matches) != 1:
        raise IngestError("meeting not found or identifier is ambiguous")
    return matches[0]


def meeting_source(path: Path, filename: str) -> Path:
    candidate = meeting_artifact(path, "source", filename)
    if candidate.parent != path.resolve() / "source" or not candidate.is_file():
        raise IngestError("meeting source not found")
    return candidate


def meeting_artifact(path: Path, *parts: str) -> Path:
    candidate = path.joinpath(*parts).resolve()
    if not candidate.is_relative_to(path.resolve()):
        raise IngestError("artifact is outside the meeting directory")
    return candidate
