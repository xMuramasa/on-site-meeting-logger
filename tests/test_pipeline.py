import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import yaml

from meeting_pipeline.ingest import ingest_meeting
from meeting_pipeline.manifest import load_manifest
from meeting_pipeline.models import AudioMetadata, CanonicalActa
from meeting_pipeline.pipeline import run_stages
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
    assert stage.note == "RuntimeError: processing failed"
