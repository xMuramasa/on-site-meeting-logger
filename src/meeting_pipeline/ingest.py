"""Safe source ingestion."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

from .errors import IngestError
from .manifest import create_manifest, load_manifest, sha256_file, write_manifest

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
    if prior is not None and (not prior.is_file() or prior.suffix.lower() != ".pdf"):
        raise IngestError(f"previous acta must be an existing PDF: {prior}")

    if output_root is None:
        root = (
            audio.parent.parent if audio.parent.name == meeting_date.isoformat() else audio.parent
        )
    else:
        root = Path(output_root).expanduser().resolve()
    meeting_dir = root / meeting_date.isoformat()
    source_dir = meeting_dir / "source"
    build_dir = meeting_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    copied_audio = source_dir / f"meeting{audio.suffix.lower()}"
    _copy_immutable(audio, copied_audio)
    copied_prior = source_dir / "previous-acta.pdf" if prior else None
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
    else:
        manifest = create_manifest(meeting_dir, meeting_date, copied_audio, copied_prior)
        write_manifest(manifest_path, manifest)
    return meeting_dir
