from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from meeting_pipeline.models import CanonicalActa, PipelineManifest
from meeting_pipeline.review import generate_review
from meeting_pipeline.web import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "valid-acta.json"


def client(
    tmp_path: Path,
    runner=lambda *_args, **_kwargs: None,
    static_dir: Path | None = None,
) -> TestClient:
    app = create_app(
        output_root=tmp_path / "meetings",
        csrf_token="test-csrf-token",
        runner=runner,
        static_dir=static_dir,
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
    assert calls == [(meeting, "validate")]


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
