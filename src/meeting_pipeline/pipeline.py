"""Resumable stage orchestration for the weekly pipeline."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .audio import probe_audio
from .chunking import chunk_transcript
from .config import load_config
from .errors import (
    AudioDecodeError,
    AudioError,
    ModelUnavailableError,
    NoSpeechError,
    PipelineCancelled,
    PipelineError,
)
from .manifest import (
    cancel_stage,
    complete_stage,
    fail_stage,
    fingerprint,
    load_manifest,
    meeting_lock,
    sha256_file,
    stage_is_current,
    start_stage,
    write_json_atomic,
    write_manifest,
)
from .models import AudioMetadata, CanonicalActa, Transcript
from .pdf import export_pdf
from .previous_context import extract_previous_context
from .providers.openai_compatible import OpenAICompatibleProvider
from .reasoning import generate_acta_draft, write_draft_artifacts
from .rendering import artifact_stem, render_documents
from .review import apply_review, generate_review, load_review
from .transcription import transcribe_audio, write_transcript_files
from .validation import require_valid, validate_meeting

STAGES = [
    "inspect",
    "transcribe",
    "chunk",
    "extract_previous_context",
    "consolidate",
    "generate_review",
    "approve",
    "render",
    "export_pdf",
    "validate",
]


@dataclass
class PipelineResult:
    meeting_dir: Path
    completed_stage: str
    artifacts: dict[str, Path] = field(default_factory=dict)


@dataclass(frozen=True)
class FailureDiagnostic:
    code: str
    retryable: bool


def failure_diagnostic(exc: Exception) -> FailureDiagnostic:
    """Classify failures without preserving untrusted provider or meeting content."""
    if isinstance(exc, NoSpeechError):
        return FailureDiagnostic("NO_SPEECH", False)
    if isinstance(exc, ModelUnavailableError):
        return FailureDiagnostic("MODEL_UNAVAILABLE", True)
    if isinstance(exc, (AudioDecodeError, AudioError)):
        return FailureDiagnostic("AUDIO_DECODE_FAILED", False)
    if isinstance(exc, OSError):
        return FailureDiagnostic("STORAGE_UNAVAILABLE", True)
    return FailureDiagnostic("PROCESSING_FAILED", True)


def _read_model(path: Path, model_type: type[Any]) -> Any:
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def _all_artifacts(manifest: Any) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for stage_name, state in manifest.stages.items():
        if state.status == "complete":
            for label, value in state.artifacts.items():
                result[f"{stage_name}.{label}"] = Path(value)
    return result


def _run_stages(
    meeting_dir: Path,
    until: str = "validate",
    config_path: Path | None = None,
    force: bool = False,
    provider: Any | None = None,
    transcription_model: Any | None = None,
    audio_probe: Callable[[Path], AudioMetadata] = probe_audio,
    pdf_exporter: Callable[..., Path] = export_pdf,
) -> PipelineResult:
    meeting_dir = Path(meeting_dir).expanduser().resolve()
    if until not in STAGES:
        raise PipelineError(f"unknown pipeline stage {until!r}; expected one of {STAGES}")
    settings = load_config(config_path)
    manifest_path = meeting_dir / "manifest.json"
    manifest = load_manifest(manifest_path)
    manifest.config_fingerprint = settings.fingerprint()
    source = meeting_dir / "source" / manifest.source_filename
    if not source.is_file() or sha256_file(source) != manifest.source_sha256:
        raise PipelineError("ingested source is missing or its hash changed")
    build = meeting_dir / "build"
    build.mkdir(parents=True, exist_ok=True)

    target_index = STAGES.index(until)

    def reached(stage: str) -> bool:
        return STAGES.index(stage) > target_index

    def commit(stage: str, fp: str, artifacts: dict[str, Path]) -> None:
        if (build / ".cancel-requested").is_file():
            raise PipelineCancelled("cancellation requested")
        complete_stage(manifest, stage, fp, artifacts)
        write_manifest(manifest_path, manifest)

    def begin(stage: str) -> None:
        start_stage(manifest, stage)
        write_manifest(manifest_path, manifest)
        if (build / ".cancel-requested").is_file():
            raise PipelineCancelled("cancellation requested")

    # inspect
    stage = "inspect"
    if reached(stage):
        return PipelineResult(meeting_dir, until, _all_artifacts(manifest))
    metadata_path = build / "audio-metadata.json"
    inspect_fp = fingerprint("inspect-v1", manifest.source_sha256)
    if not stage_is_current(manifest, stage, inspect_fp):
        begin(stage)
        try:
            metadata = audio_probe(source)
            write_json_atomic(metadata_path, metadata.model_dump(mode="json"))
            commit(stage, inspect_fp, {"metadata": metadata_path})
        except Exception as exc:
            _fail_with_diagnostic(manifest, stage, exc)
            write_manifest(manifest_path, manifest)
            raise
    metadata = _read_model(metadata_path, AudioMetadata)
    if until == stage:
        return PipelineResult(meeting_dir, stage, _all_artifacts(manifest))

    # transcribe
    stage = "transcribe"
    transcript_path = build / "transcript.json"
    transcribe_fp = fingerprint(
        "transcribe-v1", manifest.source_sha256, settings.transcription.model_dump(mode="json")
    )
    if not stage_is_current(manifest, stage, transcribe_fp):
        begin(stage)
        transcript = transcribe_audio(
            source, metadata, settings.transcription, model=transcription_model
        )
        json_path, md_path = write_transcript_files(meeting_dir, transcript)
        commit(stage, transcribe_fp, {"json": json_path, "markdown": md_path})
    transcript = _read_model(transcript_path, Transcript)
    if until == stage:
        return PipelineResult(meeting_dir, stage, _all_artifacts(manifest))

    # chunk
    stage = "chunk"
    chunks_path = build / "transcript-chunks.json"
    chunk_fp = fingerprint(
        "chunk-v1", sha256_file(transcript_path), settings.chunking.model_dump(mode="json")
    )
    if not stage_is_current(manifest, stage, chunk_fp):
        begin(stage)
        chunks = chunk_transcript(
            transcript,
            target_tokens=settings.chunking.target_tokens,
            chars_per_token=settings.chunking.chars_per_token,
            overlap_seconds=settings.chunking.overlap_seconds,
        )
        write_json_atomic(chunks_path, chunks)
        commit(stage, chunk_fp, {"chunks": chunks_path})
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    if until == stage:
        return PipelineResult(meeting_dir, stage, _all_artifacts(manifest))

    # previous context (a sentinel is still an artifact when no PDF was supplied)
    stage = "extract_previous_context"
    context_path = build / "previous-context.json"
    context_fp = fingerprint(
        "previous-context-v1",
        manifest.previous_acta_sha256,
        settings.glossary.model_dump(mode="json"),
        [item.model_dump(mode="json") for item in settings.reference_participants],
    )
    if not stage_is_current(manifest, stage, context_fp):
        begin(stage)
        prior_path = meeting_dir / "source" / "previous-acta.pdf"
        if manifest.previous_acta_sha256:
            context = extract_previous_context(prior_path, settings)
        else:
            context = {
                "source": None,
                "sha256": None,
                "page_count": 0,
                "text": "No se proporcionó acta anterior.",
                "participants": [],
                "canonical_terms_found": [],
                "provenance": "none",
                "warning": None,
            }
        write_json_atomic(context_path, context)
        commit(stage, context_fp, {"context": context_path})
    context = json.loads(context_path.read_text(encoding="utf-8"))
    if until == stage:
        return PipelineResult(meeting_dir, stage, _all_artifacts(manifest))

    # reasoning + consolidation
    stage = "consolidate"
    draft_path = build / "acta-draft.json"
    prompt_hashes = [
        sha256_file(Path(__file__).resolve().parents[2] / "prompts" / name)
        for name in ("extract-chunk.md", "consolidate-acta.md")
    ]
    consolidate_fp = fingerprint(
        "consolidate-v1",
        sha256_file(chunks_path),
        sha256_file(context_path),
        settings.reasoning.model_dump(mode="json", exclude={"base_url", "api_key_env"}),
        prompt_hashes,
    )
    if not stage_is_current(manifest, stage, consolidate_fp):
        begin(stage)
        active_provider = provider or OpenAICompatibleProvider(settings.reasoning)
        fixed_meeting = {
            "date": manifest.meeting_date.isoformat(),
            "recording_filename": manifest.source_filename,
            "recording_sha256": manifest.source_sha256,
            "previous_acta_filename": manifest.previous_acta_filename,
            "previous_acta_sha256": manifest.previous_acta_sha256,
            "previous_acta_date": None,
        }
        acta, extractions = generate_acta_draft(
            transcript,
            chunks,
            context,
            settings,
            active_provider,
            fixed_meeting=fixed_meeting,
        )
        usage = getattr(active_provider, "last_usage", None)
        usage_data = usage.model_dump(mode="json") if usage is not None else {}
        artifacts = write_draft_artifacts(meeting_dir, acta, extractions, usage_data)
        commit(stage, consolidate_fp, artifacts)
    acta = _read_model(draft_path, CanonicalActa)
    if until == stage:
        return PipelineResult(meeting_dir, stage, _all_artifacts(manifest))

    # review sheet
    stage = "generate_review"
    review_path = meeting_dir / "review.yaml"
    review_fp = fingerprint("review-v1", sha256_file(draft_path))
    if not stage_is_current(manifest, stage, review_fp):
        begin(stage)
        generate_review(acta, review_path)
        commit(stage, review_fp, {"review": review_path})
    if until == stage:
        return PipelineResult(meeting_dir, stage, _all_artifacts(manifest))

    # explicit approval
    stage = "approve"
    approved_path = build / "acta-approved.json"
    approve_fp = fingerprint("approve-v1", sha256_file(draft_path), sha256_file(review_path))
    if not stage_is_current(manifest, stage, approve_fp):
        begin(stage)
        approved = apply_review(acta, load_review(review_path))
        write_json_atomic(approved_path, approved.model_dump(mode="json"))
        commit(stage, approve_fp, {"approved": approved_path})
    approved = _read_model(approved_path, CanonicalActa)
    if until == stage:
        return PipelineResult(meeting_dir, stage, _all_artifacts(manifest))

    # document rendering
    stage = "render"
    template_dir = Path(__file__).resolve().parents[2] / "templates"
    render_fp = fingerprint(
        "render-v2",
        sha256_file(approved_path),
        settings.branding.model_dump(mode="json"),
        *[
            sha256_file(template_dir / name)
            for name in ("acta.md.j2", "acta.html.j2", "digest.md.j2")
        ],
    )
    if not stage_is_current(manifest, stage, render_fp):
        begin(stage)
        rendered = render_documents(approved, meeting_dir, settings.branding)
        commit(stage, render_fp, rendered)
    if until == stage:
        return PipelineResult(meeting_dir, stage, _all_artifacts(manifest))

    # PDF
    stage = "export_pdf"
    document_stem = artifact_stem(approved, settings.branding)
    html_path = meeting_dir / f"{document_stem}.html"
    pdf_path = meeting_dir / f"{document_stem}.pdf"
    pdf_fp = fingerprint(
        "pdf-v2",
        document_stem,
        sha256_file(html_path),
        settings.pdf.model_dump(mode="json"),
    )
    if not stage_is_current(manifest, stage, pdf_fp):
        begin(stage)
        pdf_exporter(html_path, pdf_path, settings.pdf)
        commit(stage, pdf_fp, {"pdf": pdf_path})
    if until == stage:
        return PipelineResult(meeting_dir, stage, _all_artifacts(manifest))

    # quality gates
    stage = "validate"
    report_path = build / "validation-report.json"
    validate_fp = fingerprint(
        "validate-v1",
        sha256_file(pdf_path),
        sha256_file(meeting_dir / f"{document_stem}.md"),
        sha256_file(html_path),
        settings.validation.model_dump(mode="json"),
    )
    if not stage_is_current(manifest, stage, validate_fp):
        begin(stage)
        report = validate_meeting(meeting_dir, settings)
        require_valid(report)
        commit(stage, validate_fp, {"report": report_path})
    return PipelineResult(meeting_dir, stage, _all_artifacts(manifest))


def _fail_with_diagnostic(manifest: Any, stage: str, exc: Exception) -> None:
    """Persist only a stable safe code, never exception text or chained payloads."""
    diagnostic = failure_diagnostic(exc)
    fail_stage(
        manifest,
        stage,
        diagnostic.code,
        error_code=diagnostic.code,
        retryable=diagnostic.retryable,
    )


def request_cancellation(meeting_dir: Path) -> None:
    """Persist a cooperative cancellation request without contending for the active run lock."""
    meeting_dir = Path(meeting_dir).expanduser().resolve()
    write_json_atomic(meeting_dir / "build" / ".cancel-requested", {"requested": True})


def clear_cancellation(meeting_dir: Path) -> None:
    (Path(meeting_dir).expanduser().resolve() / "build" / ".cancel-requested").unlink(
        missing_ok=True
    )


def run_stages(
    meeting_dir: Path,
    until: str = "validate",
    config_path: Path | None = None,
    force: bool = False,
    provider: Any | None = None,
    transcription_model: Any | None = None,
    audio_probe: Callable[[Path], AudioMetadata] = probe_audio,
    pdf_exporter: Callable[..., Path] = export_pdf,
) -> PipelineResult:
    """Run one meeting exclusively and leave every attempted stage durable and resumable."""
    meeting_dir = Path(meeting_dir).expanduser().resolve()
    manifest_path = meeting_dir / "manifest.json"
    with meeting_lock(meeting_dir):
        try:
            return _run_stages(
                meeting_dir,
                until=until,
                config_path=config_path,
                force=force,
                provider=provider,
                transcription_model=transcription_model,
                audio_probe=audio_probe,
                pdf_exporter=pdf_exporter,
            )
        except Exception as exc:
            if manifest_path.is_file():
                manifest = load_manifest(manifest_path)
                running = next(
                    (
                        name
                        for name, state in manifest.stages.items()
                        if state.status == "running"
                    ),
                    None,
                )
                if running:
                    if isinstance(exc, PipelineCancelled):
                        cancel_stage(manifest, running, "Cancellation requested")
                    else:
                        _fail_with_diagnostic(manifest, running, exc)
                    write_manifest(manifest_path, manifest)
            raise
