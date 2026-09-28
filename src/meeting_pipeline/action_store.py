"""SQLite-backed canonical action lineage with append-only reviewer history."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

STATUSES = {"open", "in_progress", "blocked", "completed", "cancelled"}


class ActionStoreError(ValueError):
    """A domain error that callers can translate to a stable API response."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


class ActionStore:
    """Owns action-lineage schema migrations and transactional persistence operations."""

    def __init__(self, database_path: Path | str):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _migrate(self) -> None:
        migration = Path(__file__).with_name("migrations") / "001_action_lineage.sql"
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"  # noqa: E501
            )
            if (
                connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ?", ("001_action_lineage",)
                ).fetchone()
                is None
            ):
                connection.executescript(migration.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    ("001_action_lineage", _now()),
                )

    def create_series(
        self, workspace_id: str, name: str, created_by: str, description: str | None = None
    ) -> dict[str, Any]:
        if not name.strip():
            raise ActionStoreError("series name is required")
        record = {
            "id": str(uuid.uuid4()),
            "workspace_id": workspace_id,
            "name": name.strip(),
            "description": description,
            "created_by": created_by,
            "created_at": _now(),
            "archived_at": None,
        }
        try:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO meeting_series
                    (id, workspace_id, name, description, created_by, created_at, archived_at)
                    VALUES (:id, :workspace_id, :name, :description, :created_by, :created_at, :archived_at)""",  # noqa: E501
                    record,
                )
        except sqlite3.IntegrityError as exc:
            raise ActionStoreError("series name already exists in this workspace") from exc
        return record

    def assign_meeting_series(
        self,
        meeting_id: str,
        workspace_id: str,
        series_id: str | None,
        changed_by: str,
        audit_reason: str = "explicit meeting preparation selection",
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if series_id is not None:
                series = connection.execute(
                    "SELECT workspace_id, archived_at FROM meeting_series WHERE id = ?",
                    (series_id,),
                ).fetchone()
                if series is None or series["workspace_id"] != workspace_id:
                    raise ActionStoreError("meeting series is not available in this workspace")
                if series["archived_at"] is not None:
                    raise ActionStoreError("archived series cannot be selected")
            materialized = connection.execute(
                "SELECT 1 FROM canonical_actions WHERE source_meeting_id = ? LIMIT 1", (meeting_id,)
            ).fetchone()
            if materialized:
                raise ActionStoreError("cannot change series after action materialization")
            connection.execute(
                """INSERT INTO meeting_series_memberships
                (id, meeting_id, workspace_id, series_id, changed_by, audit_reason, changed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    meeting_id,
                    workspace_id,
                    series_id,
                    changed_by,
                    audit_reason,
                    _now(),
                ),
            )
            connection.commit()

    def materialize_finalization(
        self,
        *,
        meeting_id: str,
        workspace_id: str,
        series_id: str | None,
        finalization_id: str,
        reviewer_id: str,
        idempotency_key: str,
        approved_actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if series_id is None:
            raise ActionStoreError("standalone_meeting_has_no_action_series")
        payload = {
            "meeting_id": meeting_id,
            "finalization_id": finalization_id,
            "actions": approved_actions,
        }
        request_hash = _hash(payload)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            receipt = connection.execute(
                """SELECT request_hash, response_json FROM finalization_materialization_receipts
                WHERE meeting_id = ? AND finalization_id = ? AND idempotency_key = ?""",
                (meeting_id, finalization_id, idempotency_key),
            ).fetchone()
            if receipt:
                if receipt["request_hash"] != request_hash:
                    raise ActionStoreError("idempotency_key_reused")
                connection.commit()
                return json.loads(receipt["response_json"])

            membership = connection.execute(
                """SELECT series_id FROM meeting_series_memberships WHERE meeting_id = ?
                ORDER BY changed_at DESC LIMIT 1""",
                (meeting_id,),
            ).fetchone()
            if membership is None or membership["series_id"] != series_id:
                raise ActionStoreError("meeting series membership must be explicitly assigned")

            result_actions: list[dict[str, Any]] = []
            for item in approved_actions:
                result_actions.append(
                    self._materialize_action(
                        connection,
                        item=item,
                        meeting_id=meeting_id,
                        workspace_id=workspace_id,
                        series_id=series_id,
                        finalization_id=finalization_id,
                        reviewer_id=reviewer_id,
                    )
                )
            response = {"finalization_id": finalization_id, "actions": result_actions}
            connection.execute(
                """INSERT INTO finalization_materialization_receipts
                (id, meeting_id, finalization_id, idempotency_key, request_hash,
                 response_json, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    meeting_id,
                    finalization_id,
                    idempotency_key,
                    request_hash,
                    _canonical(response),
                    _now(),
                ),
            )
            connection.commit()
            return response

    def _materialize_action(
        self,
        connection: sqlite3.Connection,
        *,
        item: dict[str, Any],
        meeting_id: str,
        workspace_id: str,
        series_id: str,
        finalization_id: str,
        reviewer_id: str,
    ) -> dict[str, Any]:
        source_action_key = str(item.get("source_action_key", "")).strip()
        approved_text = str(item.get("approved_text", "")).strip()
        status = item.get("initial_status", "open")
        if not source_action_key or not approved_text or status not in STATUSES:
            raise ActionStoreError(
                "approved action requires source_action_key, text, and valid status"
            )
        snapshot_payload = {
            "approved_text": approved_text,
            "owner": item.get("owner"),
            "due_date": item.get("due_date"),
            "initial_status": status,
            "evidence_references": item.get("evidence_references", []),
        }
        fingerprint = _hash(snapshot_payload)
        existing = connection.execute(
            """SELECT id FROM canonical_actions WHERE source_finalization_id = ?
            AND source_action_key = ?""",
            (finalization_id, source_action_key),
        ).fetchone()
        if existing:
            return {"id": existing["id"], "source_action_key": source_action_key, "created": False}
        duplicate = connection.execute(
            """SELECT id FROM canonical_actions WHERE source_finalization_id = ?
            AND normalized_snapshot_fingerprint = ?""",
            (finalization_id, fingerprint),
        ).fetchone()
        if duplicate:
            raise ActionStoreError("duplicate approved action payload in finalization")

        action_id, snapshot_id, event_id, now = (
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            _now(),
        )
        source_payload_hash = _hash(snapshot_payload)
        connection.execute(
            """INSERT INTO approved_action_snapshots
            (id, approved_text, owner_json, due_date, initial_status, evidence_references_json,
             approved_by, approved_at, source_payload_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                approved_text,
                _canonical(item.get("owner")),
                item.get("due_date").isoformat()
                if isinstance(item.get("due_date"), date)
                else item.get("due_date"),
                status,
                _canonical(item.get("evidence_references", [])),
                reviewer_id,
                now,
                source_payload_hash,
            ),
        )
        connection.execute(
            """INSERT INTO canonical_actions
            (id, workspace_id, series_id, source_meeting_id,
             source_finalization_id, source_action_key, normalized_snapshot_fingerprint,
             snapshot_id, current_status, current_status_event_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                action_id,
                workspace_id,
                series_id,
                meeting_id,
                finalization_id,
                source_action_key,
                fingerprint,
                snapshot_id,
                status,
                event_id,
                now,
            ),
        )
        creation_payload = {
            "event_type": "created",
            "new_status": status,
            "source_action_key": source_action_key,
        }
        connection.execute(
            """INSERT INTO action_status_history
            (id, action_id, event_type, previous_status, new_status, changes_json, reason,
             meeting_id, evidence_references_json, reviewed_by, reviewed_at,
             idempotency_key, request_hash)
            VALUES (?, ?, 'created', NULL, ?, NULL, NULL, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                action_id,
                status,
                meeting_id,
                _canonical(item.get("evidence_references", [])),
                reviewer_id,
                now,
                f"materialize:{finalization_id}:{source_action_key}",
                _hash(creation_payload),
            ),
        )
        return {"id": action_id, "source_action_key": source_action_key, "created": True}

    def record_status_change(
        self,
        *,
        action_id: str,
        reviewer_id: str,
        expected_current_event_id: str,
        new_status: str,
        reason: str | None,
        idempotency_key: str,
        meeting_id: str | None = None,
        evidence_references: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if new_status not in STATUSES:
            raise ActionStoreError("invalid action status")
        request = {
            "event_type": "status_changed",
            "expected_current_event_id": expected_current_event_id,
            "new_status": new_status,
            "reason": reason,
            "meeting_id": meeting_id,
            "evidence_references": evidence_references or [],
        }
        request_hash = _hash(request)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            action = connection.execute(
                "SELECT current_status, current_status_event_id FROM canonical_actions "
                "WHERE id = ?",
                (action_id,),
            ).fetchone()
            if action is None:
                raise ActionStoreError("action not found")
            previous_event = connection.execute(
                """SELECT id, request_hash FROM action_status_history
                WHERE action_id = ? AND idempotency_key = ?""",
                (action_id, idempotency_key),
            ).fetchone()
            if previous_event:
                if previous_event["request_hash"] != request_hash:
                    raise ActionStoreError("idempotency_key_reused")
                connection.commit()
                return {"event_id": previous_event["id"], "action_id": action_id, "created": True}
            if action["current_status_event_id"] != expected_current_event_id:
                raise ActionStoreError("action_state_conflict")
            old_status = action["current_status"]
            reopening = old_status in {"completed", "cancelled"} and new_status in {
                "open",
                "in_progress",
            }
            if (new_status in {"blocked", "cancelled"} or reopening) and not reason:
                raise ActionStoreError("status transition requires a reason")
            event_id, now = str(uuid.uuid4()), _now()
            connection.execute(
                """INSERT INTO action_status_history
                (id, action_id, event_type, previous_status, new_status, changes_json, reason,
                 meeting_id, evidence_references_json, reviewed_by, reviewed_at,
                 idempotency_key, request_hash)
                VALUES (?, ?, 'status_changed', ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    action_id,
                    old_status,
                    new_status,
                    reason,
                    meeting_id,
                    _canonical(evidence_references or []),
                    reviewer_id,
                    now,
                    idempotency_key,
                    request_hash,
                ),
            )
            connection.execute(
                "UPDATE canonical_actions SET current_status = ?, current_status_event_id = ? "
                "WHERE id = ?",
                (new_status, event_id, action_id),
            )
            connection.commit()
            return {"event_id": event_id, "action_id": action_id, "created": True}

    def counts(self) -> dict[str, int]:
        with self._connect() as connection:
            return {
                "actions": connection.execute("SELECT COUNT(*) FROM canonical_actions").fetchone()[
                    0
                ],
                "snapshots": connection.execute(
                    "SELECT COUNT(*) FROM approved_action_snapshots"
                ).fetchone()[0],
                "history_events": connection.execute(
                    "SELECT COUNT(*) FROM action_status_history"
                ).fetchone()[0],
                "receipts": connection.execute(
                    "SELECT COUNT(*) FROM finalization_materialization_receipts"
                ).fetchone()[0],
            }

    def get_action(self, action_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            action = connection.execute(
                """SELECT a.*, s.approved_text, s.owner_json, s.due_date, s.initial_status,
                s.evidence_references_json, s.approved_by, s.approved_at, s.source_payload_hash
                FROM canonical_actions a JOIN approved_action_snapshots s ON s.id = a.snapshot_id
                WHERE a.id = ?""",
                (action_id,),
            ).fetchone()
            if action is None:
                raise ActionStoreError("action not found")
            history = connection.execute(
                "SELECT * FROM action_status_history WHERE action_id = ? ORDER BY reviewed_at, id",
                (action_id,),
            ).fetchall()
        result = dict(action)
        for field in ("owner_json", "evidence_references_json"):
            result[field.removesuffix("_json")] = json.loads(result.pop(field))
        result["history"] = [dict(event) for event in history]
        return result
