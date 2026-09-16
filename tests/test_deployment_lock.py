"""One expensive pipeline job at a time per deployment root, across meetings and processes."""

import subprocess
import sys
import textwrap
from datetime import date
from pathlib import Path

import pytest

from meeting_pipeline.errors import PipelineBusyError
from meeting_pipeline.ingest import ingest_meeting
from meeting_pipeline.manifest import deployment_lock, load_manifest
from meeting_pipeline.pipeline import run_stages
from test_pipeline import FakeWhisper, fake_probe

HOLDER = textwrap.dedent(
    """
    import sys, time
    from meeting_pipeline.manifest import deployment_lock

    with deployment_lock(sys.argv[1]):
        print("held", flush=True)
        time.sleep(float(sys.argv[2]))
    """
)


def hold_lock(root: Path, seconds: float) -> subprocess.Popen:
    """A separate OS process, because a same-process lock would prove nothing about flock."""
    process = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(root), str(seconds)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "held"
    return process


def test_a_second_meeting_in_another_process_is_rejected_with_a_bounded_wait(tmp_path):
    root = tmp_path / "Recordings"
    root.mkdir()
    holder = hold_lock(root, 30.0)
    try:
        with (
            pytest.raises(PipelineBusyError) as excinfo,
            deployment_lock(root, timeout_seconds=0.5),
        ):
            pytest.fail("the deployment lock must not be granted twice")
    finally:
        holder.kill()
        holder.wait()

    assert "deployment" in str(excinfo.value).casefold()


def test_the_lock_is_released_when_the_body_raises(tmp_path):
    root = tmp_path / "Recordings"
    root.mkdir()

    with pytest.raises(RuntimeError), deployment_lock(root):
        raise RuntimeError("stage failed")

    with deployment_lock(root):
        pass


def test_different_deployment_roots_do_not_block_each_other(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    holder = hold_lock(first, 30.0)
    try:
        with deployment_lock(second, timeout_seconds=0.5):
            pass
    finally:
        holder.kill()
        holder.wait()


def test_run_stages_refuses_a_second_meeting_without_marking_any_stage_failed(tmp_path):
    root = tmp_path / "Recordings"
    busy = tmp_path / "busy.m4a"
    busy.write_bytes(b"recording")
    meeting_dir = ingest_meeting(busy, None, date(2026, 8, 31), root)
    holder = hold_lock(root, 30.0)
    try:
        with pytest.raises(PipelineBusyError):
            run_stages(
                meeting_dir,
                until="transcribe",
                transcription_model=FakeWhisper(),
                audio_probe=fake_probe,
                busy_wait_seconds=0.5,
            )
    finally:
        holder.kill()
        holder.wait()

    manifest = load_manifest(meeting_dir / "manifest.json")
    assert all(state.status != "failed" for state in manifest.stages.values())
    assert all(state.status != "running" for state in manifest.stages.values())
