# Durable meeting queue

`DurableMeetingQueue` stores a deployment-scoped SQLite database at
`<output-root>/.meeting-queue.sqlite3`. The schema is migrated with
`PRAGMA user_version`; version 1 uses WAL plus `synchronous=FULL`.

Jobs are keyed by `meeting_key()` from `meetings.py`: the meeting UUID, or the ISO date for
legacy meetings without one. The column is still named `meeting_date` for schema compatibility.

## Status API

`GET /api/meetings` and `GET /api/meetings/{id}` read the queue database on every request:

- `queue` is the raw durable record (`queued`, `running`, `failed`, `cancelled`, `completed`,
  plus `position`, set only while queued, and timestamps), or `null` if never enqueued.
- `job` is the merged view the UI uses. A running stage wins; otherwise a queued record appears
  as `{"status": "queued", "position": N}`; otherwise the most recent durable write between the
  queue terminal state and the stage manifest wins.

`POST /api/meetings/{id}/cancel` cancels a queued record in one transaction and returns
`{"status": "cancelled"}`; it leaves FIFO order immediately and later positions shift up.
A running meeting gets the existing cooperative stop request (`cancellation_requested`).
Cancelling a failed, cancelled, or completed queue record returns 409.

## Invariants

- Upload acknowledgement must occur only after immutable artifacts, `manifest.json`, and the
  queue `enqueue()` transaction have committed. Retried uploads call `enqueue()` again with the
  same meeting date and receive the original record; the primary key prevents a second job.
- `sequence` is allocated inside `BEGIN IMMEDIATE`, never changed, and is the sole FIFO order.
  A queued position is `1 +` the number of earlier durable queued sequences.
- `claim_next()` runs in one immediate transaction and claims only the lowest queued sequence.
  The partial unique index on `status = 'running'` makes a duplicate active claim impossible,
  even if a caller bypasses the normal worker loop.
- Only queued jobs may be cancelled. A cancelled job is excluded from position calculations and
  cannot be claimed. Completion and failure require the row to be running, so stale workers
  cannot overwrite another state.
- On a verified server restart, call `recover_running()` before polling: it requeues the
  interrupted claim without changing its sequence. Existing stage manifests/checkpoints remain
  authoritative, so the worker can reuse completed extraction work.
- The worker must acquire the existing `deployment_lock()` before invoking pipeline stages. The
  queue owns durable admission and claims; the lock remains the cross-process guard around
  expensive model execution.
