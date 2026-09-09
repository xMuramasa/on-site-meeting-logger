"""Typer CLI. One command per pipeline stage plus `process` for the usual weekly run.

Stage modules are imported inside the command bodies so `--help` stays fast and works
even when an optional backend (faster-whisper, Chromium) is not installed.
"""

from __future__ import annotations

import json
from datetime import date as Date
from pathlib import Path

import typer

from .errors import PipelineError

app = typer.Typer(
    name="meeting",
    help="Turn a meeting recording into a validated acta.",
    no_args_is_help=True,
    add_completion=False,
)

AudioOpt = typer.Option(..., "--audio", help="Source recording (.m4a/.wav/.mp3).")
PreviousOpt = typer.Option(None, "--previous-acta", help="Previous acta PDF (context only).")
DateOpt = typer.Option(None, "--date", help="Meeting date (YYYY-MM-DD). Defaults to today.")
OutRootOpt = typer.Option(
    None,
    "--output-root",
    help="Parent of the dated meeting directory. Defaults to the audio's parent.",
)
MeetingDirOpt = typer.Option(..., "--meeting-dir", help="Existing dated meeting directory.")
ConfigOpt = typer.Option(None, "--config", "-c", help="Pipeline config YAML.")
ForceOpt = typer.Option(
    False,
    "--force",
    help="Deprecated compatibility option; changed approved reviews regenerate derived artifacts.",
)


def _fail(exc: Exception) -> None:
    typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _report(paths: dict[str, Path] | dict[str, object]) -> None:
    for label, value in paths.items():
        typer.echo(f"  {label}: {value}")


@app.command()
def doctor(
    output_root: Path = typer.Option(
        Path.home() / "Recordings",
        "--output-root",
        help="Directory where meeting artifacts will be written.",
    ),
    config: Path | None = ConfigOpt,
) -> None:
    """Check local model, transcription, PDF, storage, and executable readiness."""
    from .config import load_config
    from .readiness import check_readiness

    try:
        report = check_readiness(load_config(config), output_root)
    except PipelineError as exc:
        _fail(exc)
        return
    typer.echo(json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False))
    if not report.ok:
        raise typer.Exit(code=1)


@app.command()
def inspect(
    audio: Path = AudioOpt,
    meeting_dir: Path | None = typer.Option(None, "--meeting-dir", help="Write metadata here."),
    config: Path | None = ConfigOpt,
) -> None:
    """Probe the recording with ffprobe and report codec, duration, and size."""
    from .audio import probe_audio
    from .config import load_config

    try:
        load_config(config)
        metadata = probe_audio(audio)
    except PipelineError as exc:
        _fail(exc)
        return
    if meeting_dir is not None:
        from .manifest import write_json_atomic

        target = meeting_dir / "build" / "audio-metadata.json"
        write_json_atomic(target, metadata.model_dump(mode="json"))
        typer.echo(f"wrote {target}")
    typer.echo(json.dumps(metadata.model_dump(mode="json"), indent=2, ensure_ascii=False))


@app.command()
def transcribe(
    meeting_dir: Path = MeetingDirOpt,
    config: Path | None = ConfigOpt,
    force: bool = ForceOpt,
) -> None:
    """Transcribe the ingested recording into build/transcript.json and transcript.md."""
    from .pipeline import run_stages

    try:
        result = run_stages(meeting_dir, until="chunk", config_path=config, force=force)
    except PipelineError as exc:
        _fail(exc)
        return
    _report(result.artifacts)


@app.command(name="extract-context")
def extract_context(
    meeting_dir: Path = MeetingDirOpt,
    config: Path | None = ConfigOpt,
    force: bool = ForceOpt,
) -> None:
    """Extract provenance-labelled context from the previous acta PDF."""
    from .pipeline import run_stages

    try:
        result = run_stages(
            meeting_dir, until="extract_previous_context", config_path=config, force=force
        )
    except PipelineError as exc:
        _fail(exc)
        return
    _report(result.artifacts)


@app.command()
def draft(
    meeting_dir: Path = MeetingDirOpt,
    config: Path | None = ConfigOpt,
    force: bool = ForceOpt,
) -> None:
    """Run chunk extraction + consolidation, then emit the draft acta and review.yaml."""
    from .pipeline import run_stages

    try:
        result = run_stages(meeting_dir, until="generate_review", config_path=config, force=force)
    except PipelineError as exc:
        _fail(exc)
        return
    _report(result.artifacts)


@app.command()
def finalize(
    meeting_dir: Path = MeetingDirOpt,
    config: Path | None = ConfigOpt,
    force: bool = ForceOpt,
) -> None:
    """Apply the approved review, then render Markdown, HTML, and a validated PDF."""
    from .pipeline import run_stages

    try:
        result = run_stages(meeting_dir, until="validate", config_path=config, force=force)
    except PipelineError as exc:
        _fail(exc)
        return
    _report(result.artifacts)


@app.command()
def validate(
    meeting_dir: Path = MeetingDirOpt,
    config: Path | None = ConfigOpt,
) -> None:
    """Re-run every output check and rewrite build/validation-report.json."""
    from .config import load_config
    from .validation import validate_meeting

    try:
        settings = load_config(config)
        report = validate_meeting(meeting_dir, settings)
    except PipelineError as exc:
        _fail(exc)
        return
    typer.echo(json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False))
    if not report.ok:
        raise typer.Exit(code=1)


@app.command()
def process(
    audio: Path = AudioOpt,
    previous_acta: Path | None = PreviousOpt,
    date: str | None = DateOpt,
    output_root: Path | None = OutRootOpt,
    config: Path | None = ConfigOpt,
    force: bool = ForceOpt,
) -> None:
    """Ingest and run everything up to the review gate (stops before final rendering)."""
    from .ingest import ingest_meeting
    from .pipeline import run_stages

    try:
        meeting_date = Date.fromisoformat(date) if date else Date.today()
        meeting_dir = ingest_meeting(
            audio=audio,
            previous_acta=previous_acta,
            meeting_date=meeting_date,
            output_root=output_root,
            force=force,
        )
        result = run_stages(meeting_dir, until="generate_review", config_path=config, force=force)
    except ValueError as exc:
        _fail(exc)
        return
    except PipelineError as exc:
        _fail(exc)
        return
    _report(result.artifacts)
    typer.echo("")
    typer.echo(f"Review and approve: {meeting_dir / 'review.yaml'}")
    typer.echo(f"Then run: meeting finalize --meeting-dir {meeting_dir}")


@app.command()
def serve(
    output_root: Path = typer.Option(
        Path.home() / "Recordings",
        "--output-root",
        help="Parent directory containing dated meeting directories.",
    ),
    config: Path | None = ConfigOpt,
    port: int = typer.Option(8765, "--port", min=1024, max=65535),
) -> None:
    """Run the microphone/upload and review UI on the local Mac only."""
    import uvicorn

    from .web import create_app

    typer.echo(f"Meeting Studio: http://127.0.0.1:{port}")
    uvicorn.run(
        create_app(
            output_root=output_root,
            config_path=config,
            allowed_origins={f"http://127.0.0.1:{port}", f"http://localhost:{port}"},
        ),
        host="127.0.0.1",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":  # pragma: no cover
    app()
