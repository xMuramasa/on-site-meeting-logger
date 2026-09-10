from datetime import date
from pathlib import Path

import pytest

from meeting_pipeline.errors import IngestError
from meeting_pipeline.ingest import ingest_meeting
from meeting_pipeline.manifest import load_manifest, sha256_file


def test_ingest_creates_immutable_sources_and_manifest(tmp_path):
    audio = tmp_path / "input.m4a"
    audio.write_bytes(b"audio-data")
    prior = tmp_path / "prior.pdf"
    prior.write_bytes(b"%PDF-prior")

    meeting_dir = ingest_meeting(audio, prior, date(2026, 9, 7), tmp_path / "out")

    copied = meeting_dir / "source" / "meeting.m4a"
    assert copied.read_bytes() == b"audio-data"
    assert (meeting_dir / "source" / "previous-acta.pdf").read_bytes() == b"%PDF-prior"
    manifest = load_manifest(meeting_dir / "manifest.json")
    assert manifest.source_sha256 == sha256_file(audio)
    assert manifest.previous_acta_sha256 == sha256_file(prior)
    assert (meeting_dir / "build").is_dir()


@pytest.mark.parametrize(
    ("suffix", "content_type"),
    [
        (".md", "text/markdown"),
        (".html", "text/html"),
        (".json", "application/json"),
    ],
)
def test_ingest_preserves_previous_acta_identity_hash_and_format(tmp_path, suffix, content_type):
    audio = tmp_path / "input.m4a"
    audio.write_bytes(b"audio-data")
    prior = tmp_path / f"prior{suffix}"
    if suffix == ".json":
        fixture = Path(__file__).parent / "fixtures" / "valid-acta.json"
        prior.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        prior.write_text("Prior acta", encoding="utf-8")

    meeting_dir = ingest_meeting(audio, prior, date(2026, 9, 7), tmp_path / "out")

    manifest = load_manifest(meeting_dir / "manifest.json")
    assert manifest.previous_acta_filename == prior.name
    assert manifest.previous_acta_sha256 == sha256_file(prior)
    assert manifest.previous_acta_format == content_type
    assert (meeting_dir / "source" / f"previous-acta{suffix}").read_bytes() == prior.read_bytes()


def test_ingest_resumes_when_source_is_identical(tmp_path):
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"same")
    first = ingest_meeting(audio, None, date(2026, 9, 7), tmp_path / "out")
    second = ingest_meeting(audio, None, date(2026, 9, 7), tmp_path / "out")
    assert first == second


def test_ingest_refuses_to_replace_different_source_even_with_force(tmp_path):
    first = tmp_path / "first.m4a"
    second = tmp_path / "second.m4a"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    root = tmp_path / "out"
    ingest_meeting(first, None, date(2026, 9, 7), root)
    with pytest.raises(IngestError, match="different source"):
        ingest_meeting(second, None, date(2026, 9, 7), root, force=True)


def test_ingest_rejects_unsupported_audio(tmp_path):
    audio = tmp_path / "input.txt"
    audio.write_text("no")
    with pytest.raises(IngestError, match="unsupported"):
        ingest_meeting(audio, None, date(2026, 9, 7), tmp_path / "out")
