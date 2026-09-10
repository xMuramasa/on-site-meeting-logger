import shutil
from pathlib import Path

import pymupdf

from meeting_pipeline.config import load_config
from meeting_pipeline.models import CanonicalActa
from meeting_pipeline.rendering import render_documents
from meeting_pipeline.validation import (
    validate_pdf,
    validate_pdf_visuals,
    validate_rendered_semantics,
)

FIXTURE = Path(__file__).parent / "fixtures" / "valid-acta.json"
BASELINE_PDF = Path(
    "/Users/muramasa/Recordings/2026-08-31/Acta_Reunion_Semanal_2026-08-31.pdf"
)


def acta():
    return CanonicalActa.model_validate_json(FIXTURE.read_text())


def test_rendered_semantics_match(tmp_path):
    outputs = render_documents(acta(), tmp_path)
    checks = validate_rendered_semantics(acta(), outputs["markdown"], outputs["html"])
    assert all(check.ok for check in checks), [check.detail for check in checks]


def test_existing_approved_pdf_passes_layout_validation(tmp_path):
    if not BASELINE_PDF.is_file():
        return
    copy = tmp_path / "baseline.pdf"
    shutil.copy2(BASELINE_PDF, copy)
    checks = validate_pdf(copy, load_config(None).validation)
    failures = [check.detail for check in checks if check.required and not check.ok]
    assert not failures, failures


def test_visual_validation_rejects_weak_trailing_page(tmp_path):
    pdf = pymupdf.open()
    full_page = pdf.new_page(width=612, height=792)
    full_page.draw_rect(
        pymupdf.Rect(72, 72, 540, 700), color=None, fill=(0.7, 0.7, 0.7)
    )
    trailing_page = pdf.new_page(width=612, height=792)
    trailing_page.insert_text((72, 72), "[00:42:17]", fontsize=9)
    path = tmp_path / "weak-trailing.pdf"
    pdf.save(path)

    checks = validate_pdf_visuals(path, load_config(None).validation)

    assert {check.name for check in checks if not check.ok} == {"visual-no-weak-trailing-page"}


def test_visual_validation_rejects_weak_middle_page(tmp_path):
    pdf = pymupdf.open()
    for index, text in enumerate(("Opening content.", "[00:42:17]", "Closing content.")):
        page = pdf.new_page(width=612, height=792)
        if index != 1:
            page.draw_rect(pymupdf.Rect(72, 72, 540, 700), color=None, fill=(0.7, 0.7, 0.7))
        page.insert_textbox(pymupdf.Rect(72, 72, 540, 700), text, fontsize=11)
    path = tmp_path / "weak-middle.pdf"
    pdf.save(path)

    checks = validate_pdf_visuals(path, load_config(None).validation)

    assert "visual-no-awkward-pagination" in {check.name for check in checks if not check.ok}
