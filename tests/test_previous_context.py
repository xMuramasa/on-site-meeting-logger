from pathlib import Path

import yaml

from meeting_pipeline.config import load_config
from meeting_pipeline.errors import PreviousContextError
from meeting_pipeline.previous_context import extract_previous_context


def write_pdf(path, text):
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    # pypdf cannot add text simply; extraction is injected in unit tests instead.
    with path.open("wb") as handle:
        writer.write(handle)


def test_extract_previous_context_labels_roster_and_terms(tmp_path, monkeypatch):
    pdf = tmp_path / "prior.pdf"
    write_pdf(pdf, "")
    overlay = tmp_path / "config.yaml"
    overlay.write_text(
        yaml.safe_dump(
            {
                "glossary": {"product name": "Product Name"},
                "reference_participants": [
                    {"name": "Alex", "email": "alex@example.com"}
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "meeting_pipeline.previous_context._extract_pages",
        lambda path: ["Alex alex@example.com Product Name Acuerdos y próximos pasos"],
    )
    result = extract_previous_context(pdf, load_config(overlay))
    assert result["page_count"] == 1
    assert result["participants"][0]["attendance"] == "unconfirmed"
    assert "Product Name" in result["canonical_terms_found"]
    assert result["sha256"]


def test_empty_pdf_requires_ocr(tmp_path, monkeypatch):
    import pytest

    pdf = tmp_path / "prior.pdf"
    write_pdf(pdf, "")
    monkeypatch.setattr("meeting_pipeline.previous_context._extract_pages", lambda path: [""])
    with pytest.raises(PreviousContextError, match="OCR"):
        extract_previous_context(pdf, load_config(None))


def test_extract_previous_context_accepts_markdown_and_html(tmp_path):
    markdown = tmp_path / "prior.md"
    markdown.write_text("# Acuerdos\n\nAlex realizará el seguimiento.", encoding="utf-8")
    html = tmp_path / "prior.html"
    html.write_text("<h1>Acuerdos</h1><p>Alex realizará el seguimiento.</p>", encoding="utf-8")

    for prior, source_format in ((markdown, "text/markdown"), (html, "text/html")):
        result = extract_previous_context(prior, load_config(None))
        assert result["source"] == str(prior.resolve())
        assert result["sha256"]
        assert result["format"] == source_format
        assert result["date"] is None
        assert "Alex realizará el seguimiento." in result["text"]
        assert result["provenance"] == "previous_acta"


def test_extract_previous_context_prefers_canonical_json_metadata(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "valid-acta.json"
    prior = tmp_path / "prior.json"
    prior.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")

    result = extract_previous_context(prior, load_config(None))

    assert result["format"] == "application/json"
    assert result["date"] == "2026-08-31"
    assert result["canonical_acta"]["meeting"]["title"] == "Reunión semanal"
    assert result["provenance"] == "previous_acta"
