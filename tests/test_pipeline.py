import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import yaml

from meeting_pipeline.ingest import ingest_meeting
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
