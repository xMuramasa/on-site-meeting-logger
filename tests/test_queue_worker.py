from pathlib import Path

from meeting_pipeline.queue import DurableMeetingQueue
from meeting_pipeline.queue_worker import QueueWorker


def test_worker_processes_jobs_in_fifo_order_and_continues_after_failure(tmp_path: Path):
    queue = DurableMeetingQueue(tmp_path)
    queue.enqueue("2026-09-03")
    queue.enqueue("2026-09-04")
    queue.enqueue("2026-09-05")
    processed: list[str] = []

    def process(meeting_dir: Path) -> None:
        processed.append(meeting_dir.name)
        if meeting_dir.name == "2026-09-04":
            raise RuntimeError("transcription failed")

    outcomes = QueueWorker(queue, process, lock_timeout_seconds=0).recover_and_drain()

    assert processed == ["2026-09-03", "2026-09-04", "2026-09-05"]
    assert [outcome.status for outcome in outcomes] == ["completed", "failed", "completed"]
    assert queue.get("2026-09-03").status == "completed"  # type: ignore[union-attr]
    failed = queue.get("2026-09-04")
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error == "transcription failed"
    assert queue.get("2026-09-05").status == "completed"  # type: ignore[union-attr]


def test_restart_recovery_reuses_claimed_job_and_preserves_fifo_order(tmp_path: Path):
    queue = DurableMeetingQueue(tmp_path)
    queue.enqueue("2026-09-03")
    queue.enqueue("2026-09-04")
    claimed = queue.claim_next()
    assert claimed is not None
    assert claimed.meeting_date == "2026-09-03"
    processed: list[str] = []

    outcomes = QueueWorker(
        DurableMeetingQueue(tmp_path),
        lambda meeting_dir: processed.append(meeting_dir.name),
        lock_timeout_seconds=0,
    ).recover_and_drain()

    assert processed == ["2026-09-03", "2026-09-04"]
    assert [outcome.recovered_running for outcome in outcomes] == [True, True]
    assert [outcome.status for outcome in outcomes] == ["completed", "completed"]


def test_worker_leaves_queue_unchanged_when_deployment_lock_is_contended(tmp_path: Path):
    from meeting_pipeline.manifest import deployment_lock

    queue = DurableMeetingQueue(tmp_path)
    queue.enqueue("2026-09-03")

    with deployment_lock(tmp_path):
        outcomes = QueueWorker(queue, lambda meeting_dir: None, lock_timeout_seconds=0).recover_and_drain()

    assert outcomes == []
    queued = queue.get("2026-09-03")
    assert queued is not None
    assert queued.status == "queued"
