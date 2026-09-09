import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from meeting_pipeline.evaluation import evaluate_reviewed_meeting
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
    def __init__(self, generated: CanonicalActa):
        self.generated = generated
        self.last_usage = SimpleNamespace(
            model_dump=lambda mode=None: {"model": "fake", "repaired": False}
        )

    def generate_typed(self, messages, model_type):
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
        return self.generated


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


def _reviewed_meeting(tmp_path: Path) -> tuple[Path, CanonicalActa]:
    audio = tmp_path / "input.m4a"
    audio.write_bytes(b"fake audio")
    meeting_dir = ingest_meeting(audio, None, date(2026, 8, 31), tmp_path / "out")
    approved = CanonicalActa.model_validate_json(FIXTURE.read_text())
    provider = FakeProvider(approved)
    run_stages(
        meeting_dir,
        until="generate_review",
        provider=provider,
        transcription_model=FakeWhisper(),
        audio_probe=fake_probe,
    )
    approved = CanonicalActa.model_validate_json(
        (meeting_dir / "build" / "acta-draft.json").read_text(encoding="utf-8")
    )
    (meeting_dir / "build" / "acta-approved.json").write_text(
        approved.model_dump_json(indent=2), encoding="utf-8"
    )
    return meeting_dir, approved


def test_evaluate_reviewed_meeting_replays_model_and_writes_aggregate_only_report(tmp_path):
    meeting_dir, approved = _reviewed_meeting(tmp_path)

    report_path = evaluate_reviewed_meeting(meeting_dir, provider=FakeProvider(approved))

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report_path == meeting_dir / "build" / "evaluation-report.json"
    assert report["citation_precision"] == pytest.approx(1.0)
    assert report["unsupported_claims"] == 0
    assert report["decision_as_proposal_errors"] == 0
    assert report["proposal_as_decision_errors"] == 0
    assert report["schema_repairs"] == 0
    assert report["human_corrections"] == 0
    assert report["runtime_seconds"] >= 0
    assert report["peak_memory_mib"] > 0
    assert "Se acordó enviar material" not in report_path.read_text(encoding="utf-8")
    assert "acta" not in report


def test_evaluate_reviewed_meeting_counts_unsupported_and_type_errors(tmp_path):
    meeting_dir, approved = _reviewed_meeting(tmp_path)
    generated = approved.model_copy(deep=True)
    generated.decisions = generated.decisions[:1]
    generated.decisions[0].statement = approved.proposals[0].statement
    generated.proposals = []

    report = json.loads(
        evaluate_reviewed_meeting(meeting_dir, provider=FakeProvider(generated)).read_text(encoding="utf-8")
    )

    assert report["unsupported_claims"] == 0
    assert report["decision_as_proposal_errors"] == 1


def test_evaluate_reviewed_meeting_counts_schema_repairs_per_model_call(tmp_path):
    meeting_dir, approved = _reviewed_meeting(tmp_path)
    provider = FakeProvider(approved)
    provider.last_usage = SimpleNamespace(
        model_dump=lambda mode=None: {"model": "fake", "repaired": True}, repaired=True
    )

    report = json.loads(evaluate_reviewed_meeting(meeting_dir, provider=provider).read_text(encoding="utf-8"))

    assert report["model_calls"] == 2
    assert report["schema_repairs"] == 2


def test_evaluate_reviewed_meeting_requires_an_approved_reference(tmp_path):
    meeting_dir, _ = _reviewed_meeting(tmp_path)
    (meeting_dir / "build" / "acta-approved.json").unlink()

    with pytest.raises(FileNotFoundError, match="approved acta"):
        evaluate_reviewed_meeting(meeting_dir, provider=FakeProvider(CanonicalActa.model_validate_json(FIXTURE.read_text())))
