from datetime import date

from meeting_pipeline.ingest import ingest_meeting
from meeting_pipeline.manifest import load_manifest, meeting_lock, start_stage, write_manifest
from meeting_pipeline.web import _mark_interrupted_jobs


def test_recovery_does_not_interrupt_a_live_locked_job(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"test audio")
    root = tmp_path / "meetings"
    meeting = ingest_meeting(audio, None, date(2026, 9, 9), root)
    path = meeting / "manifest.json"
    manifest = load_manifest(path)
    start_stage(manifest, "transcribe")
    write_manifest(path, manifest)
    with meeting_lock(meeting):
        _mark_interrupted_jobs(root)
        assert load_manifest(path).stages["transcribe"].status == "running"
    _mark_interrupted_jobs(root)
    state = load_manifest(path).stages["transcribe"]
    assert state.status == "failed"
    assert state.error_code == "JOB_INTERRUPTED"
