"""Durable FIFO queue for serial meeting processing.

Queue invariants:

* Every job has one immutable ``sequence`` assigned by an IMMEDIATE SQLite transaction.
* Only queued jobs have a position; it is computed from durable sequence order, not memory.
* SQLite's partial unique index permits at most one running job across this output root.
* A worker can claim only the oldest queued job, and terminal transitions require that claim.
* Restart recovery returns a stale running claim to queued without changing its sequence.

The queue database lives alongside the deployment lock at ``<output-root>/.meeting-queue.sqlite3``.
The database uses WAL and FULL synchronous mode: acknowledgement can safely depend on a committed
queue entry after meeting artifacts and the manifest have been persisted.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

QueueStatus = Literal["queued", "running", "failed", "cancelled", "completed"]


@dataclass(frozen=True)
class QueueJob:
    meeting_date: str
    sequence: int
    status: QueueStatus
    enqueued_at: str
    claimed_at: str | None
    finished_at: str | None
    error: str | None
    position: int | None


class DurableMeetingQueue:
    """SQLite-backed queue scoped to one deployment output root."""

    _SCHEMA_VERSION = 1

    def __init__(self, output_root: Path):
        self.output_root = Path(output_root).expanduser().resolve()
        self.path = self.output_root / ".meeting-queue.sqlite3"
        self.output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _migrate(self) -> None:
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > self._SCHEMA_VERSION:
                raise RuntimeError(
                    f"queue database schema {version} is newer than this application"
                )
            if version == 0:
                connection.executescript(
                    """
                    PRAGMA journal_mode = WAL;
                    PRAGMA synchronous = FULL;
                    CREATE TABLE meeting_queue_jobs (
                        meeting_date TEXT PRIMARY KEY,
                        sequence INTEGER NOT NULL UNIQUE,
                        status TEXT NOT NULL CHECK (
                            status IN ('queued', 'running', 'failed', 'cancelled', 'completed')
                        ),
                        enqueued_at TEXT NOT NULL,
                        claimed_at TEXT,
                        finished_at TEXT,
                        error TEXT
                    );
                    CREATE INDEX meeting_queue_jobs_fifo
                        ON meeting_queue_jobs(status, sequence);
                    CREATE UNIQUE INDEX meeting_queue_one_active_claim
                        ON meeting_queue_jobs(status) WHERE status = 'running';
                    PRAGMA user_version = 1;
                    """
                )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _job(row: sqlite3.Row, position: int | None = None) -> QueueJob:
        return QueueJob(
            meeting_date=row["meeting_date"],
            sequence=row["sequence"],
            status=row["status"],
            enqueued_at=row["enqueued_at"],
            claimed_at=row["claimed_at"],
            finished_at=row["finished_at"],
            error=row["error"],
            position=position,
        )

    def _get(self, connection: sqlite3.Connection, meeting_date: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM meeting_queue_jobs WHERE meeting_date = ?", (meeting_date,)
        ).fetchone()

    @staticmethod
    def _position(connection: sqlite3.Connection, sequence: int, status: str) -> int | None:
        if status != "queued":
            return None
        return connection.execute(
            "SELECT COUNT(*) + 1 FROM meeting_queue_jobs WHERE status = 'queued' AND sequence < ?",
            (sequence,),
        ).fetchone()[0]

    def get(self, meeting_date: str) -> QueueJob | None:
        with self._connect() as connection:
            row = self._get(connection, meeting_date)
            if row is None:
                return None
            return self._job(row, self._position(connection, row["sequence"], row["status"]))

    def enqueue(self, meeting_date: str) -> QueueJob:
        """Create a job once; retries return its original durable queue entry."""
        with self._transaction() as connection:
            row = self._get(connection, meeting_date)
            if row is None:
                sequence = connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM meeting_queue_jobs"
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO meeting_queue_jobs(meeting_date, sequence, status, enqueued_at) "
                    "VALUES (?, ?, 'queued', ?)",
                    (meeting_date, sequence, self._now()),
                )
                row = self._get(connection, meeting_date)
            assert row is not None
            return self._job(row, self._position(connection, row["sequence"], row["status"]))

    def queued(self) -> list[QueueJob]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM meeting_queue_jobs WHERE status = 'queued' ORDER BY sequence"
            ).fetchall()
            return [self._job(row, index) for index, row in enumerate(rows, start=1)]

    def position(self, meeting_date: str) -> int | None:
        job = self.get(meeting_date)
        return job.position if job else None

    def claim_next(self) -> QueueJob | None:
        """Atomically claim the oldest queued job, or return None while another job runs."""
        with self._transaction() as connection:
            running = connection.execute(
                "SELECT 1 FROM meeting_queue_jobs WHERE status = 'running'"
            ).fetchone()
            if running:
                return None
            row = connection.execute(
                "SELECT * FROM meeting_queue_jobs WHERE status = 'queued' ORDER BY sequence LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE meeting_queue_jobs SET status = 'running', claimed_at = ?, error = NULL "
                "WHERE meeting_date = ? AND status = 'queued'",
                (self._now(), row["meeting_date"]),
            )
            claimed = self._get(connection, row["meeting_date"])
            assert claimed is not None
            return self._job(claimed)

    def cancel(self, meeting_date: str) -> bool:
        """Cancel only a queued job; running and terminal jobs are intentionally immutable."""
        with self._transaction() as connection:
            result = connection.execute(
                "UPDATE meeting_queue_jobs SET status = 'cancelled', finished_at = ? "
                "WHERE meeting_date = ? AND status = 'queued'",
                (self._now(), meeting_date),
            )
            return result.rowcount == 1

    def complete(self, meeting_date: str) -> bool:
        return self._finish(meeting_date, "completed")

    def fail(self, meeting_date: str, error: str) -> bool:
        return self._finish(meeting_date, "failed", error)

    def _finish(self, meeting_date: str, status: QueueStatus, error: str | None = None) -> bool:
        with self._transaction() as connection:
            result = connection.execute(
                "UPDATE meeting_queue_jobs SET status = ?, finished_at = ?, error = ? "
                "WHERE meeting_date = ? AND status = 'running'",
                (status, self._now(), error, meeting_date),
            )
            return result.rowcount == 1

    def recover_running(self) -> int:
        """Release interrupted claims in FIFO order for a restart worker to reclaim."""
        with self._transaction() as connection:
            result = connection.execute(
                "UPDATE meeting_queue_jobs SET status = 'queued', claimed_at = NULL "
                "WHERE status = 'running'"
            )
            return result.rowcount
