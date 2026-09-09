"""Audio metadata inspection through ffprobe."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Literal

from .errors import AudioDecodeError, AudioError
from .manifest import sha256_file
from .models import AudioLevelAnalysis, AudioMetadata

SILENT_MAX_DB = -60.0
QUIET_MEAN_DB = -45.0
MAX_LEVEL_SCAN_SECONDS = 120


def classify_audio_levels(mean_db: float, max_db: float) -> Literal["silent", "quiet", "normal"]:
    """Classify decoded audio conservatively without inferring speech content."""
    if max_db <= SILENT_MAX_DB:
        return "silent"
    if mean_db <= QUIET_MEAN_DB:
        return "quiet"
    return "normal"


def inspect_audio_levels(
    path: Path, ffmpeg: str = "ffmpeg", scan_seconds: int = MAX_LEVEL_SCAN_SECONDS
) -> AudioLevelAnalysis:
    """Decode at most the recording's opening two minutes and measure its signal level."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise AudioDecodeError(f"audio file not found: {path}")
    if not 0 < scan_seconds <= MAX_LEVEL_SCAN_SECONDS:
        raise ValueError(f"scan_seconds must be between 1 and {MAX_LEVEL_SCAN_SECONDS}")
    args = [
        ffmpeg,
        "-v",
        "info",
        "-t",
        str(scan_seconds),
        "-i",
        str(path),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=90, check=False)
    except FileNotFoundError as exc:
        raise AudioDecodeError("ffmpeg is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioDecodeError(f"ffmpeg level check timed out for {path}") from exc
    if result.returncode != 0:
        raise AudioDecodeError(f"ffmpeg could not decode audio for {path}: {result.stderr.strip()}")
    mean = re.search(r"mean_volume:\s*(-?[\d.]+) dB", result.stderr)
    peak = re.search(r"max_volume:\s*(-?[\d.]+) dB", result.stderr)
    if mean is None or peak is None:
        raise AudioDecodeError(f"ffmpeg level check returned no audio measurements for {path}")
    mean_db = float(mean.group(1))
    max_db = float(peak.group(1))
    return AudioLevelAnalysis(
        scanned_seconds=float(scan_seconds),
        mean_db=mean_db,
        max_db=max_db,
        classification=classify_audio_levels(mean_db, max_db),
    )


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
