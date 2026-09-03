import shutil
from pathlib import Path

from meeting_pipeline.config import load_config
from meeting_pipeline.models import CanonicalActa
from meeting_pipeline.rendering import render_documents
from meeting_pipeline.validation import validate_pdf, validate_rendered_semantics

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
