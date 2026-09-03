import json

import pytest

from meeting_pipeline.audio import parse_ffprobe_output, probe_audio
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
