from datetime import date

from meeting_pipeline.action_store import ActionStore


def _approved_action(key: str = "A-1") -> dict:
    return {
        "source_action_key": key,
        "approved_text": "Prepare vendor comparison",
        "owner": {"display_name": "Avery", "principal_id": "user-123"},
        "due_date": date(2026, 10, 2),
        "initial_status": "open",
        "evidence_references": [
            {"kind": "acta", "artifact_id": "acta-1", "section": "Actions", "item_index": 1}
        ],
    }


def test_materializing_the_same_finalization_twice_preserves_one_action_and_created_event(
    tmp_path,
):
    store = ActionStore(tmp_path / "actions.sqlite3")
    series = store.create_series("workspace-1", "Weekly operations", "reviewer-1")
    store.assign_meeting_series("meeting-1", "workspace-1", series["id"], "reviewer-1")

    first = store.materialize_finalization(
        meeting_id="meeting-1",
        workspace_id="workspace-1",
        series_id=series["id"],
        finalization_id="finalization-1",
        reviewer_id="reviewer-1",
        idempotency_key="finalize:meeting-1:finalization-1",
        approved_actions=[_approved_action()],
    )
    retry = store.materialize_finalization(
        meeting_id="meeting-1",
        workspace_id="workspace-1",
        series_id=series["id"],
        finalization_id="finalization-1",
        reviewer_id="reviewer-1",
        idempotency_key="finalize:meeting-1:finalization-1",
        approved_actions=[_approved_action()],
    )

    assert first == retry
    assert first["actions"][0]["created"] is True
    assert store.counts() == {"actions": 1, "snapshots": 1, "history_events": 1, "receipts": 1}
    assert store.get_action(first["actions"][0]["id"])["history"][0]["event_type"] == "created"


def test_status_history_is_idempotent_and_does_not_mutate_the_approved_snapshot(tmp_path):
    store = ActionStore(tmp_path / "actions.sqlite3")
    series = store.create_series("workspace-1", "Weekly operations", "reviewer-1")
    store.assign_meeting_series("meeting-1", "workspace-1", series["id"], "reviewer-1")
    action_id = store.materialize_finalization(
        meeting_id="meeting-1",
        workspace_id="workspace-1",
        series_id=series["id"],
        finalization_id="finalization-1",
        reviewer_id="reviewer-1",
        idempotency_key="finalize:meeting-1:finalization-1",
        approved_actions=[_approved_action()],
    )["actions"][0]["id"]
    initial = store.get_action(action_id)

    first = store.record_status_change(
        action_id=action_id,
        reviewer_id="reviewer-1",
        expected_current_event_id=initial["current_status_event_id"],
        new_status="completed",
        reason="Deliverable accepted",
        idempotency_key="review:meeting-1:action-1:completion",
        meeting_id="meeting-1",
        evidence_references=[{"kind": "acta", "section": "Review", "item_index": 1}],
    )
    retry = store.record_status_change(
        action_id=action_id,
        reviewer_id="reviewer-1",
        expected_current_event_id=initial["current_status_event_id"],
        new_status="completed",
        reason="Deliverable accepted",
        idempotency_key="review:meeting-1:action-1:completion",
        meeting_id="meeting-1",
        evidence_references=[{"kind": "acta", "section": "Review", "item_index": 1}],
    )

    refreshed = store.get_action(action_id)
    assert first == retry
    assert refreshed["current_status"] == "completed"
    assert refreshed["approved_text"] == "Prepare vendor comparison"
    assert len(refreshed["history"]) == 2
