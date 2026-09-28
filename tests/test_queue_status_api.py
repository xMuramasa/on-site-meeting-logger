from pathlib import Path

from fastapi.testclient import TestClient

from meeting_pipeline.manifest import start_stage, write_manifest
from meeting_pipeline.models import PipelineManifest
from meeting_pipeline.queue import DurableMeetingQueue
from meeting_pipeline.queue_worker import QueueWorker
from meeting_pipeline.readiness import ReadinessReport
from meeting_pipeline.web import create_app

HEADERS = {"origin": "http://127.0.0.1:8765", "x-csrf-token": "test-csrf-token"}


def _client(root: Path) -> TestClient:
    return TestClient(
        create_app(
            output_root=root,
            csrf_token="test-csrf-token",
            runner=lambda *_args, **_kwargs: None,
            readiness=lambda: ReadinessReport(checks=[]),
        ),
        base_url="http://127.0.0.1:8765",
    )


def _upload(api: TestClient, meeting_date: str) -> str:
    response = api.post(
        "/api/meetings",
        headers=HEADERS,
        data={"meeting_date": meeting_date, "title": "Semanal"},
        files={"audio": ("meeting.webm", b"audio-" + meeting_date.encode(), "audio/webm")},
    )
    assert response.status_code == 202
    return response.json()["id"]


def _by_id(api: TestClient) -> dict[str, dict]:
    return {item["id"]: item for item in api.get("/api/meetings").json()["meetings"]}


def test_status_position_and_queued_cancellation_use_meeting_keys(tmp_path: Path):
    root = tmp_path / "meetings"
    api = _client(root)
    first, second, third = (_upload(api, day) for day in ("2026-09-03", "2026-09-03", "2026-09-04"))
    queue = DurableMeetingQueue(root)
    for key in (first, second, third):
        queue.enqueue(key)

    before = _by_id(api)
    assert before[second]["job"] == {"status": "queued", "stage": "inspect", "position": 2}
    assert before[third]["queue"]["position"] == 3
    detail = api.get(f"/api/meetings/{second}").json()
    assert detail["job"]["position"] == 2
    assert detail["queue"]["status"] == "queued"

    cancelled = api.post(f"/api/meetings/{second}/cancel", headers=HEADERS)
    assert cancelled.status_code == 202
    assert cancelled.json() == {"id": second, "date": "2026-09-03", "status": "cancelled"}

    # A fresh app instance reads the same durable queue: nothing is held in memory.
    after = _by_id(_client(root))
    assert after[first]["job"]["position"] == 1
    assert after[second]["job"]["status"] == "cancelled"
    assert after[second]["queue"] == {
        "status": "cancelled",
        "position": None,
        "enqueued_at": after[second]["queue"]["enqueued_at"],
        "finished_at": after[second]["queue"]["finished_at"],
    }
    assert after[third]["job"]["position"] == 2

    processed: list[str] = []
    QueueWorker(
        queue, lambda meeting_dir: processed.append(meeting_dir.name), lock_timeout_seconds=0
    ).recover_and_drain()
    assert [name.split("--")[1] for name in processed] == [first, third]
    final = _by_id(api)
    assert final[second]["queue"]["status"] == "cancelled"
    assert final[first]["queue"]["status"] == "completed"


def test_cancel_rejects_terminal_queue_records_and_leaves_running_claims_alone(tmp_path: Path):
    root = tmp_path / "meetings"
    api = _client(root)
    running_id = _upload(api, "2026-09-03")
    done_id = _upload(api, "2026-09-04")
    queue = DurableMeetingQueue(root)
    queue.enqueue(done_id)
    assert queue.claim_next() is not None
    assert queue.complete(done_id)
    queue.enqueue(running_id)
    assert queue.claim_next() is not None

    rejected = api.post(f"/api/meetings/{done_id}/cancel", headers=HEADERS)
    assert rejected.status_code == 409
    assert queue.get(done_id).status == "completed"  # type: ignore[union-attr]

    # Running work is not removed from the queue; it receives a cooperative stop request.
    running = api.post(f"/api/meetings/{running_id}/cancel", headers=HEADERS)
    assert running.status_code == 202
    assert running.json()["status"] == "cancellation_requested"
    assert queue.get(running_id).status == "running"  # type: ignore[union-attr]
    assert _by_id(api)[running_id]["job"]["status"] == "running"


def test_running_stage_takes_precedence_over_a_queued_record(tmp_path: Path):
    root = tmp_path / "meetings"
    meeting = root / "2026-09-03"
    manifest = PipelineManifest.model_validate(
        {
            "pipeline_version": "test",
            "meeting_dir": str(meeting),
            "meeting_date": "2026-09-03",
            "created_at": "2026-09-03T00:00:00Z",
            "updated_at": "2026-09-03T00:00:00Z",
            "source_filename": "meeting.webm",
            "source_sha256": "a" * 64,
        }
    )
    write_manifest(meeting / "manifest.json", manifest)
    api = _client(root)
    DurableMeetingQueue(root).enqueue("2026-09-03")
    assert api.get("/api/meetings/2026-09-03").json()["job"]["position"] == 1

    start_stage(manifest, "transcribe")
    write_manifest(meeting / "manifest.json", manifest)

    job = api.get("/api/meetings/2026-09-03").json()["job"]
    assert job["status"] == "running"
    assert job["stage"] == "transcribe"
