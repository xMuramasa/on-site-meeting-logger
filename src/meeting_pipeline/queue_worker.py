"""Serial worker for durable meeting queue entries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .errors import PipelineBusyError, PipelineError
from .manifest import deployment_lock
from .meetings import find_meeting
from .queue import DurableMeetingQueue, QueueJob, QueueStatus


@dataclass(frozen=True)
class QueueWorkOutcome:
    """Durable result of one claimed queue entry."""

    meeting_date: str
    status: QueueStatus
    recovered_running: bool


class QueueWorker:
    """Drain one deployment queue under the same lock used by pipeline processing.

    The lock spans recovery, claiming, and processing. A competing worker therefore cannot
    recover a live claim or run another meeting concurrently. Pipeline stages retain their
    checkpoint behavior because the supplied processor resumes a meeting with ``force=False``.
    """

    def __init__(
        self,
        queue: DurableMeetingQueue,
        process_meeting: Callable[[Path], object],
        *,
        lock_timeout_seconds: float = 0,
    ) -> None:
        self.queue = queue
        self.process_meeting = process_meeting
        self.lock_timeout_seconds = lock_timeout_seconds

    @classmethod
    def for_pipeline(
        cls,
        output_root: Path,
        *,
        config_path: Path | None = None,
        until: str = "generate_review",
        lock_timeout_seconds: float = 0,
    ) -> QueueWorker:
        """Build a worker that resumes existing stage checkpoints for queued meetings."""
        from .pipeline import run_stages

        root = Path(output_root).expanduser().resolve()
        return cls(
            DurableMeetingQueue(root),
            lambda meeting_dir: run_stages(
                meeting_dir,
                until=until,
                config_path=config_path,
                force=False,
                acquire_deployment_lock=False,
            ),
            lock_timeout_seconds=lock_timeout_seconds,
        )

    def recover_and_drain(self) -> list[QueueWorkOutcome]:
        """Recover interrupted work then process every eligible job in FIFO order.

        Lock contention intentionally leaves the queue untouched; a future worker invocation
        will try again. Individual processing failures are persisted and do not stop later jobs.
        """
        try:
            with deployment_lock(self.queue.output_root, self.lock_timeout_seconds):
                recovered_count = self.queue.recover_running()
                recovered_running = recovered_count > 0
                outcomes: list[QueueWorkOutcome] = []
                while job := self.queue.claim_next():
                    outcomes.append(self._process_claim(job, recovered_running))
                return outcomes
        except PipelineBusyError:
            return []

    def _meeting_dir(self, key: str) -> Path:
        """Resolve a queue key (UUID or legacy date) to its meeting directory."""
        try:
            return find_meeting(self.queue.output_root, key)
        except PipelineError:
            return self.queue.output_root / key

    def _process_claim(self, job: QueueJob, recovered_running: bool) -> QueueWorkOutcome:
        try:
            self.process_meeting(self._meeting_dir(job.meeting_date))
        except Exception as exc:
            self.queue.fail(job.meeting_date, str(exc))
            return QueueWorkOutcome(job.meeting_date, "failed", recovered_running)
        self.queue.complete(job.meeting_date)
        return QueueWorkOutcome(job.meeting_date, "completed", recovered_running)
