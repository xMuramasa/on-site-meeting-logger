from __future__ import annotations

from types import SimpleNamespace

import httpx

from meeting_pipeline.config import load_config
from meeting_pipeline.readiness import ReadinessReport, check_readiness


def test_check_readiness_reports_every_required_dependency(tmp_path):
    settings = load_config(None).model_copy(
        update={
            "reasoning": load_config(None).reasoning.model_copy(
                update={"base_url": "http://model.test/v1", "model": "local-model"}
            )
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("http://model.test/v1/models")
        return httpx.Response(200, json={"data": [{"id": "local-model"}]})

    def fake_exporter(html, pdf, _settings):
        assert html.read_text(encoding="utf-8") == "<p>Meeting Pipeline readiness check</p>"
        pdf.write_bytes(b"%PDF-fake")
        return pdf

    report = check_readiness(
        settings,
        tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        model_loader=lambda _settings: object(),
        command_locator=lambda name: f"/usr/local/bin/{name}",
        pdf_exporter=fake_exporter,
        disk_usage=lambda _path: SimpleNamespace(free=10 * 1024**3),
    )

    assert report.ok
    assert [check.name for check in report.checks] == [
        "model-endpoint",
        "model-identity",
        "transcription-model",
        "ffmpeg",
        "output-permissions",
        "chromium-pdf",
        "free-disk-space",
    ]


def test_check_readiness_keeps_independent_failures_in_one_report(tmp_path):
    settings = load_config(None)

    report = check_readiness(
        settings,
        tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(503))),
        model_loader=lambda _settings: (_ for _ in ()).throw(RuntimeError("model unavailable")),
        command_locator=lambda _name: None,
        pdf_exporter=lambda *_args: (_ for _ in ()).throw(RuntimeError("browser unavailable")),
        disk_usage=lambda _path: SimpleNamespace(free=0),
    )

    assert not report.ok
    assert {check.name for check in report.checks if not check.ok} == {
        "model-endpoint",
        "model-identity",
        "transcription-model",
        "ffmpeg",
        "chromium-pdf",
        "free-disk-space",
    }


def test_readiness_report_serializes_for_api_clients():
    report = ReadinessReport(checks=[])

    assert report.model_dump(mode="json") == {"ok": True, "checks": []}


def test_check_readiness_reports_an_unusable_output_root_without_crashing(tmp_path):
    output_root = tmp_path / "not-a-directory"
    output_root.write_text("file", encoding="utf-8")

    report = check_readiness(
        load_config(None),
        output_root,
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(503))),
        model_loader=lambda _settings: object(),
        command_locator=lambda name: f"/usr/local/bin/{name}",
        pdf_exporter=lambda *_args: (_ for _ in ()).throw(AssertionError("must not run")),
        disk_usage=lambda _path: SimpleNamespace(free=10 * 1024**3),
    )

    checks = {check.name: check for check in report.checks}
    assert not checks["output-permissions"].ok
    assert checks["chromium-pdf"].detail.startswith("output directory unavailable")
