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
