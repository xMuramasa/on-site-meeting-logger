import pytest

from meeting_pipeline.config import load_config
from meeting_pipeline.errors import PdfError
from meeting_pipeline.pdf import discover_chromium, export_pdf


def test_discover_chromium_uses_explicit_executable(tmp_path):
    chrome = tmp_path / "chrome"
    chrome.write_text("x")
    chrome.chmod(0o755)
    assert discover_chromium(str(chrome)) == chrome


def test_discover_chromium_rejects_bad_explicit_path(tmp_path):
    with pytest.raises(PdfError):
        discover_chromium(str(tmp_path / "missing"))


def test_export_pdf_invokes_chromium_without_shell(tmp_path, monkeypatch):
    chrome = tmp_path / "chrome"
    chrome.write_text("x")
    chrome.chmod(0o755)
    html = tmp_path / "x.html"
    html.write_text("<html>x</html>")
    pdf = tmp_path / "x.pdf"
    seen = {}

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        pdf.write_bytes(b"%PDF-fake")
        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)
    settings = load_config(None).pdf.model_copy(update={"chromium_path": str(chrome)})
    export_pdf(html, pdf, settings)
    assert seen["args"][0] == str(chrome)
    assert seen["kwargs"].get("shell") is not True
    assert pdf.is_file()
