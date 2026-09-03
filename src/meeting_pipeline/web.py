"""Loopback-only web API for upload, recording, review, and finalization."""
from __future__ import annotations

import secrets
import shutil
import threading
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .errors import PipelineError
from .ingest import SUPPORTED_AUDIO, ingest_meeting
from .manifest import load_manifest, write_text_atomic
from .models import ReviewState
from .pipeline import run_stages
from .review import load_review

TRUSTED_ORIGINS = {
    "http://127.0.0.1",
    "http://localhost",
    "http://127.0.0.1:8765",
    "http://localhost:8765",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
}
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024
DOWNLOAD_SUFFIXES = {".md", ".html", ".pdf", ".json", ".yaml"}
Runner = Callable[..., Any]


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
) -> FastAPI:
    """Create an app that can only mutate local pipeline state."""
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    incoming = root / ".incoming"
    incoming.mkdir(mode=0o700, exist_ok=True)
    token = csrf_token or secrets.token_urlsafe(32)
    origins = allowed_origins or TRUSTED_ORIGINS
    jobs: dict[str, dict[str, str]] = {}
    lock = threading.RLock()

    app = FastAPI(title="Meeting Studio", docs_url=None, redoc_url=None)
    app.state.output_root = root
    app.state.csrf_token = token
    app.state.jobs = jobs
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

    def start_job(key: str, meeting_dir: Path, until: str) -> None:
        with lock:
            jobs[key] = {"status": "running", "stage": until}
        try:
            runner(meeting_dir, until=until, config_path=config_path)
        except Exception as exc:  # surfaced in job status, never source contents
            with lock:
                jobs[key] = {"status": "failed", "stage": until, "error": str(exc)}
        else:
            with lock:
                jobs[key] = {"status": "complete", "stage": until}

    def meeting_path(value: str) -> Path:
        parsed = _safe_date(value)
        path = root / parsed.isoformat()
        if not path.is_dir():
            raise HTTPException(status_code=404, detail="meeting not found")
        return path

    @app.get("/api/bootstrap")
    def bootstrap() -> dict[str, Any]:
        return {
            "csrf_token": token,
            "output_root": str(root),
            "accepted_audio": sorted(SUPPORTED_AUDIO),
            "recording_supported": True,
        }

    @app.get("/api/meetings")
    def list_meetings() -> dict[str, Any]:
        items = []
        for manifest_path in sorted(root.glob("????-??-??/manifest.json"), reverse=True):
            try:
                manifest = load_manifest(manifest_path)
            except PipelineError:
                continue
            key = manifest.meeting_date.isoformat()
            items.append(
                {
                    "date": key,
                    "source": manifest.source_filename,
                    "stages": {
                        name: state.status for name, state in manifest.stages.items()
                    },
                    "job": jobs.get(key),
                }
            )
        return {"meetings": items}

    @app.post("/api/meetings", status_code=202)
    async def create_meeting(
        background: BackgroundTasks,
        meeting_date: str = Form(...),
        audio: UploadFile = File(...),
        previous_acta: UploadFile | None = File(None),
    ) -> dict[str, Any]:
        parsed = _safe_date(meeting_date)
        request_dir = incoming / secrets.token_urlsafe(16)
        request_dir.mkdir(mode=0o700)
        try:
            audio_path = await _save_upload(audio, request_dir / "audio", SUPPORTED_AUDIO)
            previous_path = None
            if previous_acta is not None and previous_acta.filename:
                previous_path = await _save_upload(
                    previous_acta, request_dir / "previous-acta", {".pdf"}
                )
            meeting_dir = ingest_meeting(audio_path, previous_path, parsed, output_root=root)
        except PipelineError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            shutil.rmtree(request_dir, ignore_errors=True)
        background.add_task(start_job, parsed.isoformat(), meeting_dir, "generate_review")
        return {"date": parsed.isoformat(), "status": "accepted"}

    @app.get("/api/meetings/{meeting_date}")
    def get_meeting(meeting_date: str) -> dict[str, Any]:
        path = meeting_path(meeting_date)
        review_path = path / "review.yaml"
        review = None
        if review_path.is_file():
            try:
                review = load_review(review_path).model_dump(mode="json")
            except PipelineError as exc:
                review = {"error": str(exc)}
        files = [
            item.name
            for item in sorted(path.iterdir())
            if item.is_file() and item.suffix.lower() in DOWNLOAD_SUFFIXES
        ]
        return {
            "date": meeting_date,
            "job": jobs.get(meeting_date),
            "review": review,
            "files": files,
        }

    @app.put("/api/meetings/{meeting_date}/review")
    def save_review(meeting_date: str, review: ReviewState) -> dict[str, bool]:
        path = meeting_path(meeting_date)
        serialized = yaml.safe_dump(
            review.model_dump(mode="json"), allow_unicode=True, sort_keys=False
        )
        write_text_atomic(path / "review.yaml", serialized)
        return {"saved": True}

    @app.post("/api/meetings/{meeting_date}/finalize", status_code=202)
    def finalize(meeting_date: str, background: BackgroundTasks) -> dict[str, str]:
        path = meeting_path(meeting_date)
        try:
            review = load_review(path / "review.yaml")
        except PipelineError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not review.approve_for_final_render:
            raise HTTPException(status_code=409, detail="approve the review before finalizing")
        background.add_task(start_job, meeting_date, path, "validate")
        return {"date": meeting_date, "status": "accepted"}

    @app.get("/api/meetings/{meeting_date}/files/{filename}")
    def download(meeting_date: str, filename: str) -> FileResponse:
        path = meeting_path(meeting_date)
        unsafe_name = Path(filename).name != filename
        unsupported = Path(filename).suffix.lower() not in DOWNLOAD_SUFFIXES
        if unsafe_name or unsupported:
            raise HTTPException(status_code=404, detail="file not found")
        target = path / filename
        if not target.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(target, filename=filename)

    assets = Path(static_dir).resolve() if static_dir else Path(__file__).parent / "_web"
    if assets.is_dir():
        app.mount("/", StaticFiles(directory=assets, html=True), name="web")
    return app
