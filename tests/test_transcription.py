from types import SimpleNamespace

from meeting_pipeline.config import load_config
from meeting_pipeline.models import AudioMetadata
from meeting_pipeline.transcription import transcribe_audio, write_transcript_files


class FakeModel:
    def transcribe(self, path, **kwargs):
        segments = [
            SimpleNamespace(
                id=0, start=0.0, end=2.0, text=" Hola", no_speech_prob=0.1, avg_logprob=-0.2
            ),
            SimpleNamespace(
                id=1, start=2.0, end=4.5, text=" equipo", no_speech_prob=0.9, avg_logprob=-1.3
            ),
        ]
        info = SimpleNamespace(language="es", language_probability=0.98, duration=5.0)
        return iter(segments), info


def metadata(path):
    return AudioMetadata(
        path=str(path),
        filename=path.name,
        size_bytes=4,
        sha256="a" * 64,
        codec_name="aac",
        sample_rate=48000,
        channels=1,
        duration_seconds=5.0,
    )


def test_transcribe_with_injected_model_and_write_outputs(tmp_path):
    audio = tmp_path / "meeting.m4a"
    audio.write_bytes(b"fake")
    settings = load_config(None).transcription
    transcript = transcribe_audio(audio, metadata(audio), settings, model=FakeModel())
    assert transcript.language == "es"
    assert transcript.coverage_ratio == 0.9
    assert [s.text for s in transcript.segments] == ["Hola", "equipo"]
    assert transcript.low_confidence_ranges

    json_path, md_path = write_transcript_files(tmp_path, transcript)
    assert json_path.is_file()
    text = md_path.read_text(encoding="utf-8")
    assert "[00:00:00–00:00:02] Hola" in text
    assert "Speaker identification unavailable" in text
