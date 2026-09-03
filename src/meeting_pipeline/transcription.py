"""Local faster-whisper transcription with dependency injection for tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import TranscriptionSettings
from .errors import TranscriptionError
from .manifest import write_json_atomic
from .models import AudioMetadata, EvidenceRange, Transcript, TranscriptSegment, format_timestamp


def _load_model(settings: TranscriptionSettings) -> Any:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionError(
            "faster-whisper is not installed; run `uv sync --extra stt`"
        ) from exc
    try:
        return WhisperModel(
            settings.model, device=settings.device, compute_type=settings.compute_type
        )
    except Exception as exc:
        raise TranscriptionError(
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
        raise TranscriptionError(f"transcription failed: {exc}") from exc
    if not segments:
        raise TranscriptionError("transcription produced no speech segments")
    final_end = max(s.end for s in segments)
    duration = metadata.duration_seconds
    low = [
        EvidenceRange(start=s.start, end=s.end, segment_ids=[s.id])
        for s in segments
        if (s.no_speech_prob is not None and s.no_speech_prob >= 0.8)
        or (s.avg_logprob is not None and s.avg_logprob <= -1.0)
    ]
    return Transcript(
        source=metadata.filename,
        language=str(getattr(info, "language", settings.language or "unknown")),
        language_probability=getattr(info, "language_probability", None),
        duration_seconds=duration,
        coverage_ratio=min(1.0, final_end / duration) if duration else 0.0,
        segments=segments,
        low_confidence_ranges=low,
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
    for segment in transcript.segments:
        lines.append(
            f"[{format_timestamp(segment.start)}–{format_timestamp(segment.end)}] {segment.text}"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
