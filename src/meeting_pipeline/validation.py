"""Semantic and PDF quality gates."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pdfplumber
import pymupdf
from PIL import Image, ImageDraw
from pypdf import PdfReader

from .config import Settings, ValidationSettings
from .errors import ValidationFailed
from .manifest import write_json_atomic
from .models import CanonicalActa, CheckResult, ValidationReport
from .rendering import artifact_stem

# Chromium's PDF text layer emits typographic ligatures, so a literal "Confidencial"
# never matches the extracted "Conﬁdencial". Fold them before comparing phrases.
LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"})
TIMESTAMP_RE = re.compile(
    r"\[(?:\d{2}:){2}\d{2}[–-](?:\d{2}:){2}\d{2}"
    r"(?:;\s*(?:\d{2}:){2}\d{2}[–-](?:\d{2}:){2}\d{2})*\]"
)


def normalize_pdf_text(text: str) -> str:
    """Fold ligatures and collapse the whitespace a PDF text layer scatters around."""
    return re.sub(r"[ \t ]+", " ", text.translate(LIGATURES))


def _ink_ratio(page: pymupdf.Page, dpi: int) -> float:
    scale = dpi / 72
    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY, alpha=False
    )
    samples = memoryview(pixmap.samples)
    return sum(value < 245 for value in samples) / len(samples)


def _line_records(page: pymupdf.Page) -> list[tuple[int, int, str, pymupdf.Rect]]:
    records: list[tuple[int, int, str, pymupdf.Rect]] = []
    for block_index, block in enumerate(page.get_text("dict")["blocks"]):
        for line_index, line in enumerate(block.get("lines", [])):
            text = "".join(span["text"] for span in line["spans"])
            records.append((block_index, line_index, text, pymupdf.Rect(line["bbox"])))
    return records


def _overlap_count(lines: list[tuple[int, int, str, pymupdf.Rect]]) -> int:
    overlaps = 0
    for index, (block, line, _, rect) in enumerate(lines):
        for other_block, other_line, _, other_rect in lines[index + 1 :]:
            if (block, line) == (other_block, other_line):
                continue
            intersection = rect & other_rect
            if intersection.is_valid and intersection.get_area() > 4:
                overlaps += 1
    return overlaps


def _write_contact_sheet(document: pymupdf.Document, dpi: int, output_path: Path) -> None:
    thumbnails: list[Image.Image] = []
    scale = dpi / 72
    for page in document:
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        image.thumbnail((240, 320))
        thumbnails.append(image)
    columns = min(3, len(thumbnails))
    rows = (len(thumbnails) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * 260, rows * 350), "#e5e7eb")
    draw = ImageDraw.Draw(sheet)
    for index, image in enumerate(thumbnails):
        x = (index % columns) * 260 + 10
        y = (index // columns) * 350 + 22
        sheet.paste(image, (x, y))
        draw.text((x, y - 16), f"Page {index + 1}", fill="#111827")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, "PNG")


def validate_pdf_visuals(
    path: Path, settings: ValidationSettings, contact_sheet_path: Path | None = None
) -> list[CheckResult]:
    """Rasterize pages and reject visual defects that text-layer checks cannot see."""
    try:
        document = pymupdf.open(path)
    except Exception as exc:
        return [CheckResult(name="visual-rasterization", ok=False, detail=str(exc))]
    try:
        ink_ratios = [_ink_ratio(page, settings.visual_dpi) for page in document]
        awkward_pages = [
            index + 1
            for index, ink_ratio in enumerate(ink_ratios[:-1])
            if ink_ratio < settings.max_trailing_page_ink_ratio
        ]
        lines_by_page = [_line_records(page) for page in document]
        overlap_count = sum(_overlap_count(lines) for lines in lines_by_page)
        stranded_timestamps = sum(
            1
            for lines in lines_by_page
            for _, _, text, _ in lines
            if TIMESTAMP_RE.search(text) and len(TIMESTAMP_RE.sub("", text).strip()) < 12
        )
        broken_tables = 0
        with pdfplumber.open(path) as pdf_document:
            for page in pdf_document.pages:
                for table in page.extract_tables():
                    broken_tables += sum(
                        cell is None or not cell.strip() for row in table for cell in row
                    )
        checks = [
            CheckResult(
                name="visual-rasterization",
                ok=True,
                detail=(
                    f"pages={len(ink_ratios)}; dpi={settings.visual_dpi}; "
                    f"ink_ratios={ink_ratios}"
                ),
            ),
            CheckResult(
                name="visual-no-weak-trailing-page",
                ok=len(ink_ratios) <= 1 or ink_ratios[-1] >= settings.max_trailing_page_ink_ratio,
                detail=(
                    f"last_page_ink_ratio={ink_ratios[-1] if ink_ratios else 0:.5f}; "
                    f"minimum={settings.max_trailing_page_ink_ratio:.5f}"
                ),
            ),
            CheckResult(
                name="visual-no-awkward-pagination",
                ok=not awkward_pages,
                detail=f"nearly_blank_nonfinal_pages={awkward_pages}",
            ),
            CheckResult(
                name="visual-no-overlap",
                ok=overlap_count <= settings.max_visual_overlap_count,
                detail=f"overlaps={overlap_count}; maximum={settings.max_visual_overlap_count}",
            ),
            CheckResult(
                name="visual-no-stranded-timestamps",
                ok=stranded_timestamps == 0,
                detail=f"stranded_timestamps={stranded_timestamps}",
            ),
            CheckResult(
                name="visual-tables-intact",
                ok=broken_tables == 0,
                detail=f"empty_or_missing_cells={broken_tables}",
            ),
        ]
        if contact_sheet_path is not None:
            _write_contact_sheet(document, settings.visual_dpi, contact_sheet_path)
            checks.append(
                CheckResult(name="visual-contact-sheet", ok=True, detail=str(contact_sheet_path))
            )
        return checks
    except Exception as exc:
        return [CheckResult(name="visual-rasterization", ok=False, detail=str(exc))]
    finally:
        document.close()


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
    visual_contact_sheet = path.parent / "build" / f"{path.stem}-contact-sheet.png"
    visual_checks = validate_pdf_visuals(path, settings, visual_contact_sheet)
    for check in visual_checks:
        check.required = settings.require_visual_validation
    checks.extend(visual_checks)
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
        base = artifact_stem(acta, settings.branding)
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
