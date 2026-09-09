import json

import pytest

from meeting_pipeline.audio import (
    classify_audio_levels,
    inspect_audio_levels,
    parse_ffprobe_output,
    probe_audio,
)
from meeting_pipeline.errors import AudioError

PROBE = {
    "streams": [{"codec_name": "aac", "sample_rate": "48000", "channels": 1}],
    "format": {
        "duration": "3132.309333",
        "size": "26076523",
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "bit_rate": "66600",
    },
}


def test_parse_ffprobe_audio_metadata(tmp_path):
    audio = tmp_path / "meeting.m4a"
    audio.write_bytes(b"fake")
    meta = parse_ffprobe_output(audio, PROBE)
    assert meta.codec_name == "aac"
    assert meta.sample_rate == 48000
    assert meta.channels == 1
    assert meta.duration_seconds == pytest.approx(3132.309333)
    assert len(meta.sha256) == 64


def test_parse_ffprobe_rejects_no_audio_stream(tmp_path):
    audio = tmp_path / "meeting.m4a"
    audio.write_bytes(b"fake")
    with pytest.raises(AudioError, match="audio stream"):
        parse_ffprobe_output(audio, {"streams": [], "format": {"duration": "1", "size": "4"}})


def test_probe_audio_invokes_ffprobe_without_shell(tmp_path, monkeypatch):
    audio = tmp_path / "meeting.m4a"
    audio.write_bytes(b"fake")
    seen = {}

    class Result:
        returncode = 0
        stdout = json.dumps(PROBE)
        stderr = ""

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)
    probe_audio(audio)
    assert seen["args"][0] == "ffprobe"
    assert seen["args"][-1] == str(audio)
    assert seen["kwargs"].get("shell") is not True


@pytest.mark.parametrize(
    ("mean_db", "max_db", "expected"),
    [
        (-91.0, -91.0, "silent"),
        (-47.0, -31.0, "quiet"),
        (-22.0, -3.0, "normal"),
    ],
)
def test_classify_audio_levels_distinguishes_synthetic_silence_quiet_and_speech(
    mean_db, max_db, expected
):
    assert classify_audio_levels(mean_db, max_db) == expected


def test_inspect_audio_levels_uses_a_bounded_local_decode(tmp_path, monkeypatch):
    audio = tmp_path / "large-recording.m4a"
    audio.write_bytes(b"large recording")
    seen = {}

    class Result:
        returncode = 0
        stderr = "[Parsed_volumedetect_0] mean_volume: -47.0 dB\n[Parsed_volumedetect_0] max_volume: -31.0 dB\n"

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)

    analysis = inspect_audio_levels(audio)

    assert analysis.classification == "quiet"
    assert analysis.mean_db == -47.0
    assert analysis.max_db == -31.0
    assert seen["args"][:2] == ["ffmpeg", "-v"]
    assert seen["args"][seen["args"].index("-t") + 1] == "120"
    assert seen["args"][-1] == "-"
    assert seen["kwargs"].get("shell") is not True
