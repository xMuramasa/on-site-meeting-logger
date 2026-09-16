"""Atomic manifests and stage fingerprints."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from . import PIPELINE_VERSION
from .errors import IngestError, PipelineBusyError
from .models import PipelineManifest, StageState


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(*values: object) -> str:
    blob = json.dumps(values, sort_keys=True, ensure_ascii=False, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def write_text_atomic(path: Path, text: str) -> Path:
    """Temp file in the same directory + rename, so a crash never leaves half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name)
        raise
    return path


def write_json_atomic(path: Path, data: Any) -> Path:
    return write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def create_manifest(
    meeting_dir: Path,
    meeting_date: date,
    source: Path,
    previous_acta: Path | None,
    previous_acta_format: str | None = None,
    previous_acta_date: date | None = None,
) -> PipelineManifest:
    now = datetime.now(UTC)
    return PipelineManifest(
        pipeline_version=PIPELINE_VERSION,
        meeting_dir=str(Path(meeting_dir).resolve()),
        meeting_date=meeting_date,
        created_at=now,
        updated_at=now,
        source_filename=source.name,
        source_sha256=sha256_file(source),
        previous_acta_filename=previous_acta.name if previous_acta else None,
        previous_acta_sha256=sha256_file(previous_acta) if previous_acta else None,
        previous_acta_format=previous_acta_format,
        previous_acta_date=previous_acta_date,
    )


def write_manifest(path: Path, manifest: PipelineManifest) -> None:
    manifest.updated_at = datetime.now(UTC)
    write_json_atomic(path, manifest.model_dump(mode="json"))


def load_manifest(path: Path) -> PipelineManifest:
    path = Path(path)
    if not path.is_file():
        raise IngestError(f"manifest not found: {path}")
    try:
        return PipelineManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise IngestError(f"invalid manifest: {path}: {exc}") from exc


def complete_stage(
    manifest: PipelineManifest,
    name: str,
    stage_fingerprint: str,
    artifacts: dict[str, Path],
) -> None:
    artifact_map = {label: str(Path(path).resolve()) for label, path in artifacts.items()}
    manifest.stages[name] = StageState(
        status="complete",
        fingerprint=stage_fingerprint,
        started_at=manifest.stages.get(name, StageState()).started_at or datetime.now(UTC),
        completed_at=datetime.now(UTC),
        artifacts=artifact_map,
    )


def fail_stage(
    manifest: PipelineManifest,
    name: str,
    note: str,
    *,
    error_code: str | None = None,
    retryable: bool | None = None,
) -> None:
    state = manifest.stage(name)
    state.status = "failed"
    state.note = note
    state.error_code = error_code
    state.retryable = retryable
    state.failed_at = datetime.now(UTC)


def start_stage(manifest: PipelineManifest, name: str) -> None:
    manifest.stages[name] = StageState(status="running", started_at=datetime.now(UTC))


def cancel_stage(manifest: PipelineManifest, name: str, note: str) -> None:
    state = manifest.stage(name)
    state.status = "cancelled"
    state.note = note
    state.failed_at = datetime.now(UTC)


def block_stage(manifest: PipelineManifest, name: str, note: str) -> None:
    """Resource contention is not a failure: the work never started and stays resumable."""
    manifest.stages[name] = StageState(status="blocked", note=note, retryable=True)


DEPLOYMENT_LOCK_NAME = ".deployment.lock"
DEPLOYMENT_LOCK_WAIT_SECONDS = 30.0


@contextlib.contextmanager
def deployment_lock(root: Path, timeout_seconds: float = DEPLOYMENT_LOCK_WAIT_SECONDS):
    """Serialize expensive work across *different* meetings sharing one deployment root.

    The per-meeting lock only stops two runs of the same meeting. On a single Mac the
    transcription model and the reasoning server compete for the same unified memory, so
    two meetings — CLI and web, or two CLI shells — must not decode at once.
    """
    lock_path = Path(root) / DEPLOYMENT_LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    with lock_path.open("a+") as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise PipelineBusyError(
                        "another meeting is already using the models on this deployment; "
                        f"waited {max(0.0, timeout_seconds):.0f}s. Wait for it to finish, "
                        "then resume this meeting."
                    ) from exc
                # Poll instead of blocking flock, so the wait stays bounded.
                time.sleep(0.25)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextlib.contextmanager
def meeting_lock(meeting_dir: Path):
    """Hold a non-blocking advisory lock while changing one meeting's pipeline state."""
    lock_path = Path(meeting_dir) / "build" / ".pipeline.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            message = "another pipeline run is already active for this meeting"
            raise PipelineBusyError(message) from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def stage_is_current(manifest: PipelineManifest, name: str, stage_fingerprint: str) -> bool:
    state = manifest.stages.get(name)
    if state is None or state.status != "complete" or state.fingerprint != stage_fingerprint:
        return False
    return bool(state.artifacts) and all(Path(path).is_file() for path in state.artifacts.values())
