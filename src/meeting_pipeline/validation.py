"""Semantic and PDF quality gates."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pdfplumber
from pypdf import PdfReader

from .config import Settings, ValidationSettings
from .errors import ValidationFailed
from .manifest import write_json_atomic
from .models import CanonicalActa, CheckResult, ValidationReport

# Chromium's PDF text layer emits typographic ligatures, so a literal "Confidencial"
# never matches the extracted "Conﬁdencial". Fold them before comparing phrases.
LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"})


def normalize_pdf_text(text: str) -> str:
    """Fold ligatures and collapse the whitespace a PDF text layer scatters around."""
    return re.sub(r"[ \t ]+", " ", text.translate(LIGATURES))


def validate_rendered_semantics(
    acta: CanonicalActa, markdown_path: Path, html_path: Path
) -> list[CheckResult]:
    checks: list[CheckResult] = []
    try:
        markdown = Path(markdown_path).read_text(encoding="utf-8")
        html = Path(html_path).read_text(encoding="utf-8")
    except OSError as exc:
        return [CheckResult(name="rendered-files", ok=False, detail=str(exc))]
    for semantic_id in acta.semantic_ids():
        checks.append(
            CheckResult(
                name=f"semantic-{semantic_id}",
                ok=semantic_id in markdown and f'data-semantic-id="{semantic_id}"' in html,
                detail=f"{semantic_id} must occur in Markdown and HTML",
            )
        )
    titles_ok = all(
        section.title in markdown and section.title in html for section in acta.sections
    )
    checks.append(
        CheckResult(name="section-parity", ok=titles_ok, detail="all sections in both formats")
    )
    external = any(
        token in html.lower() for token in ('src="http', "src='http", 'href="http', "href='http")
    )
    checks.append(
        CheckResult(name="no-external-assets", ok=not external, detail="HTML is self-contained")
    )
    return checks


def validate_pdf(path: Path, settings: ValidationSettings) -> list[CheckResult]:
    path = Path(path)
    if not path.is_file():
        return [CheckResult(name="pdf-exists", ok=False, detail=f"missing PDF: {path}")]
    checks = [CheckResult(name="pdf-exists", ok=path.stat().st_size > 100, detail=str(path))]
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        checks.append(CheckResult(name="pdf-readable", ok=False, detail=str(exc)))
        return checks
    checks.append(CheckResult(name="pdf-not-encrypted", ok=not reader.is_encrypted))
    checks.append(
        CheckResult(
            name="pdf-has-pages", ok=len(reader.pages) > 0, detail=f"pages={len(reader.pages)}"
        )
    )
    sizes = [(float(page.mediabox.width), float(page.mediabox.height)) for page in reader.pages]
    letter_ok = all(abs(width - 612) <= 1 and abs(height - 792) <= 1 for width, height in sizes)
    checks.append(
        CheckResult(
            name="letter-pages",
            ok=letter_ok or not settings.require_letter_pages,
            required=settings.require_letter_pages,
            detail=str(sizes),
        )
    )
    page_texts = [(page.extract_text() or "").strip() for page in reader.pages]
    total_text = "\n".join(page_texts)
    text_ok = len(total_text) >= settings.min_text_chars and all(page_texts)
    checks.append(
        CheckResult(
            name="text-layer",
            ok=text_ok or not settings.require_text_layer,
            required=settings.require_text_layer,
            detail=(
                f"characters={len(total_text)}; "
                f"blank_pages={sum(not value for value in page_texts)}"
            ),
        )
    )
    folded = normalize_pdf_text(total_text)
    for phrase in settings.required_phrases:
        checks.append(
            CheckResult(
                name=f"phrase-{phrase}",
                ok=normalize_pdf_text(phrase) in folded,
                detail=f"required phrase: {phrase}",
            )
        )
    trailing_ok = len(page_texts) <= 1 or len(page_texts[-1]) > settings.max_trailing_page_chars
    checks.append(
        CheckResult(
            name="no-tiny-trailing-page",
            ok=trailing_ok,
            detail=f"last_page_characters={len(page_texts[-1]) if page_texts else 0}",
        )
    )
    try:
        overflow = 0
        with pdfplumber.open(path) as document:
            for page in document.pages:
                width, height = float(page.width), float(page.height)
                for char in page.chars:
                    if (
                        float(char.get("x0", 0)) < -1
                        or float(char.get("x1", 0)) > width + 1
                        or float(char.get("top", 0)) < -1
                        or float(char.get("bottom", 0)) > height + 1
                    ):
                        overflow += 1
        checks.append(
            CheckResult(name="text-in-page-bounds", ok=overflow == 0, detail=f"overflow={overflow}")
        )
    except Exception as exc:
        checks.append(CheckResult(name="text-in-page-bounds", ok=False, detail=str(exc)))
    return checks


def validate_meeting(meeting_dir: Path, settings: Settings) -> ValidationReport:
    meeting_dir = Path(meeting_dir)
    approved_path = meeting_dir / "build" / "acta-approved.json"
    checks: list[CheckResult] = []
    try:
        acta = CanonicalActa.model_validate_json(approved_path.read_text(encoding="utf-8"))
        checks.append(CheckResult(name="approved-schema", ok=True))
    except Exception as exc:
        acta = None
        checks.append(CheckResult(name="approved-schema", ok=False, detail=str(exc)))
    if acta is not None:
        base = acta.meeting.slug()
        checks.extend(
            validate_rendered_semantics(
                acta, meeting_dir / f"{base}.md", meeting_dir / f"{base}.html"
            )
        )
        checks.extend(validate_pdf(meeting_dir / f"{base}.pdf", settings.validation))
    report = ValidationReport(
        meeting_dir=str(meeting_dir.resolve()), generated_at=datetime.now(UTC), checks=checks
    )
    write_json_atomic(
        meeting_dir / "build" / "validation-report.json", report.model_dump(mode="json")
    )
    return report


def require_valid(report: ValidationReport) -> None:
    if not report.ok:
        details = "; ".join(f"{item.name}: {item.detail}" for item in report.failures())
        raise ValidationFailed(f"meeting validation failed: {details}")
