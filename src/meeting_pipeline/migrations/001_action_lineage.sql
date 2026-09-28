CREATE TABLE IF NOT EXISTS meeting_series (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    archived_at TEXT,
    UNIQUE(workspace_id, name COLLATE NOCASE)
);

CREATE TABLE IF NOT EXISTS meeting_series_memberships (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    series_id TEXT,
    changed_by TEXT NOT NULL,
    audit_reason TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    UNIQUE(meeting_id, changed_at)
);
CREATE INDEX IF NOT EXISTS idx_membership_meeting ON meeting_series_memberships(meeting_id, changed_at DESC);

CREATE TABLE IF NOT EXISTS approved_action_snapshots (
    id TEXT PRIMARY KEY,
    approved_text TEXT NOT NULL,
    owner_json TEXT,
    due_date TEXT,
    initial_status TEXT NOT NULL CHECK(initial_status IN ('open', 'in_progress', 'blocked', 'completed', 'cancelled')),
    evidence_references_json TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    source_payload_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS canonical_actions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    series_id TEXT NOT NULL REFERENCES meeting_series(id),
    source_meeting_id TEXT NOT NULL,
    source_finalization_id TEXT NOT NULL,
    source_action_key TEXT NOT NULL,
    normalized_snapshot_fingerprint TEXT,
    snapshot_id TEXT NOT NULL UNIQUE REFERENCES approved_action_snapshots(id),
    current_status TEXT NOT NULL CHECK(current_status IN ('open', 'in_progress', 'blocked', 'completed', 'cancelled')),
    current_status_event_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source_finalization_id, source_action_key),
    UNIQUE(source_finalization_id, normalized_snapshot_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_actions_series_status ON canonical_actions(series_id, current_status);

CREATE TABLE IF NOT EXISTS action_status_history (
    id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL REFERENCES canonical_actions(id),
    event_type TEXT NOT NULL CHECK(event_type IN ('created', 'status_changed', 'action_metadata_changed', 'comment_added')),
    previous_status TEXT,
    new_status TEXT CHECK(new_status IS NULL OR new_status IN ('open', 'in_progress', 'blocked', 'completed', 'cancelled')),
    changes_json TEXT,
    reason TEXT,
    meeting_id TEXT,
    evidence_references_json TEXT NOT NULL,
    reviewed_by TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    UNIQUE(action_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_history_action_reviewed ON action_status_history(action_id, reviewed_at, id);

CREATE TABLE IF NOT EXISTS finalization_materialization_receipts (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    finalization_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    UNIQUE(meeting_id, finalization_id, idempotency_key)
);

CREATE TRIGGER IF NOT EXISTS deny_snapshot_update
BEFORE UPDATE ON approved_action_snapshots BEGIN SELECT RAISE(ABORT, 'approved snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS deny_snapshot_delete
BEFORE DELETE ON approved_action_snapshots BEGIN SELECT RAISE(ABORT, 'approved snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS deny_history_update
BEFORE UPDATE ON action_status_history BEGIN SELECT RAISE(ABORT, 'action history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS deny_history_delete
BEFORE DELETE ON action_status_history BEGIN SELECT RAISE(ABORT, 'action history is append-only'); END;
