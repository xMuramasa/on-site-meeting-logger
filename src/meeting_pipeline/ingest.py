"""Safe source ingestion."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

from .errors import IngestError
from .manifest import create_manifest, load_manifest, meeting_lock, sha256_file, write_manifest
from .meetings import meeting_records
from .previous_context import (
    SUPPORTED_PREVIOUS_ACTA,
    canonical_acta_from_json,
    previous_acta_format,
)

SUPPORTED_AUDIO = {".m4a", ".wav", ".mp3", ".mp4", ".webm", ".ogg"}


def _copy_immutable(source: Path, target: Path) -> None:
    if target.exists():
        if not target.is_file() or sha256_file(source) != sha256_file(target):
            raise IngestError(f"refusing to replace different source: {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.copying")
    try:
        shutil.copy2(source, temporary)
        if sha256_file(source) != sha256_file(temporary):
            raise IngestError(f"source copy verification failed: {source}")
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()


def ingest_meeting(
    audio: Path,
    previous_acta: Path | None,
    meeting_date: date,
    output_root: Path | None = None,
    force: bool = False,
    meeting_id: UUID | None = None,
    title: str | None = None,
) -> Path:
    del force  # Source immutability is never bypassed.
    audio = Path(audio).expanduser().resolve()
    if not audio.is_file():
        raise IngestError(f"audio file not found: {audio}")
    if audio.suffix.lower() not in SUPPORTED_AUDIO:
        raise IngestError(
            f"unsupported audio format {audio.suffix!r}; use m4a, wav, mp3, mp4, webm, or ogg"
        )
    prior = Path(previous_acta).expanduser().resolve() if previous_acta else None
    if prior is not None and not prior.is_file():
        raise IngestError(f"previous acta not found: {prior}")
    try:
        prior_format = previous_acta_format(prior) if prior else None
        if prior_format == "application/json":
            assert prior is not None
            prior_date = canonical_acta_from_json(prior).meeting.date
        else:
            prior_date = None
    except Exception as exc:
        formats = ", ".join(suffix.removeprefix(".") for suffix in SUPPORTED_PREVIOUS_ACTA)
        message = f"previous acta must be an existing {formats} file: {prior}: {exc}"
        raise IngestError(message) from exc

    if output_root is None:
        root = (
            audio.parent.parent if audio.parent.name == meeting_date.isoformat() else audio.parent
        )
    else:
        root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with meeting_lock(root / ".ingest"):
        records = meeting_records(root)
        source_hash = sha256_file(audio)
        prior_hash = sha256_file(prior) if prior else None
        if meeting_id is not None:
            matches = [(p, m) for p, m in records if m.meeting_id == meeting_id]
            if matches:
                path, existing = matches[0]
                if (
                    existing.meeting_date != meeting_date
                    or existing.source_sha256 != source_hash
                    or existing.previous_acta_sha256 != prior_hash
                    or (title is not None and existing.title != title)
                ):
                    raise IngestError("meeting identifier describes different source or metadata")
                return path
        else:
            matches = [
                (p, m)
                for p, m in records
                if m.meeting_date == meeting_date
                and m.source_sha256 == source_hash
                and m.previous_acta_sha256 == prior_hash
            ]
            if len(matches) > 1:
                raise IngestError("multiple matching meetings; specify --meeting-id")
            if matches:
                return matches[0][0]
        identity = meeting_id or uuid4()
        meeting_dir = root / f"{meeting_date.isoformat()}--{identity}"
        return _ingest_sources(
            audio, prior, prior_format, prior_date, meeting_date, meeting_dir, identity, title
        )


def _ingest_sources(
    audio, prior, prior_format, prior_date, meeting_date, meeting_dir, identity, title
):
    source_dir = meeting_dir / "source"
    build_dir = meeting_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    copied_audio = source_dir / f"meeting{audio.suffix.lower()}"
    _copy_immutable(audio, copied_audio)
    copied_prior = source_dir / f"previous-acta{prior.suffix.lower()}" if prior else None
    if prior and copied_prior:
        _copy_immutable(prior, copied_prior)

    manifest_path = meeting_dir / "manifest.json"
    if manifest_path.exists():
        manifest = load_manifest(manifest_path)
        if manifest.source_sha256 != sha256_file(copied_audio):
            raise IngestError(f"manifest describes a different source: {meeting_dir}")
        expected_prior = sha256_file(copied_prior) if copied_prior else None
        if manifest.previous_acta_sha256 != expected_prior:
            raise IngestError(f"manifest describes a different previous acta: {meeting_dir}")
        if manifest.previous_acta_format is None and manifest.previous_acta_date is None:
            manifest.previous_acta_format = prior_format
            manifest.previous_acta_date = prior_date
            write_manifest(manifest_path, manifest)
        elif (
            manifest.previous_acta_format != prior_format
            or manifest.previous_acta_date != prior_date
        ):
            raise IngestError(f"manifest describes different previous acta metadata: {meeting_dir}")
    else:
        manifest = create_manifest(
            meeting_dir,
            meeting_date,
            copied_audio,
            prior,
            previous_acta_format=prior_format,
            previous_acta_date=prior_date,
        )
        manifest.meeting_id = identity
        manifest.title = title
        write_manifest(manifest_path, manifest)
    return meeting_dir
