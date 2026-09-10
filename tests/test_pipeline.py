import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from meeting_pipeline.errors import AudioDecodeError, ModelUnavailableError, NoSpeechError
from meeting_pipeline.ingest import ingest_meeting
from meeting_pipeline.manifest import complete_stage, load_manifest, write_manifest
from meeting_pipeline.models import AudioLevelAnalysis, AudioMetadata, CanonicalActa
from meeting_pipeline.pipeline import failure_diagnostic, run_stages
from meeting_pipeline.reasoning import ChunkExtraction

FIXTURE = Path(__file__).parent / "fixtures" / "valid-acta.json"


class FakeWhisper:
    def transcribe(self, path, **kwargs):
        return iter(
            [
                SimpleNamespace(
                    id=0,
                    start=0.0,
                    end=5.0,
                    text="Se acordó enviar material",
                    no_speech_prob=0.1,
                    avg_logprob=-0.1,
                )
            ]
        ), SimpleNamespace(language="es", language_probability=0.99)


class FakeProvider:
    def __init__(self):
        self.calls = 0
        self.last_usage = SimpleNamespace(model_dump=lambda mode=None: {"model": "fake"})

    def generate_typed(self, messages, model_type):
        self.calls += 1
        if model_type is ChunkExtraction:
            return ChunkExtraction(
                chunk_id=0,
                facts=[
                    {
                        "kind": "decision",
                        "text": "Enviar material",
                        "section": "Comercial / B2B",
                        "evidence": [{"start": 0.0, "end": 5.0, "segment_ids": [0]}],
                    }
                ],
            )
        data = json.loads(FIXTURE.read_text())
        data["meeting"]["recording_filename"] = "meeting.m4a"
        return CanonicalActa.model_validate(data)


def fake_probe(path):
    return AudioMetadata(
        path=str(path),
        filename=path.name,
        size_bytes=path.stat().st_size,
        sha256="a" * 64,
        codec_name="aac",
        sample_rate=48000,
        channels=1,
        duration_seconds=3132.309333,
    )


def silent_analysis(_path):
    return AudioLevelAnalysis(
        scanned_seconds=120,
        mean_db=-91.0,
        max_db=-91.0,
        classification="silent",
    )


def short_probe(path):
    metadata = fake_probe(path)
    metadata.duration_seconds = 60.0
    return metadata


def test_pipeline_rejects_silence_before_transcription_and_keeps_source(tmp_path):
    audio = tmp_path / "input.m4a"
    audio.write_bytes(b"original recording")
    meeting_dir = ingest_meeting(audio, None, date(2026, 8, 31), tmp_path / "out")

    with pytest.raises(NoSpeechError, match="No se detectó audio"):
        run_stages(
            meeting_dir,
            until="transcribe",
            transcription_model=FakeWhisper(),
            audio_probe=short_probe,
            audio_level_inspector=silent_analysis,
        )

    assert (meeting_dir / "source" / "meeting.m4a").read_bytes() == b"original recording"
    manifest = load_manifest(meeting_dir / "manifest.json")
    assert manifest.stages["inspect"].status == "failed"


def test_pipeline_does_not_reject_long_recording_from_silent_opening_sample(tmp_path):
    audio = tmp_path / "input.m4a"
    audio.write_bytes(b"original recording")
    meeting_dir = ingest_meeting(audio, None, date(2026, 8, 31), tmp_path / "out")

    run_stages(
        meeting_dir,
        until="transcribe",
        transcription_model=FakeWhisper(),
        audio_probe=fake_probe,
        audio_level_inspector=silent_analysis,
    )

    assert (meeting_dir / "build" / "transcript.json").is_file()
    manifest = load_manifest(meeting_dir / "manifest.json")
    assert manifest.stages["inspect"].status == "complete"
    assert manifest.stages["transcribe"].status == "complete"


def test_failed_upstream_rerun_invalidates_completed_downstream_outputs(tmp_path):
    audio = tmp_path / "input.m4a"
    audio.write_bytes(b"original recording")
    meeting_dir = ingest_meeting(audio, None, date(2026, 8, 31), tmp_path / "out")
    manifest_path = meeting_dir / "manifest.json"
    manifest = load_manifest(manifest_path)
    old_output = meeting_dir / "old-minutes.pdf"
    old_output.write_bytes(b"old")
    complete_stage(manifest, "inspect", "stale", {"metadata": old_output})
    complete_stage(manifest, "render", "old-render", {"markdown": old_output})
    complete_stage(manifest, "export_pdf", "old-pdf", {"pdf": old_output})
    complete_stage(manifest, "validate", "old-validation", {"report": old_output})
    write_manifest(manifest_path, manifest)

    def broken_probe(_path):
        raise RuntimeError("inspection failed")

    with pytest.raises(RuntimeError, match="inspection failed"):
        run_stages(meeting_dir, until="inspect", audio_probe=broken_probe)

    stages = load_manifest(manifest_path).stages
    assert stages["inspect"].status == "failed"
    assert stages["render"].status == "pending"
    assert stages["export_pdf"].status == "pending"
    assert stages["validate"].status == "pending"


def test_pipeline_runs_to_review_then_approved_render(tmp_path):
    audio = tmp_path / "input.m4a"
    audio.write_bytes(b"fake audio")
    meeting_dir = ingest_meeting(audio, None, date(2026, 8, 31), tmp_path / "out")
    provider = FakeProvider()
    result = run_stages(
        meeting_dir,
        until="generate_review",
        provider=provider,
        transcription_model=FakeWhisper(),
        audio_probe=fake_probe,
    )
    assert (meeting_dir / "build" / "transcript.json").is_file()
    assert (meeting_dir / "build" / "acta-draft.json").is_file()
    review_path = meeting_dir / "review.yaml"
    assert review_path.is_file()
    assert result.completed_stage == "generate_review"

    review = yaml.safe_load(review_path.read_text())
    review["approve_for_final_render"] = True
    review_path.write_text(yaml.safe_dump(review, allow_unicode=True), encoding="utf-8")
    rendered = run_stages(
        meeting_dir,
        until="render",
        provider=provider,
        transcription_model=FakeWhisper(),
        audio_probe=fake_probe,
    )
    assert rendered.completed_stage == "render"
    assert (meeting_dir / "Acta_Reunion_Semanal_2026-08-31.html").is_file()
    assert (meeting_dir / "build" / "acta-approved.json").is_file()


def test_pipeline_regenerates_derived_files_after_approved_review_edit(tmp_path):
    audio = tmp_path / "input.m4a"
    audio.write_bytes(b"fake audio")
    meeting_dir = ingest_meeting(audio, None, date(2026, 8, 31), tmp_path / "out")
    provider = FakeProvider()
    pdf_calls = []

    def fake_pdf_exporter(html_path, pdf_path, settings):
        pdf_calls.append(html_path.read_text(encoding="utf-8"))
        pdf_path.write_bytes(b"%PDF-fake")
        return pdf_path

    kwargs = dict(
        provider=provider,
        transcription_model=FakeWhisper(),
        audio_probe=fake_probe,
        pdf_exporter=fake_pdf_exporter,
    )
    run_stages(meeting_dir, until="generate_review", **kwargs)
    review_path = meeting_dir / "review.yaml"
    review = yaml.safe_load(review_path.read_text(encoding="utf-8"))
    review["approve_for_final_render"] = True
    review_path.write_text(yaml.safe_dump(review, allow_unicode=True), encoding="utf-8")
    run_stages(meeting_dir, until="export_pdf", **kwargs)

    source_path = meeting_dir / "source" / "meeting.m4a"
    source_before = source_path.read_bytes()
    markdown_path = meeting_dir / "Acta_Reunion_Semanal_2026-08-31.md"
    html_path = meeting_dir / "Acta_Reunion_Semanal_2026-08-31.html"
    markdown_before = markdown_path.read_text(encoding="utf-8")
    html_before = html_path.read_text(encoding="utf-8")
    first_manifest = load_manifest(meeting_dir / "manifest.json")

    review["proper_nouns"] = {"Obvio": "Obvio Health"}
    review_path.write_text(yaml.safe_dump(review, allow_unicode=True), encoding="utf-8")
    run_stages(meeting_dir, until="export_pdf", **kwargs)

    assert source_path.read_bytes() == source_before
    assert "Obvio Health" in markdown_path.read_text(encoding="utf-8")
    assert markdown_path.read_text(encoding="utf-8") != markdown_before
    assert "Obvio Health" in html_path.read_text(encoding="utf-8")
    assert html_path.read_text(encoding="utf-8") != html_before
    assert len(pdf_calls) == 2
    second_manifest = load_manifest(meeting_dir / "manifest.json")
    for stage in ("approve", "render", "export_pdf"):
        assert second_manifest.stages[stage].fingerprint != first_manifest.stages[stage].fingerprint


def test_pipeline_rerenders_documents_when_branding_changes(tmp_path):
    audio = tmp_path / "input.m4a"
    audio.write_bytes(b"fake audio")
    meeting_dir = ingest_meeting(audio, None, date(2026, 8, 31), tmp_path / "out")
    pdf_calls = []

    def fake_pdf_exporter(html_path, pdf_path, settings):
        pdf_calls.append((html_path, pdf_path, settings))
        pdf_path.write_bytes(b"%PDF-fake")
        return pdf_path

    kwargs = dict(
        provider=FakeProvider(),
        transcription_model=FakeWhisper(),
        audio_probe=fake_probe,
        pdf_exporter=fake_pdf_exporter,
    )
    run_stages(meeting_dir, until="generate_review", **kwargs)
    review_path = meeting_dir / "review.yaml"
    review = yaml.safe_load(review_path.read_text(encoding="utf-8"))
    review["approve_for_final_render"] = True
    review_path.write_text(yaml.safe_dump(review, allow_unicode=True), encoding="utf-8")
    run_stages(meeting_dir, until="export_pdf", **kwargs)

    branding = tmp_path / "branded.yaml"
    branding.write_text(
        yaml.safe_dump({"branding": {"filename_prefix": "Branded_Acta"}}),
        encoding="utf-8",
    )
    run_stages(meeting_dir, until="export_pdf", config_path=branding, **kwargs)

    assert (meeting_dir / "Branded_Acta_2026-08-31.md").is_file()
    assert (meeting_dir / "Branded_Acta_2026-08-31.html").is_file()
    assert (meeting_dir / "Branded_Acta_2026-08-31.pdf").is_file()
    assert len(pdf_calls) == 2


def test_pipeline_resumes_completed_reasoning_stages(tmp_path):
    audio = tmp_path / "input.m4a"
    audio.write_bytes(b"fake audio")
    meeting_dir = ingest_meeting(audio, None, date(2026, 8, 31), tmp_path / "out")
    provider = FakeProvider()
    kwargs = dict(provider=provider, transcription_model=FakeWhisper(), audio_probe=fake_probe)
    run_stages(meeting_dir, until="generate_review", **kwargs)
    first_calls = provider.calls
    run_stages(meeting_dir, until="generate_review", **kwargs)
    assert provider.calls == first_calls


def test_pipeline_persists_sanitized_failure_for_every_stage(tmp_path):
    audio = tmp_path / "input.m4a"
    audio.write_bytes(b"fake audio")
    meeting_dir = ingest_meeting(audio, None, date(2026, 8, 31), tmp_path / "out")

    def broken_probe(_path):
        raise RuntimeError("recording text: private meeting contents")

    try:
        run_stages(meeting_dir, until="inspect", audio_probe=broken_probe)
    except RuntimeError:
        pass
    else:  # pragma: no cover - assertion is more useful than pytest.raises here
        raise AssertionError("the injected stage failure must propagate")

    stage = load_manifest(meeting_dir / "manifest.json").stages["inspect"]
    assert stage.status == "failed"
    assert stage.started_at is not None
    assert stage.failed_at is not None
    assert stage.note == "PROCESSING_FAILED"
    assert stage.error_code == "PROCESSING_FAILED"
    assert stage.retryable is True


def test_failure_diagnostics_are_stable_and_safe():
    assert failure_diagnostic(NoSpeechError("private transcript")).code == "NO_SPEECH"
    assert failure_diagnostic(NoSpeechError("private transcript")).retryable is False
    assert failure_diagnostic(ModelUnavailableError("provider payload")).code == "MODEL_UNAVAILABLE"
    assert failure_diagnostic(AudioDecodeError("decoder payload")).code == "AUDIO_DECODE_FAILED"
    assert failure_diagnostic(OSError("filesystem path")).code == "STORAGE_UNAVAILABLE"


def test_pipeline_persists_safe_decode_diagnostic_for_lazy_whisper_iterator(tmp_path):
    class LazyBrokenWhisper:
        def transcribe(self, _path, **_kwargs):
            def segments():
                raise RuntimeError("decoder payload contains private meeting text")
                yield None  # pragma: no cover - makes this a generator

            return segments(), SimpleNamespace(language="es")

    audio = tmp_path / "input.m4a"
    audio.write_bytes(b"fake audio")
    meeting_dir = ingest_meeting(audio, None, date(2026, 8, 31), tmp_path / "out")

    try:
        run_stages(
            meeting_dir,
            until="transcribe",
            transcription_model=LazyBrokenWhisper(),
            audio_probe=fake_probe,
        )
    except Exception:
        pass
    else:  # pragma: no cover - assertion is more useful than pytest.raises here
        raise AssertionError("the lazy decoder failure must propagate")

    stage = load_manifest(meeting_dir / "manifest.json").stages["transcribe"]
    assert stage.status == "failed"
    assert stage.error_code == "AUDIO_DECODE_FAILED"
    assert stage.retryable is False
    assert "private meeting text" not in stage.note
