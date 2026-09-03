"""Audio metadata inspection through ffprobe."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .errors import AudioError
from .manifest import sha256_file
from .models import AudioMetadata


def parse_ffprobe_output(path: Path, data: dict[str, Any]) -> AudioMetadata:
    streams = [s for s in data.get("streams", []) if s.get("codec_type") in {None, "audio"}]
    if not streams:
        raise AudioError(f"no audio stream found in {path}")
    stream = streams[0]
    fmt = data.get("format") or {}
    try:
        duration = float(fmt["duration"])
        if duration <= 0:
            raise ValueError
        return AudioMetadata(
            path=str(path.resolve()),
            filename=path.name,
            size_bytes=path.stat().st_size,
            sha256=sha256_file(path),
            codec_name=str(stream["codec_name"]),
            sample_rate=int(stream["sample_rate"]),
            channels=int(stream["channels"]),
            duration_seconds=duration,
            bit_rate=int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
            format_name=fmt.get("format_name"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AudioError(f"incomplete ffprobe metadata for {path}") from exc


def probe_audio(path: Path, ffprobe: str = "ffprobe") -> AudioMetadata:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise AudioError(f"audio file not found: {path}")
    args = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration,size,bit_rate,format_name:stream=codec_type,codec_name,sample_rate,channels",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=60, check=False)
    except FileNotFoundError as exc:
        raise AudioError("ffprobe is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioError(f"ffprobe timed out for {path}") from exc
    if result.returncode != 0:
        raise AudioError(f"ffprobe failed for {path}: {result.stderr.strip()}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AudioError("ffprobe returned invalid JSON") from exc
    return parse_ffprobe_output(path, data)
