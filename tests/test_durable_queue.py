from pathlib import Path

from meeting_pipeline.queue import DurableMeetingQueue


def test_enqueued_meetings_keep_fifo_order_and_durable_positions(tmp_path: Path):
    queue = DurableMeetingQueue(tmp_path)

    first = queue.enqueue("2026-09-03")
    second = queue.enqueue("2026-09-04")

    assert [job.meeting_date for job in queue.queued()] == ["2026-09-03", "2026-09-04"]
    assert first.position == 1
    assert second.position == 2
    assert DurableMeetingQueue(tmp_path).position("2026-09-04") == 2


def test_claims_oldest_job_once_and_failure_releases_the_next_job(tmp_path: Path):
    queue = DurableMeetingQueue(tmp_path)
    queue.enqueue("2026-09-03")
    queue.enqueue("2026-09-04")

    first = queue.claim_next()

    assert first is not None
    assert first.meeting_date == "2026-09-03"
    assert first.status == "running"
    assert queue.claim_next() is None
    assert queue.fail("2026-09-03", "transcription failed")
    second = queue.claim_next()
    assert second is not None
    assert second.meeting_date == "2026-09-04"


def test_cancellation_and_restart_recovery_preserve_fifo_sequence(tmp_path: Path):
    queue = DurableMeetingQueue(tmp_path)
    first = queue.enqueue("2026-09-03")
    queue.enqueue("2026-09-04")
    assert queue.cancel("2026-09-04")
    assert not queue.cancel("2026-09-04")

    claimed = queue.claim_next()
    assert claimed is not None
    assert claimed.meeting_date == "2026-09-03"
    assert queue.recover_running() == 1

    recovered = DurableMeetingQueue(tmp_path).get("2026-09-03")
    assert recovered is not None
    assert recovered.status == "queued"
    assert recovered.sequence == first.sequence
    assert recovered.position == 1
