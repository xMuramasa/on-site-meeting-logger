"""MLX Whisper adapter: same Transcript contract as faster-whisper, no invented metadata."""

import sys
import types

import pytest
import yaml

from meeting_pipeline.config import load_config
from meeting_pipeline.errors import AudioDecodeError, ConfigError, ModelUnavailableError
from meeting_pipeline.models import AudioMetadata
from meeting_pipeline.transcription import MlxWhisperEngine, _load_model, transcribe_audio

# Shape taken from mlx_whisper/transcribe.py upstream: a dict with "text", "segments", "language".
MLX_RESULT = {
    "text": " Hola equipo",
    "language": "es",
    "segments": [
        {
            "id": 0,
            "seek": 0,
            "start": 0.0,
            "end": 2.0,
            "text": " Hola",
            "tokens": [1, 2],
            "temperature": 0.0,
            "avg_logprob": -0.2,
            "compression_ratio": 1.1,
            "no_speech_prob": 0.05,
        },
        {
            "id": 1,
            "seek": 200,
            "start": 2.0,
            "end": 4.5,
            "text": " equipo",
            "tokens": [3, 4],
            "temperature": 0.4,
            "avg_logprob": -1.3,
            "compression_ratio": 1.2,
            "no_speech_prob": 0.9,
        },
    ],
}


class FakeModelHolder:
    model = "loaded-weights"
    model_path = "mlx-community/whisper-large-v3-turbo"


def install_fake_mlx(monkeypatch, result=MLX_RESULT, on_call=None):
    """Stand in for the optional macOS-only dependency without installing it."""
    calls: list[dict] = []
    module = types.ModuleType("mlx_whisper")
    transcribe_module = types.ModuleType("mlx_whisper.transcribe")
    FakeModelHolder.model = "loaded-weights"
    FakeModelHolder.model_path = "mlx-community/whisper-large-v3-turbo"
    transcribe_module.ModelHolder = FakeModelHolder

    def fake_transcribe(audio, **kwargs):
        calls.append({"audio": audio, **kwargs})
        if on_call is not None:
            on_call(kwargs)
        return result

    module.transcribe = fake_transcribe
    module.__path__ = []  # marks it as a package so the submodule import resolves
    monkeypatch.setitem(sys.modules, "mlx_whisper", module)
    monkeypatch.setitem(sys.modules, "mlx_whisper.transcribe", transcribe_module)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    return calls


def mlx_settings(**overrides):
    base = {
        "provider": "mlx-whisper",
        "model": "mlx-community/whisper-large-v3-turbo",
        "vad_filter": False,
        "beam_size": 1,
        **overrides,
    }
    return load_config(None).transcription.model_copy(update=base)


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


def test_config_rejects_vad_filter_for_a_provider_that_has_no_vad(tmp_path):
    overlay = tmp_path / "mlx.yaml"
    overlay.write_text(
        yaml.safe_dump(
            {"transcription": {"provider": "mlx-whisper", "model": "m", "vad_filter": True}}
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="vad_filter"):
        load_config(overlay)


def test_config_rejects_a_device_mlx_cannot_honour(tmp_path):
    overlay = tmp_path / "mlx.yaml"
    overlay.write_text(
        yaml.safe_dump(
            {
                "transcription": {
                    "provider": "mlx-whisper",
                    "model": "m",
                    "vad_filter": False,
                    "device": "cuda",
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="device"):
        load_config(overlay)


def test_loader_dispatches_on_the_configured_provider(monkeypatch):
    install_fake_mlx(monkeypatch)

    assert isinstance(_load_model(mlx_settings()), MlxWhisperEngine)


def test_mlx_does_not_pass_unimplemented_beam_search(tmp_path, monkeypatch):
    def reject_beam_search(kwargs):
        if kwargs.get("beam_size") is not None:
            raise NotImplementedError("Beam search decoder is not yet implemented")

    calls = install_fake_mlx(monkeypatch, on_call=reject_beam_search)
    audio = tmp_path / "fixture.wav"
    transcribe_audio(audio, metadata(audio), mlx_settings())
    assert calls[0].get("beam_size") is None


def test_mlx_config_rejects_unsupported_beam_search(tmp_path):
    overlay = tmp_path / "mlx.yaml"
    overlay.write_text(yaml.safe_dump({"transcription": {
        "provider": "mlx-whisper", "vad_filter": False, "beam_size": 5,
    }}))
    with pytest.raises(ConfigError, match="beam_size"):
        load_config(overlay)


def test_non_apple_silicon_is_refused_with_an_actionable_message(monkeypatch):
    install_fake_mlx(monkeypatch)
    monkeypatch.setattr("platform.machine", lambda: "x86_64")

    with pytest.raises(ModelUnavailableError, match="Apple Silicon"):
        _load_model(mlx_settings())


def test_missing_dependency_names_the_extra_to_install(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    monkeypatch.setitem(sys.modules, "mlx_whisper", None)

    with pytest.raises(ModelUnavailableError, match="stt-mlx"):
        _load_model(mlx_settings())


def test_transcript_keeps_timestamps_and_uncertainty_without_inventing_language_probability(
    tmp_path, monkeypatch
):
    calls = install_fake_mlx(monkeypatch)
    audio = tmp_path / "meeting.m4a"
    audio.write_bytes(b"fake")

    transcript = transcribe_audio(
        audio, metadata(audio), mlx_settings(), model=_load_model(mlx_settings())
    )

    assert [(s.start, s.end, s.text) for s in transcript.segments] == [
        (0.0, 2.0, "Hola"),
        (2.0, 4.5, "equipo"),
    ]
    assert [s.no_speech_prob for s in transcript.segments] == [0.05, 0.9]
    assert [s.avg_logprob for s in transcript.segments] == [-0.2, -1.3]
    assert transcript.language == "es"
    assert transcript.language_probability is None, "mlx-whisper does not report one"
    assert transcript.model == "mlx-community/whisper-large-v3-turbo"
    # The degraded second segment is still flagged by the shared safeguards.
    assert [(r.start, r.end) for r in transcript.low_confidence_ranges] == [(2.0, 4.5)]
    assert calls[0]["path_or_hf_repo"] == "mlx-community/whisper-large-v3-turbo"
    assert calls[0]["language"] == "es"
    assert calls[0]["word_timestamps"] is False


def test_compute_type_selects_fp16_and_never_leaks_faster_whisper_only_options(
    tmp_path, monkeypatch
):
    calls = install_fake_mlx(monkeypatch)
    audio = tmp_path / "meeting.m4a"
    audio.write_bytes(b"fake")

    transcribe_audio(
        audio,
        metadata(audio),
        mlx_settings(compute_type="float32"),
        model=_load_model(mlx_settings(compute_type="float32")),
    )

    assert calls[0]["fp16"] is False
    assert "vad_filter" not in calls[0]
    assert "compute_type" not in calls[0]
    assert "device" not in calls[0]


def test_weights_are_released_after_transcription(tmp_path, monkeypatch):
    install_fake_mlx(monkeypatch)
    audio = tmp_path / "meeting.m4a"
    audio.write_bytes(b"fake")

    transcribe_audio(
        audio, metadata(audio), mlx_settings(), model=_load_model(mlx_settings())
    )

    assert FakeModelHolder.model is None
    assert FakeModelHolder.model_path is None


def test_weights_are_released_even_when_transcription_fails(tmp_path, monkeypatch):
    def boom(_kwargs):
        raise RuntimeError("decode failed")

    install_fake_mlx(monkeypatch, on_call=boom)
    audio = tmp_path / "meeting.m4a"
    audio.write_bytes(b"fake")

    with pytest.raises(AudioDecodeError):
        transcribe_audio(
            audio, metadata(audio), mlx_settings(), model=_load_model(mlx_settings())
        )

    assert FakeModelHolder.model is None


def test_no_speech_result_raises_instead_of_producing_an_empty_transcript(tmp_path, monkeypatch):
    from meeting_pipeline.errors import NoSpeechError

    install_fake_mlx(monkeypatch, result={"text": "", "language": "es", "segments": []})
    audio = tmp_path / "meeting.m4a"
    audio.write_bytes(b"fake")

    with pytest.raises(NoSpeechError):
        transcribe_audio(
            audio, metadata(audio), mlx_settings(), model=_load_model(mlx_settings())
        )
