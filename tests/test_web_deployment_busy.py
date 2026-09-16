"""The studio serializes expensive work across meetings and says so honestly."""

from pathlib import Path

from fastapi.testclient import TestClient

from meeting_pipeline.errors import PipelineBusyError
from meeting_pipeline.manifest import complete_stage, meeting_lock, start_stage, write_manifest
from meeting_pipeline.models import PipelineManifest
from meeting_pipeline.readiness import ReadinessReport
from meeting_pipeline.web import create_app

HEADERS = {"origin": "http://127.0.0.1:8765", "x-csrf-token": "test-csrf-token"}


def client(tmp_path: Path, runner=lambda *_a, **_k: None) -> TestClient:
    app = create_app(
        output_root=tmp_path / "meetings",
        csrf_token="test-csrf-token",
        runner=runner,
        readiness=lambda: ReadinessReport(checks=[]),
    )
    return TestClient(app, base_url="http://127.0.0.1:8765")


def meeting_with_manifest(tmp_path: Path, day: str) -> Path:
    meeting = tmp_path / "meetings" / day
    source = meeting / "source" / "meeting.webm"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
    manifest = PipelineManifest.model_validate(
        {
            "pipeline_version": "test",
            "meeting_dir": str(meeting),
            "meeting_date": day,
            "created_at": f"{day}T00:00:00Z",
            "updated_at": f"{day}T00:00:00Z",
            "source_filename": "meeting.webm",
            "source_sha256": "a" * 64,
        }
    )
    write_manifest(meeting / "manifest.json", manifest)
    return meeting


def mark_running(meeting: Path, stage: str = "transcribe") -> None:
    manifest = PipelineManifest.model_validate_json(
        (meeting / "manifest.json").read_text(encoding="utf-8")
    )
    start_stage(manifest, stage)
    write_manifest(meeting / "manifest.json", manifest)


def test_upload_is_refused_while_another_meeting_is_processing(tmp_path):
    busy = meeting_with_manifest(tmp_path, "2026-09-02")
    api = client(tmp_path)
    mark_running(busy)

    response = api.post(
        "/api/meetings",
        headers=HEADERS,
        data={"meeting_date": "2026-09-03"},
        files={"audio": ("meeting.m4a", b"audio", "audio/mp4")},
    )

    assert response.status_code == 409
    assert "2026-09-02" in response.json()["detail"]
    assert not (tmp_path / "meetings" / "2026-09-03").exists()


def test_finalize_and_restart_are_refused_while_another_meeting_is_processing(tmp_path):
    busy = meeting_with_manifest(tmp_path, "2026-09-02")
    other = meeting_with_manifest(tmp_path, "2026-09-03")
    (other / "review.yaml").write_text("approve_for_final_render: true\n", encoding="utf-8")
    api = client(tmp_path)
    mark_running(busy)

    finalize = api.post("/api/meetings/2026-09-03/finalize", headers=HEADERS)
    restart = api.post("/api/meetings/2026-09-03/restart", headers=HEADERS)

    assert finalize.status_code == 409
    assert restart.status_code == 409
    assert "2026-09-02" in finalize.json()["detail"]


def test_a_job_that_loses_the_race_is_reported_as_blocked_not_failed(tmp_path):
    meeting = meeting_with_manifest(tmp_path, "2026-09-03")
    api = client(
        tmp_path,
        runner=lambda *_a, **_k: (_ for _ in ()).throw(
            PipelineBusyError("another meeting is using this deployment")
        ),
    )

    accepted = api.post(
        "/api/meetings/2026-09-03/restart",
        headers=HEADERS,
    )
    detail = api.get("/api/meetings/2026-09-03").json()

    assert accepted.status_code == 202
    assert detail["job"]["status"] == "blocked"
    # Nothing has completed yet, so the wait is recorded at the very first stage.
    assert detail["job"]["stage"] == "inspect"
    stages = PipelineManifest.model_validate_json(
        (meeting / "manifest.json").read_text(encoding="utf-8")
    ).stages
    assert all(state.status != "failed" for state in stages.values())


def test_blocking_marks_the_first_pending_stage_and_keeps_completed_artifacts(tmp_path):
    meeting = meeting_with_manifest(tmp_path, "2026-09-03")
    transcript = meeting / "transcript.md"
    transcript.write_text("# Transcript\n", encoding="utf-8")
    manifest = PipelineManifest.model_validate_json(
        (meeting / "manifest.json").read_text(encoding="utf-8")
    )
    for stage in ("inspect", "chunk", "extract_previous_context"):
        complete_stage(manifest, stage, stage, {})
    complete_stage(manifest, "transcribe", "transcribe", {"markdown": transcript})
    write_manifest(meeting / "manifest.json", manifest)
    api = client(
        tmp_path,
        runner=lambda *_a, **_k: (_ for _ in ()).throw(PipelineBusyError("busy")),
    )

    api.post("/api/meetings/2026-09-03/restart", headers=HEADERS)
    detail = api.get("/api/meetings/2026-09-03").json()

    # `consolidate` is the first stage that has not completed; nothing already done is lost.
    assert detail["job"] == {"status": "blocked", "stage": "consolidate", "retryable": True}
    stages = PipelineManifest.model_validate_json(
        (meeting / "manifest.json").read_text(encoding="utf-8")
    ).stages
    assert stages["transcribe"].status == "complete"
    assert stages["transcribe"].artifacts == {"markdown": str(transcript.resolve())}
    assert any(item["name"] == "transcript.md" for item in detail["artifacts"])


def test_a_blocked_meeting_can_be_restarted_once_the_deployment_is_free(tmp_path):
    meeting_with_manifest(tmp_path, "2026-09-03")
    busy_api = client(
        tmp_path,
        runner=lambda *_a, **_k: (_ for _ in ()).throw(PipelineBusyError("busy")),
    )
    busy_api.post("/api/meetings/2026-09-03/restart", headers=HEADERS)

    calls = []
    free_api = client(tmp_path, runner=lambda path, **kw: calls.append((Path(path), kw["until"])))
    assert free_api.get("/api/meetings/2026-09-03").json()["job"]["status"] == "blocked"

    response = free_api.post("/api/meetings/2026-09-03/restart", headers=HEADERS)

    assert response.status_code == 202
    assert calls == [(tmp_path / "meetings" / "2026-09-03", "generate_review")]


def test_busy_handler_cannot_write_state_while_another_runner_owns_meeting_lock(tmp_path):
    meeting = meeting_with_manifest(tmp_path, "2026-09-03")
    api = client(
        tmp_path,
        runner=lambda *_a, **_k: (_ for _ in ()).throw(PipelineBusyError("busy")),
    )
    manifest_path = meeting / "manifest.json"
    before = manifest_path.read_bytes()
    with meeting_lock(meeting):
        response = api.post("/api/meetings/2026-09-03/restart", headers=HEADERS)
    assert response.status_code == 202
    assert manifest_path.read_bytes() == before
