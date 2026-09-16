"""Local transcription (faster-whisper or MLX Whisper) with dependency injection for tests."""

from __future__ import annotations

import contextlib
import platform
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .config import TranscriptionSettings
from .errors import AudioDecodeError, ModelUnavailableError, NoSpeechError, TranscriptionError
from .manifest import write_json_atomic
from .models import AudioMetadata, EvidenceRange, Transcript, TranscriptSegment, format_timestamp
from .transcript_quality import detect_degraded_ranges

MLX_TURBO_MODEL = "mlx-community/whisper-large-v3-turbo"


def _import_mlx_whisper() -> Any:
    """Import the optional macOS-only backend, or explain exactly what is missing."""
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise ModelUnavailableError(
            "mlx-whisper requires macOS on Apple Silicon (arm64); "
            "use transcription.provider: faster-whisper on this machine"
        )
    try:
        import mlx_whisper
    except ImportError as exc:
        raise ModelUnavailableError(
            "mlx-whisper is not installed; run `uv sync --extra stt-mlx`"
        ) from exc
    return mlx_whisper


def _release_mlx_weights() -> None:
    """Drop the process-wide weight cache so the reasoning model can have the memory back.

    `mlx_whisper.transcribe` keeps the last model on a module-level `ModelHolder`; on a
    16 GB machine that would sit next to llama.cpp for the rest of the run. Releasing must
    never turn a finished transcription into a failure, hence the broad suppression.
    """
    with contextlib.suppress(Exception):
        from mlx_whisper.transcribe import ModelHolder

        ModelHolder.model = None
        ModelHolder.model_path = None
    with contextlib.suppress(Exception):
        import mlx.core as mx

        clear = getattr(mx, "clear_cache", None) or mx.metal.clear_cache
        clear()


class MlxWhisperEngine:
    """Duck-types faster-whisper's `.transcribe()` so the pipeline stays backend-neutral.

    Upstream (`mlx_whisper/transcribe.py`) returns `{"text", "segments", "language"}` and
    reports no language probability, so none is fabricated here.
    """

    def __init__(self, settings: TranscriptionSettings) -> None:
        self.settings = settings
        # Verify availability now; weights download/load lazily on the first transcription.
        _import_mlx_whisper()

    def transcribe(
        self,
        audio: str,
        *,
        language: str | None = None,
        vad_filter: bool = False,
        beam_size: int = 1,
    ) -> tuple[Any, Any]:
        mlx_whisper = _import_mlx_whisper()
        try:
            result = mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=self.settings.model,
                language=language,
                # MLX 0.4.3 raises for any non-None beam_size, including 1.
                # Omitting it selects the supported greedy decoder.
                fp16=self.settings.compute_type != "float32",
                word_timestamps=False,
            )
        finally:
            _release_mlx_weights()
        segments = [
            SimpleNamespace(
                id=int(item.get("id", index)),
                start=float(item["start"]),
                end=float(item["end"]),
                text=str(item.get("text", "")),
                no_speech_prob=item.get("no_speech_prob"),
                avg_logprob=item.get("avg_logprob"),
            )
            for index, item in enumerate(result.get("segments") or [])
        ]
        info = SimpleNamespace(language=str(result.get("language") or language or "unknown"))
        return iter(segments), info


def _load_model(settings: TranscriptionSettings) -> Any:
    if settings.provider == "mlx-whisper":
        return MlxWhisperEngine(settings)
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ModelUnavailableError(
            "faster-whisper is not installed; run `uv sync --extra stt`"
        ) from exc
    try:
        return WhisperModel(
            settings.model, device=settings.device, compute_type=settings.compute_type
        )
    except Exception as exc:
        raise ModelUnavailableError(
            f"could not load faster-whisper model {settings.model!r}: {exc}"
        ) from exc


def transcribe_audio(
    audio: Path,
    metadata: AudioMetadata,
    settings: TranscriptionSettings,
    model: Any | None = None,
) -> Transcript:
    engine = model or _load_model(settings)
    try:
        raw_segments, info = engine.transcribe(
            str(audio),
            language=settings.language,
            vad_filter=settings.vad_filter,
            beam_size=settings.beam_size,
        )
        segments = [
            TranscriptSegment(
                id=int(getattr(item, "id", index)),
                start=float(item.start),
                end=float(item.end),
                text=str(item.text).strip(),
                no_speech_prob=getattr(item, "no_speech_prob", None),
                avg_logprob=getattr(item, "avg_logprob", None),
            )
            for index, item in enumerate(raw_segments)
            if str(item.text).strip()
        ]
    except Exception as exc:
        if isinstance(exc, TranscriptionError):
            raise
        raise AudioDecodeError("audio decoding failed") from exc
    if not segments:
        raise NoSpeechError("transcription produced no speech segments")
    final_end = max(s.end for s in segments)
    duration = metadata.duration_seconds
    low = [
        EvidenceRange(start=s.start, end=s.end, segment_ids=[s.id])
        for s in segments
        if (s.no_speech_prob is not None and s.no_speech_prob >= 0.8)
        or (s.avg_logprob is not None and s.avg_logprob <= -1.0)
    ]
    quality_warnings, degraded_ranges = detect_degraded_ranges(segments, duration)
    return Transcript(
        source=metadata.filename,
        language=str(getattr(info, "language", settings.language or "unknown")),
        language_probability=getattr(info, "language_probability", None),
        duration_seconds=duration,
        coverage_ratio=min(1.0, final_end / duration) if duration else 0.0,
        segments=segments,
        low_confidence_ranges=low,
        quality_warnings=quality_warnings,
        degraded_ranges=degraded_ranges,
        model=settings.model,
    )


def write_transcript_files(meeting_dir: Path, transcript: Transcript) -> tuple[Path, Path]:
    meeting_dir = Path(meeting_dir)
    json_path = meeting_dir / "build" / "transcript.json"
    md_path = meeting_dir / "transcript.md"
    write_json_atomic(json_path, transcript.model_dump(mode="json"))
    lines = [
        "# Transcript",
        "",
        f"- Source: `{transcript.source}`",
        f"- Language: `{transcript.language}`",
        f"- Duration: `{format_timestamp(transcript.duration_seconds)}`",
        f"- Model: `{transcript.model or 'unknown'}`",
        "- Speaker identification unavailable; do not infer identities from sequence.",
        "",
    ]
    if transcript.quality_warnings:
        lines += ["## Transcript quality warnings", ""]
        for warning in transcript.quality_warnings:
            labels = "; ".join(item.label() for item in warning.evidence)
            lines.append(f"- {warning.note} {labels}")
        lines.append("")
    for segment in transcript.segments:
        lines.append(
            f"[{format_timestamp(segment.start)}–{format_timestamp(segment.end)}] {segment.text}"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
