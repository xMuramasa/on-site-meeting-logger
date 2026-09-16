"""Preflight diagnostics for the local meeting-processing environment."""
from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from .config import PdfSettings, Settings, TranscriptionSettings
from .pdf import export_pdf
from .transcription import _load_model

MIN_FREE_DISK_BYTES = 5 * 1024**3


class ReadinessCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    ok: bool
    detail: str


class ReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: list[ReadinessCheck]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        return {"ok": self.ok, **super().model_dump(**kwargs)}


def _check_model_endpoint(
    settings: Settings, client: httpx.Client
) -> tuple[ReadinessCheck, list[str] | None]:
    headers: dict[str, str] = {}
    if key := settings.reasoning.resolve_api_key():
        headers["Authorization"] = f"Bearer {key}"
    url = f"{settings.reasoning.base_url.rstrip('/')}/models"
    try:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json().get("data")
        if not isinstance(data, list):
            raise ValueError("response does not contain a model list")
        model_ids = [
            item["id"]
            for item in data
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        return ReadinessCheck(name="model-endpoint", ok=False, detail=str(exc)), None
    return ReadinessCheck(name="model-endpoint", ok=True, detail=url), model_ids


def _check_model_identity(settings: Settings, model_ids: list[str] | None) -> ReadinessCheck:
    expected = settings.reasoning.model
    if model_ids is None:
        return ReadinessCheck(
            name="model-identity", ok=False, detail="model endpoint is unavailable"
        )
    if expected not in model_ids:
        return ReadinessCheck(
            name="model-identity",
            ok=False,
            detail=f"configured model {expected!r} was not returned by /models",
        )
    return ReadinessCheck(name="model-identity", ok=True, detail=expected)


def _check_pdf(
    output_root: Path,
    settings: PdfSettings,
    pdf_exporter: Callable[[Path, Path, PdfSettings], Path],
) -> ReadinessCheck:
    try:
        with tempfile.TemporaryDirectory(prefix=".readiness-", dir=output_root) as directory:
            workdir = Path(directory)
            html = workdir / "smoke.html"
            pdf = workdir / "smoke.pdf"
            html.write_text("<p>Meeting Pipeline readiness check</p>", encoding="utf-8")
            pdf_exporter(html, pdf, settings)
            if not pdf.is_file() or pdf.stat().st_size == 0:
                raise RuntimeError("PDF exporter did not produce a PDF")
    except Exception as exc:
        return ReadinessCheck(name="chromium-pdf", ok=False, detail=str(exc))
    return ReadinessCheck(name="chromium-pdf", ok=True, detail="headless PDF smoke test passed")


def _check_output_permissions(output_root: Path) -> ReadinessCheck:
    try:
        with tempfile.TemporaryDirectory(prefix=".readiness-", dir=output_root):
            pass
    except OSError as exc:
        return ReadinessCheck(name="output-permissions", ok=False, detail=str(exc))
    return ReadinessCheck(name="output-permissions", ok=True, detail=str(output_root))


def check_readiness(
    settings: Settings,
    output_root: Path,
    *,
    client: httpx.Client | None = None,
    model_loader: Callable[[TranscriptionSettings], Any] = _load_model,
    command_locator: Callable[[str], str | None] = shutil.which,
    pdf_exporter: Callable[[Path, Path, PdfSettings], Path] = export_pdf,
    disk_usage: Callable[[str | Path], Any] = shutil.disk_usage,
) -> ReadinessReport:
    """Run independent checks without leaking model credentials or meeting data."""
    root = Path(output_root).expanduser().resolve()
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        output_check = ReadinessCheck(name="output-permissions", ok=False, detail=str(exc))
    else:
        output_check = _check_output_permissions(root)
    owns_client = client is None
    http_client = client or httpx.Client(timeout=min(settings.reasoning.timeout_seconds, 10.0))
    try:
        endpoint, model_ids = _check_model_endpoint(settings, http_client)
    finally:
        if owns_client:
            http_client.close()

    checks = [endpoint, _check_model_identity(settings, model_ids)]
    transcription = settings.transcription
    try:
        model_loader(transcription)
    except Exception as exc:
        # The loader's own message names the provider and the extra to install; device
        # advice belongs to the provider, not to this check.
        checks.append(ReadinessCheck(name="transcription-model", ok=False, detail=str(exc)))
    else:
        checks.append(
            ReadinessCheck(
                name="transcription-model",
                ok=True,
                detail=f"{transcription.provider}: {transcription.model}",
            )
        )

    missing = [name for name in ("ffmpeg", "ffprobe") if not command_locator(name)]
    checks.append(
        ReadinessCheck(
            name="ffmpeg",
            ok=not missing,
            detail="missing: " + ", ".join(missing) if missing else "ffmpeg and ffprobe found",
        )
    )
    checks.append(output_check)
    if output_check.ok:
        checks.append(_check_pdf(root, settings.pdf, pdf_exporter))
    else:
        checks.append(
            ReadinessCheck(
                name="chromium-pdf",
                ok=False,
                detail=f"output directory unavailable: {output_check.detail}",
            )
        )
    try:
        free = disk_usage(root if output_check.ok else root.parent).free
    except OSError as exc:
        checks.append(ReadinessCheck(name="free-disk-space", ok=False, detail=str(exc)))
    else:
        checks.append(
            ReadinessCheck(
                name="free-disk-space",
                ok=free >= MIN_FREE_DISK_BYTES,
                detail=f"free={free} bytes; required={MIN_FREE_DISK_BYTES} bytes",
            )
        )
    return ReadinessReport(checks=checks)
