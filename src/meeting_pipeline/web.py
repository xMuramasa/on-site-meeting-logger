"""Loopback-only web API for upload, recording, review, and finalization."""

from __future__ import annotations

import json
import secrets
import shutil
from collections.abc import Callable
from contextlib import suppress
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import yaml
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .errors import PipelineBusyError, PipelineError
from .ingest import SUPPORTED_AUDIO, ingest_meeting
from .manifest import (
    block_stage,
    fail_stage,
    load_manifest,
    meeting_lock,
    write_manifest,
    write_text_atomic,
)
from .meetings import find_meeting, meeting_artifact, meeting_key, meeting_records, meeting_source
from .models import AudioLevelAnalysis, CanonicalActa, ReviewState, Transcript
from .pipeline import (
    STAGES,
    clear_cancellation,
    invalidate_stages_from,
    request_cancellation,
    run_stages,
)
from .previous_context import SUPPORTED_PREVIOUS_ACTA
from .queue import DurableMeetingQueue, QueueJob
from .readiness import ReadinessCheck, ReadinessReport, check_readiness
from .review import apply_review, draft_hash, load_review

TRUSTED_ORIGINS = {
    "http://127.0.0.1",
    "http://localhost",
    "http://127.0.0.1:8765",
    "http://localhost:8765",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
}
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024
Runner = Callable[..., Any]
Readiness = Callable[[], ReadinessReport]

ARTIFACT_ROLES = {
    ("transcribe", "markdown"): "transcript",
    ("render", "digest"): "digest",
    ("render", "markdown"): "minutes",
    ("render", "html"): "minutes",
    ("export_pdf", "pdf"): "minutes",
}
FORMAT_LABELS = {
    ".md": "Markdown",
    ".html": "HTML",
    ".pdf": "PDF",
    ".json": "JSON",
    ".yaml": "YAML",
}
FINALIZATION_STAGES = ("approve", "render", "export_pdf", "validate")


def _durable_job(manifest: Any) -> dict[str, Any] | None:
    """Project API job status from the durable per-stage manifest, never process memory."""
    states = list(manifest.stages.items())
    for name, state in reversed(states):
        if state.status == "running":
            return {
                "status": "running",
                "stage": name,
                "phase": state.phase,
                "completed_chunks": state.completed_chunks,
                "total_chunks": state.total_chunks,
            }
    for name, state in reversed(states):
        if state.status == "blocked":
            return {"status": "blocked", "stage": name, "retryable": True}
    for name, state in reversed(states):
        if state.status in {"failed", "cancelled"}:
            result = {"status": state.status, "stage": name}
            if state.status == "failed":
                result["error_code"] = state.error_code or "PROCESSING_FAILED"
                result["retryable"] = state.retryable if state.retryable is not None else True
            return result
    for name, state in reversed(states):
        if state.status == "complete":
            return {"status": "complete", "stage": name}
    return None


def _mark_interrupted_jobs(root: Path) -> None:
    """Recover abandoned stages without modifying another process's live job."""
    for meeting_dir, _ in meeting_records(root):
        manifest_path = meeting_dir / "manifest.json"
        try:
            with meeting_lock(manifest_path.parent):
                manifest = load_manifest(manifest_path)
                for name, state in manifest.stages.items():
                    if state.status == "running":
                        fail_stage(
                            manifest,
                            name,
                            "JOB_INTERRUPTED",
                            error_code="JOB_INTERRUPTED",
                            retryable=True,
                        )
                        write_manifest(manifest_path, manifest)
                        break
        except PipelineError:
            continue


def _first_pending_stage(manifest: Any, until: str) -> str | None:
    """The stage a run would actually start at, so recording a wait never erases a result."""
    for name in STAGES[: STAGES.index(until) + 1]:
        state = manifest.stages.get(name)
        if state is None or state.status != "complete":
            return name
    return None


def _load_optional_manifest(path: Path) -> Any | None:
    try:
        return load_manifest(path / "manifest.json")
    except PipelineError:
        # A review-only legacy directory has no pipeline execution state yet.
        return None


def _queue_key(path: Path, manifest: Any | None) -> str:
    """Queue records use the same identity as the API: UUID, or the legacy date."""
    return meeting_key(manifest) if manifest is not None else path.name


def _queue_record(queued: QueueJob | None) -> dict[str, Any] | None:
    """Raw durable queue state; ``position`` is set only while the job waits in FIFO order."""
    if queued is None:
        return None
    return {
        "status": queued.status,
        "position": queued.position,
        "enqueued_at": queued.enqueued_at,
        "finished_at": queued.finished_at,
    }


def _merge_job(queued: QueueJob | None, manifest: Any | None) -> dict[str, Any] | None:
    """Project job state from the durable queue record and durable stage manifest.

    A live running stage always wins. A queued record wins next so the UI can show its FIFO
    position. Otherwise the most recent durable write wins: stage state for a meeting that was
    resumed after its queue record finished, or the queue terminal state (e.g. cancelled before
    any stage ran).
    """
    stage_job = _durable_job(manifest) if manifest is not None else None
    if queued is None or (stage_job and stage_job["status"] == "running"):
        return stage_job
    pending = _first_pending_stage(manifest, "generate_review") if manifest else None
    if queued.status == "queued":
        return {"status": "queued", "stage": pending or "inspect", "position": queued.position}
    if queued.status == "running":
        return {"status": "running", "stage": pending or "inspect"}
    if (
        manifest is not None
        and stage_job is not None
        and (
            queued.status == "completed"
            or queued.finished_at is None
            or manifest.updated_at > datetime.fromisoformat(queued.finished_at)
        )
    ):
        return stage_job
    if queued.status == "completed":
        return {"status": "complete", "stage": pending or "generate_review"}
    result: dict[str, Any] = {"status": queued.status, "stage": pending or "inspect"}
    if queued.status == "failed":
        result.update(error_code="PROCESSING_FAILED", retryable=True)
    return result


def _artifact_records(path: Path) -> list[dict[str, str | bool]]:
    """Expose only current manifest artifacts; legacy files remain untouched but undiscoverable."""
    try:
        manifest = load_manifest(path / "manifest.json")
    except PipelineError:
        return []

    stages = manifest.stages
    validated = all(
        stages.get(stage) is not None
        and stages[stage].status == "complete"
        and stages[stage].artifacts
        and all(Path(value).is_file() for value in stages[stage].artifacts.values())
        for stage in FINALIZATION_STAGES
    )
    records: list[dict[str, str | bool]] = []
    exposed_paths: set[Path] = set()
    for stage, label in ARTIFACT_ROLES:
        state = stages.get(stage)
        artifact = state.artifacts.get(label) if state and state.status == "complete" else None
        if artifact is None:
            continue
        candidate = Path(artifact)
        if not candidate.is_file() or candidate.resolve().parent != path.resolve():
            continue
        exposed_paths.add(candidate.resolve())
        role = ARTIFACT_ROLES[(stage, label)]
        records.append(
            {
                "name": candidate.name,
                "role": role,
                "format": FORMAT_LABELS.get(
                    candidate.suffix.lower(), candidate.suffix.upper().lstrip(".")
                ),
                "final": role == "minutes" and validated,
            }
        )
    for state in stages.values():
        if state.status != "complete":
            continue
        for artifact in state.artifacts.values():
            candidate = Path(artifact)
            if (
                not candidate.is_file()
                or candidate.resolve() in exposed_paths
                or candidate.resolve().parent != path.resolve()
            ):
                continue
            records.append(
                {
                    "name": candidate.name,
                    "role": "supporting",
                    "format": FORMAT_LABELS.get(
                        candidate.suffix.lower(), candidate.suffix.upper().lstrip(".")
                    ),
                    "final": False,
                }
            )
    return records


def _approves_final(path: Path) -> bool:
    try:
        return load_review(path / "review.yaml").approve_for_final_render
    except PipelineError:
        return False


def _safe_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="meeting_date must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise HTTPException(status_code=422, detail="meeting_date must be YYYY-MM-DD")
    return parsed


async def _save_upload(upload: UploadFile, target: Path, allowed: set[str]) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in allowed:
        raise HTTPException(
            status_code=415, detail=f"unsupported file extension: {suffix or 'none'}"
        )
    target = target.with_suffix(suffix)
    size = 0
    try:
        with target.open("wb") as handle:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="upload exceeds 4 GiB")
                handle.write(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail="uploaded file is empty")
        return target
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


def create_app(
    *,
    output_root: Path,
    config_path: Path | None = None,
    csrf_token: str | None = None,
    runner: Runner = run_stages,
    static_dir: Path | None = None,
    allowed_origins: set[str] | None = None,
    readiness: Readiness | None = None,
) -> FastAPI:
    """Create an app that can only mutate local pipeline state."""
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    queue = DurableMeetingQueue(root)
    _mark_interrupted_jobs(root)
    incoming = root / ".incoming"
    incoming.mkdir(mode=0o700, exist_ok=True)
    if readiness is None:

        def readiness() -> ReadinessReport:
            try:
                from .config import load_config

                return check_readiness(load_config(config_path), root)
            except PipelineError as exc:
                return ReadinessReport(
                    checks=[ReadinessCheck(name="configuration", ok=False, detail=str(exc))]
                )

    token = csrf_token or secrets.token_urlsafe(32)
    origins = allowed_origins or TRUSTED_ORIGINS

    app = FastAPI(title="Meeting Studio", docs_url=None, redoc_url=None)
    app.state.output_root = root
    app.state.queue = queue
    app.state.csrf_token = token

    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("origin") not in origins:
                return JSONResponse({"detail": "untrusted origin"}, status_code=403)
            if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), token):
                return JSONResponse({"detail": "invalid CSRF token"}, status_code=403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self' http://127.0.0.1:5173; "
            "media-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "microphone=(self)"
        return response

    def start_job(meeting_dir: Path, until: str) -> None:
        try:
            runner(meeting_dir, until=until, config_path=config_path)
        except PipelineBusyError as exc:
            # Contention is not a failure: record it as blocked so the UI can say so and the
            # operator can resume, without overwriting the stage state of the winning job.
            manifest_path = meeting_dir / "manifest.json"
            if manifest_path.is_file():
                try:
                    with meeting_lock(meeting_dir):
                        manifest = load_manifest(manifest_path)
                        pending = _first_pending_stage(manifest, until)
                        if pending and not any(
                            state.status == "running" for state in manifest.stages.values()
                        ):
                            block_stage(manifest, pending, str(exc))
                            write_manifest(manifest_path, manifest)
                except PipelineBusyError:
                    # Another runner owns this meeting; do not write from a stale snapshot.
                    pass
            return
        except Exception:
            # run_stages persists its active stage. This keeps custom runners safe too.
            manifest_path = meeting_dir / "manifest.json"
            if manifest_path.is_file():
                manifest = load_manifest(manifest_path)
                if not any(state.status == "failed" for state in manifest.stages.values()):
                    fail_stage(
                        manifest,
                        until,
                        "PROCESSING_FAILED",
                        error_code="PROCESSING_FAILED",
                        retryable=True,
                    )
                    write_manifest(manifest_path, manifest)

    def meeting_path(value: str) -> Path:
        try:
            return find_meeting(root, value)
        except PipelineError as exc:
            # Legacy review-only directories remain usable without being listed as jobs.
            if len(value) == 10 and _safe_date(value):
                legacy = root / value
                if legacy.is_dir() and not legacy.is_symlink():
                    return legacy
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def job_for_meeting(path: Path, manifest: Any | None = None) -> dict[str, Any] | None:
        manifest = manifest if manifest is not None else _load_optional_manifest(path)
        return _merge_job(queue.get(_queue_key(path, manifest)), manifest)

    def reject_active_job(path: Path, manifest: Any | None = None) -> None:
        job = job_for_meeting(path, manifest)
        if job and job["status"] == "running":
            raise HTTPException(
                status_code=409, detail="a pipeline job is already active for this meeting"
            )
        # One deployment root, one expensive job: the local models cannot serve two meetings.
        for other, _ in meeting_records(root):
            if other.resolve() == path.resolve():
                continue
            running = job_for_meeting(other)
            if running and running["status"] == "running":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"the meeting of {other.name} is still being processed on this "
                        "deployment; wait for it to finish or cancel it first"
                    ),
                )

    @app.get("/api/bootstrap")
    def bootstrap() -> dict[str, Any]:
        report = readiness()
        return {
            "csrf_token": token,
            "output_root": str(root),
            "accepted_audio": sorted(SUPPORTED_AUDIO),
            "recording_supported": True,
            "readiness": report.model_dump(mode="json"),
        }

    @app.get("/api/meetings")
    def list_meetings() -> dict[str, Any]:
        items = []
        queued = queue.jobs()
        for _, manifest in sorted(
            meeting_records(root), key=lambda record: record[1].created_at, reverse=True
        ):
            key = manifest.meeting_date.isoformat()
            items.append(
                {
                    "id": meeting_key(manifest),
                    "title": manifest.title or "Reunión semanal",
                    "date": key,
                    "source": manifest.source_filename,
                    "stages": {name: state.status for name, state in manifest.stages.items()},
                    "job": _merge_job(queued.get(meeting_key(manifest)), manifest),
                    "queue": _queue_record(queued.get(meeting_key(manifest))),
                }
            )
        return {"meetings": items}

    @app.post("/api/meetings", status_code=202)
    async def create_meeting(
        background: BackgroundTasks,
        meeting_date: str = Form(...),
        audio: UploadFile = File(...),
        previous_acta: UploadFile | None = File(None),
        meeting_id: UUID | None = Form(None),
        title: str | None = Form(None, min_length=1, max_length=200),
    ) -> dict[str, Any]:
        parsed = _safe_date(meeting_date)
        identity = meeting_id or uuid4()
        existing = next((m for _, m in meeting_records(root) if m.meeting_id == identity), None)
        existing_id = existing is not None
        report = readiness() if not existing_id else ReadinessReport(checks=[])
        if not report.ok:
            failures = "; ".join(
                f"{check.name}: {check.detail}" for check in report.checks if not check.ok
            )
            raise HTTPException(status_code=503, detail=f"pipeline is not ready: {failures}")
        # Retries acknowledge durable ingestion even when models are now unavailable.
        if not existing_id:
            reject_active_job(root / f"{parsed.isoformat()}--{identity}")
        if title is None:
            if existing is not None:
                title = existing.title
            else:
                from .config import load_config

                title = load_config(config_path).meeting.title
        request_dir = incoming / secrets.token_urlsafe(16)
        request_dir.mkdir(mode=0o700)
        try:
            audio_path = await _save_upload(audio, request_dir / "audio", SUPPORTED_AUDIO)
            previous_path = None
            if previous_acta is not None and previous_acta.filename:
                previous_path = await _save_upload(
                    previous_acta, request_dir / "previous-acta", set(SUPPORTED_PREVIOUS_ACTA)
                )
            meeting_dir = ingest_meeting(
                audio_path,
                previous_path,
                parsed,
                output_root=root,
                meeting_id=identity,
                title=title,
            )
        except PipelineError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            shutil.rmtree(request_dir, ignore_errors=True)
        if not existing_id:
            background.add_task(start_job, meeting_dir, "generate_review")
        return {"id": str(identity), "date": parsed.isoformat(), "status": "accepted"}

    @app.get("/api/meetings/{meeting_date}")
    def get_meeting(meeting_date: str) -> dict[str, Any]:
        path = meeting_path(meeting_date)
        analysis_path = path / "build" / "audio-levels.json"
        audio_analysis = None
        if analysis_path.is_file():
            with suppress(OSError, ValueError):
                audio_analysis = AudioLevelAnalysis.model_validate_json(
                    analysis_path.read_text(encoding="utf-8")
                ).model_dump(mode="json")
        review_path = path / "review.yaml"
        review = None
        review_error = None
        if review_path.is_file():
            try:
                review = load_review(review_path).model_dump(mode="json")
            except PipelineError as exc:
                review_error = str(exc)
        manifest = (
            load_manifest(path / "manifest.json") if (path / "manifest.json").exists() else None
        )
        queued = queue.get(_queue_key(path, manifest))
        return {
            "id": meeting_key(manifest) if manifest else meeting_date,
            "date": manifest.meeting_date.isoformat() if manifest else meeting_date,
            "title": manifest.title if manifest else None,
            "review_error": review_error,
            "job": _merge_job(queued, manifest),
            "queue": _queue_record(queued),
            "audio_analysis": audio_analysis,
            "review": review,
            "artifacts": _artifact_records(path),
        }

    def read_draft(path: Path) -> CanonicalActa:
        try:
            return CanonicalActa.model_validate_json(
                meeting_artifact(path, "build", "acta-draft.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError, PipelineError) as exc:
            raise HTTPException(status_code=409, detail="draft is not available") from exc

    def validate_review(path: Path, review: ReviewState) -> CanonicalActa:
        draft = read_draft(path)
        if review.draft_hash is not None and review.draft_hash != draft_hash(draft):
            raise HTTPException(status_code=409, detail="draft changed; reload before saving")
        try:
            approved = apply_review(draft, review, require_approval=False)
            transcript_path = path / "build" / "transcript.json"
            if transcript_path.is_file():
                transcript = Transcript.model_validate_json(transcript_path.read_text())
                known = {segment.id for segment in transcript.segments}
                for edit in review.content_edits:
                    for evidence in edit.evidence or []:
                        if not set(evidence.segment_ids) <= known:
                            raise ValueError("citation references unknown transcript segments")
            return approved
        except (PipelineError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/meetings/{meeting_date}/draft")
    def get_draft(meeting_date: str) -> dict:
        draft = read_draft(meeting_path(meeting_date))
        return {"draft": draft.model_dump(mode="json"), "draft_hash": draft_hash(draft)}

    @app.get("/api/meetings/{meeting_date}/transcript")
    def get_transcript(meeting_date: str) -> dict:
        try:
            path = meeting_artifact(meeting_path(meeting_date), "build", "transcript.json")
        except PipelineError as exc:
            raise HTTPException(status_code=404, detail="transcript is not available") from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="transcript is not available")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.get("/api/meetings/{meeting_date}/audio")
    def get_audio(meeting_date: str) -> FileResponse:
        path = meeting_path(meeting_date)
        manifest = load_manifest(path / "manifest.json")
        try:
            source = meeting_source(path, manifest.source_filename)
        except PipelineError as exc:
            raise HTTPException(status_code=404, detail="audio is not available") from exc
        return FileResponse(source)

    @app.post("/api/meetings/{meeting_date}/review-preview")
    def preview_review(meeting_date: str, review: ReviewState) -> dict:
        return validate_review(meeting_path(meeting_date), review).model_dump(mode="json")

    @app.put("/api/meetings/{meeting_date}/review")
    def save_review(meeting_date: str, review: ReviewState) -> dict[str, bool]:
        path = meeting_path(meeting_date)
        serialized = yaml.safe_dump(
            review.model_dump(mode="json"), allow_unicode=True, sort_keys=False
        )
        try:
            with meeting_lock(path):
                if (path / "build" / "acta-draft.json").is_file():
                    validate_review(path, review)
                elif review.content_edits:
                    raise HTTPException(status_code=409, detail="draft is not available")
                write_text_atomic(path / "review.yaml", serialized)
                manifest_path = path / "manifest.json"
                if manifest_path.is_file():
                    manifest = load_manifest(manifest_path)
                    invalidate_stages_from(manifest, "approve")
                    write_manifest(manifest_path, manifest)
        except PipelineError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"saved": True}

    @app.post("/api/meetings/{meeting_date}/finalize", status_code=202)
    def finalize(meeting_date: str, background: BackgroundTasks) -> dict[str, str]:
        path = meeting_path(meeting_date)
        reject_active_job(path)
        try:
            review = load_review(path / "review.yaml")
        except PipelineError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not review.approve_for_final_render:
            raise HTTPException(status_code=409, detail="approve the review before finalizing")
        background.add_task(start_job, path, "validate")
        return {"date": meeting_date, "status": "accepted"}

    @app.post("/api/meetings/{meeting_date}/cancel", status_code=202)
    def cancel(meeting_date: str) -> dict[str, str]:
        path = meeting_path(meeting_date)
        manifest = _load_optional_manifest(path)
        key = _queue_key(path, manifest)
        day = manifest.meeting_date.isoformat() if manifest is not None else meeting_date
        # Queued work is removed from FIFO order durably; workers can never claim it afterwards.
        if queue.cancel(key):
            return {"id": key, "date": day, "status": "cancelled"}
        queued = queue.get(key)
        stage_job = _durable_job(manifest) if manifest is not None else None
        stage_running = bool(stage_job and stage_job["status"] == "running")
        if queued is not None and queued.status != "running" and not stage_running:
            raise HTTPException(
                status_code=409, detail="only queued or running meetings can be cancelled"
            )
        # Running work stops cooperatively at its next checkpoint.
        request_cancellation(path)
        return {"id": key, "date": day, "status": "cancellation_requested"}

    @app.post("/api/meetings/{meeting_date}/restart", status_code=202)
    def restart(meeting_date: str, background: BackgroundTasks) -> dict[str, str]:
        path = meeting_path(meeting_date)
        try:
            manifest = load_manifest(path / "manifest.json")
        except PipelineError as exc:
            raise HTTPException(
                status_code=409, detail="this meeting has no processing state to resume"
            ) from exc
        reject_active_job(path, manifest)
        if any(
            state.status == "failed" and state.error_code == "NO_SPEECH"
            for state in manifest.stages.values()
        ):
            raise HTTPException(
                status_code=409, detail="upload audio with audible speech before retrying"
            )
        clear_cancellation(path)
        # Resuming finalization without a currently approved review would only fail at `approve`.
        finalization_started = any(name in FINALIZATION_STAGES for name in manifest.stages)
        until = "validate" if finalization_started and _approves_final(path) else "generate_review"
        background.add_task(start_job, path, until)
        return {"date": meeting_date, "status": "accepted"}

    @app.get("/api/meetings/{meeting_date}/files/{filename}")
    def download(meeting_date: str, filename: str) -> FileResponse:
        path = meeting_path(meeting_date)
        if Path(filename).name != filename:
            raise HTTPException(status_code=404, detail="file not found")
        if filename not in {str(artifact["name"]) for artifact in _artifact_records(path)}:
            raise HTTPException(status_code=404, detail="file not found")
        target = path / filename
        return FileResponse(target, filename=filename)

    assets = Path(static_dir).resolve() if static_dir else Path(__file__).parent / "_web"
    if assets.is_dir():
        app.mount("/", StaticFiles(directory=assets, html=True), name="web")
    return app
