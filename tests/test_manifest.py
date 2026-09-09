from datetime import date

from meeting_pipeline.manifest import (
    cancel_stage,
    complete_stage,
    create_manifest,
    load_manifest,
    sha256_file,
    stage_is_current,
    start_stage,
    write_manifest,
)


def test_sha256_file_is_stable(tmp_path):
    path = tmp_path / "x"
    path.write_bytes(b"abc")
    assert sha256_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_stage_fingerprint_controls_resume(tmp_path):
    source = tmp_path / "meeting.m4a"
    source.write_bytes(b"a")
    manifest = create_manifest(tmp_path, date(2026, 9, 7), source, None)
    assert not stage_is_current(manifest, "inspect", "fp-1")
    artifact = tmp_path / "build" / "x.json"
    artifact.parent.mkdir()
    artifact.write_text("{}")
    complete_stage(manifest, "inspect", "fp-1", {"metadata": artifact})
    assert stage_is_current(manifest, "inspect", "fp-1")
    assert not stage_is_current(manifest, "inspect", "fp-2")
    artifact.unlink()
    assert not stage_is_current(manifest, "inspect", "fp-1")


def test_manifest_round_trip(tmp_path):
    source = tmp_path / "meeting.m4a"
    source.write_bytes(b"a")
    manifest = create_manifest(tmp_path, date(2026, 9, 7), source, None)
    path = tmp_path / "manifest.json"
    write_manifest(path, manifest)
    assert load_manifest(path) == manifest


def test_stage_records_durable_running_failure_and_cancellation(tmp_path):
    source = tmp_path / "meeting.m4a"
    source.write_bytes(b"a")
    manifest = create_manifest(tmp_path, date(2026, 9, 7), source, None)

    start_stage(manifest, "transcribe")
    running = manifest.stages["transcribe"]
    assert running.status == "running"
    assert running.started_at is not None
    assert running.completed_at is None

    cancel_stage(manifest, "transcribe", "Cancellation requested")
    cancelled = manifest.stages["transcribe"]
    assert cancelled.status == "cancelled"
    assert cancelled.completed_at is None
    assert cancelled.note == "Cancellation requested"
