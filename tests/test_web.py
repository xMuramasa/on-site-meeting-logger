from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from meeting_pipeline.manifest import complete_stage, fail_stage, start_stage, write_manifest
from meeting_pipeline.models import CanonicalActa, PipelineManifest
from meeting_pipeline.readiness import ReadinessCheck, ReadinessReport
from meeting_pipeline.review import generate_review
from meeting_pipeline.web import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "valid-acta.json"


def client(
    tmp_path: Path,
    runner=lambda *_args, **_kwargs: None,
    static_dir: Path | None = None,
    readiness=lambda: ReadinessReport(checks=[]),
) -> TestClient:
    app = create_app(
        output_root=tmp_path / "meetings",
        csrf_token="test-csrf-token",
        runner=runner,
        static_dir=static_dir,
        readiness=readiness,
    )
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_built_frontend_is_served_without_remote_assets(tmp_path):
    static = tmp_path / "web"
    static.mkdir()
    (static / "index.html").write_text('<script type="module" src="/assets/app.js"></script>')
    (static / "assets").mkdir()
    (static / "assets" / "app.js").write_text("document.body.dataset.ready = 'true'")

    api = client(tmp_path, static_dir=static)
    assert api.get("/").status_code == 200
    script = api.get("/assets/app.js")
    assert script.status_code == 200
    assert "dataset.ready" in script.text


def test_repository_frontend_bundle_is_wired_to_root(tmp_path):
    api = client(tmp_path)
    page = api.get("/")

    assert page.status_code == 200
    assert "Meeting Studio" in page.text
    assert ("My" + "nu Meeting Studio") not in page.text
    assert "https://" not in page.text


def test_bootstrap_is_loopback_only_and_returns_csrf(tmp_path):
    response = client(tmp_path).get("/api/bootstrap")

    assert response.status_code == 200
    assert response.json()["csrf_token"] == "test-csrf-token"
    assert response.headers["content-security-policy"].startswith("default-src 'self'")
    assert response.headers["x-content-type-options"] == "nosniff"

    rejected = client(tmp_path).get("/api/bootstrap", headers={"host": "evil.example"})
    assert rejected.status_code == 400


def test_upload_is_rejected_before_ingest_when_readiness_fails(tmp_path):
    report = ReadinessReport(
        checks=[ReadinessCheck(name="model-endpoint", ok=False, detail="connection refused")]
    )
    api = client(tmp_path, readiness=lambda: report)

    bootstrap = api.get("/api/bootstrap")
    response = api.post(
        "/api/meetings",
        headers={"origin": "http://127.0.0.1:8765", "x-csrf-token": "test-csrf-token"},
        data={"meeting_date": "2026-09-03"},
        files={"audio": ("meeting.m4a", b"audio", "audio/mp4")},
    )

    assert bootstrap.json()["readiness"] == report.model_dump(mode="json")
    assert response.status_code == 503
    assert response.json()["detail"] == "pipeline is not ready: model-endpoint: connection refused"
    assert not (tmp_path / "meetings" / "2026-09-03").exists()


def test_upload_accepts_browser_recording_and_starts_draft(tmp_path):
    calls = []

    def runner(meeting_dir, **kwargs):
        calls.append((Path(meeting_dir), kwargs["until"]))

    api = client(tmp_path, runner=runner)
    response = api.post(
        "/api/meetings",
        headers={"origin": "http://127.0.0.1:8765", "x-csrf-token": "test-csrf-token"},
        data={"meeting_date": "2026-09-03"},
        files={"audio": ("recording.webm", b"browser-audio", "audio/webm")},
    )

    assert response.status_code == 202
    meeting_dir = tmp_path / "meetings" / "2026-09-03"
    manifest = PipelineManifest.model_validate_json((meeting_dir / "manifest.json").read_text())
    assert manifest.meeting_date == date(2026, 9, 3)
    assert (meeting_dir / "source" / "meeting.webm").read_bytes() == b"browser-audio"
    assert calls == [(meeting_dir, "generate_review")]


def test_meeting_exposes_local_quiet_audio_warning_data(tmp_path):
    meeting = tmp_path / "meetings" / "2026-09-03"
    build = meeting / "build"
    build.mkdir(parents=True)
    (build / "audio-levels.json").write_text(
        '{"scanned_seconds":120,"mean_db":-47,"max_db":-31,"classification":"quiet"}'
    )

    response = client(tmp_path).get("/api/meetings/2026-09-03")

    assert response.status_code == 200
    assert response.json()["audio_analysis"]["classification"] == "quiet"


def test_mutations_require_trusted_origin_and_csrf(tmp_path):
    api = client(tmp_path)
    request = {
        "data": {"meeting_date": "2026-09-03"},
        "files": {"audio": ("meeting.m4a", b"audio", "audio/mp4")},
    }
    assert api.post("/api/meetings", **request).status_code == 403
    assert (
        api.post(
            "/api/meetings",
            headers={"origin": "https://evil.example", "x-csrf-token": "test-csrf-token"},
            **request,
        ).status_code
        == 403
    )


def test_custom_port_allows_only_its_configured_loopback_origin(tmp_path):
    report = ReadinessReport(
        checks=[ReadinessCheck(name="model-endpoint", ok=False, detail="connection refused")]
    )
    api = TestClient(
        create_app(
            output_root=tmp_path / "meetings",
            csrf_token="test-csrf-token",
            allowed_origins={"http://127.0.0.1:9123", "http://localhost:9123"},
            readiness=lambda: report,
        ),
        base_url="http://127.0.0.1:9123",
    )
    request = {
        "data": {"meeting_date": "2026-09-03"},
        "files": {"audio": ("meeting.m4a", b"audio", "audio/mp4")},
    }

    trusted = api.post(
        "/api/meetings",
        headers={"origin": "http://127.0.0.1:9123", "x-csrf-token": "test-csrf-token"},
        **request,
    )
    hostile = api.post(
        "/api/meetings",
        headers={"origin": "http://127.0.0.1:9999", "x-csrf-token": "test-csrf-token"},
        **request,
    )

    assert trusted.status_code == 503
    assert hostile.status_code == 403
    assert hostile.json() == {"detail": "untrusted origin"}


def test_restart_rejects_the_previous_csrf_token_and_issues_a_new_one(tmp_path):
    old = client(tmp_path)
    old_token = old.get("/api/bootstrap").json()["csrf_token"]
    restarted = TestClient(
        create_app(
            output_root=tmp_path / "meetings",
            csrf_token="after-restart",
            readiness=lambda: ReadinessReport(checks=[]),
        ),
        base_url="http://127.0.0.1:8765",
    )

    stale = restarted.post(
        "/api/meetings",
        headers={"origin": "http://127.0.0.1:8765", "x-csrf-token": old_token},
        data={"meeting_date": "2026-09-03"},
        files={"audio": ("meeting.m4a", b"audio", "audio/mp4")},
    )

    assert stale.status_code == 403
    assert stale.json() == {"detail": "invalid CSRF token"}
    assert restarted.get("/api/bootstrap").json()["csrf_token"] == "after-restart"


def test_review_round_trip_and_finalize(tmp_path):
    meeting = tmp_path / "meetings" / "2026-09-03"
    meeting.mkdir(parents=True)
    review = {
        "participants": [],
        "proper_nouns": {},
        "owners": {},
        "relative_date_actions": [],
        "quality_warnings": [],
        "approve_for_final_render": False,
    }
    calls = []
    api = client(tmp_path, runner=lambda path, **kwargs: calls.append((Path(path), kwargs["until"])))
    headers = {"origin": "http://127.0.0.1:8765", "x-csrf-token": "test-csrf-token"}

    saved = api.put("/api/meetings/2026-09-03/review", headers=headers, json=review)
    assert saved.status_code == 200
    assert api.get("/api/meetings/2026-09-03").json()["review"] == review

    blocked = api.post("/api/meetings/2026-09-03/finalize", headers=headers)
    assert blocked.status_code == 409

    review["approve_for_final_render"] = True
    api.put("/api/meetings/2026-09-03/review", headers=headers, json=review)
    accepted = api.post("/api/meetings/2026-09-03/finalize", headers=headers)
    assert accepted.status_code == 202
    review["proper_nouns"] = {"Obvio": "Obvio Health"}
    saved_edit = api.put("/api/meetings/2026-09-03/review", headers=headers, json=review)
    second_finalize = api.post("/api/meetings/2026-09-03/finalize", headers=headers)

    assert saved_edit.status_code == 200
    assert second_finalize.status_code == 202
    assert calls == [(meeting, "validate"), (meeting, "validate")]


def test_meeting_api_exposes_relative_date_review_context(tmp_path):
    meeting = tmp_path / "meetings" / "2026-09-03"
    generate_review(CanonicalActa.model_validate_json(FIXTURE.read_text()), meeting / "review.yaml")

    detail = client(tmp_path).get("/api/meetings/2026-09-03").json()

    assert detail["review"]["relative_date_actions"] == [
        {
            "action_id": "A-2",
            "action_text": "Realizar el segundo contacto con la Universidad de los Andes y coordinar con ANID.",
            "due_expression": "esta semana",
            "resolved_date": None,
        },
        {
            "action_id": "A-6",
            "action_text": "Presentar y revisar las pantallas de alta fidelidad.",
            "due_expression": "esta semana",
            "resolved_date": None,
        },
    ]


def test_meeting_api_derives_failed_job_from_durable_stage_state(tmp_path):
    meeting = tmp_path / "meetings" / "2026-09-03"
    source = meeting / "source" / "meeting.webm"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
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
    start_stage(manifest, "transcribe")
    fail_stage(
        manifest,
        "transcribe",
        "AUDIO_DECODE_FAILED",
        error_code="AUDIO_DECODE_FAILED",
        retryable=False,
    )
    write_manifest(meeting / "manifest.json", manifest)

    detail = client(tmp_path).get("/api/meetings/2026-09-03").json()

    assert detail["job"] == {
        "status": "failed",
        "stage": "transcribe",
        "error_code": "AUDIO_DECODE_FAILED",
        "retryable": False,
    }


def test_app_restart_marks_running_job_as_interrupted_with_safe_recovery(tmp_path):
    meeting = tmp_path / "meetings" / "2026-09-03"
    source = meeting / "source" / "meeting.webm"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
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
    start_stage(manifest, "consolidate")
    write_manifest(meeting / "manifest.json", manifest)

    detail = client(tmp_path).get("/api/meetings/2026-09-03").json()

    assert detail["job"] == {
        "status": "failed",
        "stage": "consolidate",
        "error_code": "JOB_INTERRUPTED",
        "retryable": True,
    }


def test_restart_rejects_unchanged_silent_input(tmp_path):
    meeting = tmp_path / "meetings" / "2026-09-03"
    source = meeting / "source" / "meeting.webm"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
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
    fail_stage(
        manifest,
        "transcribe",
        "NO_SPEECH",
        error_code="NO_SPEECH",
        retryable=False,
    )
    write_manifest(meeting / "manifest.json", manifest)
    api = client(tmp_path)
    headers = {"origin": "http://127.0.0.1:8765", "x-csrf-token": "test-csrf-token"}

    response = api.post("/api/meetings/2026-09-03/restart", headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "upload audio with audible speech before retrying"


def test_cancel_and_restart_are_durable_and_resume_in_background(tmp_path):
    meeting = tmp_path / "meetings" / "2026-09-03"
    source = meeting / "source" / "meeting.webm"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
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
    calls = []
    api = client(tmp_path, runner=lambda path, **kwargs: calls.append((Path(path), kwargs["until"])))
    headers = {"origin": "http://127.0.0.1:8765", "x-csrf-token": "test-csrf-token"}

    assert api.post("/api/meetings/2026-09-03/cancel", headers=headers).status_code == 202
    assert (meeting / "build" / ".cancel-requested").is_file()
    assert api.post("/api/meetings/2026-09-03/restart", headers=headers).status_code == 202
    assert not (meeting / "build" / ".cancel-requested").exists()
    assert calls == [(meeting, "generate_review")]


def test_meeting_artifacts_are_manifest_backed_and_final_only_after_validation(tmp_path):
    meeting = tmp_path / "meetings" / "2026-09-03"
    meeting.mkdir(parents=True)
    transcript = meeting / "manual-transcript.md"
    digest = meeting / "renamed-digest.md"
    markdown = meeting / "renamed-minutes.md"
    html = meeting / "renamed-minutes.html"
    pdf = meeting / "renamed-minutes.pdf"
    approved = meeting / "build" / "acta-approved.json"
    supporting = meeting / "review.yaml"
    previous_pdf = meeting / "previous-acta.pdf"
    approved.parent.mkdir()
    for path in (transcript, digest, markdown, html, pdf, approved, supporting, previous_pdf):
        path.write_text(path.name)
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
    complete_stage(manifest, "transcribe", "transcribe", {"markdown": transcript})
    complete_stage(manifest, "approve", "approve", {"approved": approved})
    complete_stage(manifest, "generate_review", "review", {"review": supporting})
    complete_stage(manifest, "render", "render", {"markdown": markdown, "html": html, "digest": digest})
    complete_stage(manifest, "export_pdf", "pdf", {"pdf": pdf})
    write_manifest(meeting / "manifest.json", manifest)

    draft_detail = client(tmp_path).get("/api/meetings/2026-09-03").json()

    assert draft_detail["artifacts"] == [
        {"name": "manual-transcript.md", "role": "transcript", "format": "Markdown", "final": False},
        {"name": "renamed-digest.md", "role": "digest", "format": "Markdown", "final": False},
        {"name": "renamed-minutes.md", "role": "minutes", "format": "Markdown", "final": False},
        {"name": "renamed-minutes.html", "role": "minutes", "format": "HTML", "final": False},
        {"name": "renamed-minutes.pdf", "role": "minutes", "format": "PDF", "final": False},
        {"name": "review.yaml", "role": "supporting", "format": "YAML", "final": False},
    ]
    assert client(tmp_path).get("/api/meetings/2026-09-03/files/previous-acta.pdf").status_code == 404

    report = meeting / "build" / "validation-report.json"
    report.parent.mkdir(exist_ok=True)
    report.write_text("{}")
    complete_stage(manifest, "validate", "validate", {"report": report})
    write_manifest(meeting / "manifest.json", manifest)

    final_detail = client(tmp_path).get("/api/meetings/2026-09-03").json()

    assert [artifact["final"] for artifact in final_detail["artifacts"]] == [False, False, True, True, True, False]
    assert client(tmp_path).get("/api/meetings/2026-09-03/files/renamed-minutes.pdf").status_code == 200

    changed_review = {
        "participants": [],
        "proper_nouns": {"antes": "después"},
        "owners": {},
        "relative_date_actions": [],
        "quality_warnings": [],
        "approve_for_final_render": False,
    }
    headers = {"origin": "http://127.0.0.1:8765", "x-csrf-token": "test-csrf-token"}
    assert client(tmp_path).put(
        "/api/meetings/2026-09-03/review", headers=headers, json=changed_review
    ).status_code == 200
    edited_detail = client(tmp_path).get("/api/meetings/2026-09-03").json()
    assert not any(artifact["final"] for artifact in edited_detail["artifacts"])
    assert not any(artifact["role"] == "minutes" for artifact in edited_detail["artifacts"])

    report.unlink()
    stale_detail = client(tmp_path).get("/api/meetings/2026-09-03").json()
    assert not any(artifact["final"] for artifact in stale_detail["artifacts"])


def test_restart_refuses_a_legacy_directory_without_processing_state(tmp_path):
    meeting = tmp_path / "meetings" / "2026-09-03"
    meeting.mkdir(parents=True)
    (meeting / "review.yaml").write_text("approve_for_final_render: false\n")
    api = client(tmp_path)
    headers = {"origin": "http://127.0.0.1:8765", "x-csrf-token": "test-csrf-token"}

    response = api.post("/api/meetings/2026-09-03/restart", headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "this meeting has no processing state to resume"


def test_restart_does_not_refinalize_a_meeting_whose_review_is_unapproved(tmp_path):
    meeting = tmp_path / "meetings" / "2026-09-03"
    source = meeting / "source" / "meeting.webm"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
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
    for stage in ("generate_review", "approve", "render", "export_pdf", "validate"):
        complete_stage(manifest, stage, stage, {})
    write_manifest(meeting / "manifest.json", manifest)
    calls = []
    api = client(tmp_path, runner=lambda path, **kwargs: calls.append((Path(path), kwargs["until"])))
    headers = {"origin": "http://127.0.0.1:8765", "x-csrf-token": "test-csrf-token"}
    unapproved = {
        "participants": [],
        "proper_nouns": {},
        "owners": {},
        "relative_date_actions": [],
        "quality_warnings": [],
        "approve_for_final_render": False,
    }
    assert api.put(
        "/api/meetings/2026-09-03/review", headers=headers, json=unapproved
    ).status_code == 200
    assert api.post("/api/meetings/2026-09-03/finalize", headers=headers).status_code == 409

    assert api.post("/api/meetings/2026-09-03/restart", headers=headers).status_code == 202
    assert calls == [(meeting, "generate_review")]

    approved = {**unapproved, "approve_for_final_render": True}
    assert api.put(
        "/api/meetings/2026-09-03/review", headers=headers, json=approved
    ).status_code == 200
    assert api.post("/api/meetings/2026-09-03/restart", headers=headers).status_code == 202
    assert calls[-1] == (meeting, "validate")
