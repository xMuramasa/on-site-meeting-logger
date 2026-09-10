"""Extract prior-acta text while preserving its provenance."""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path

from pypdf import PdfReader

from .config import Settings
from .errors import PreviousContextError
from .manifest import sha256_file
from .models import CanonicalActa

SUPPORTED_PREVIOUS_ACTA = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".json": "application/json",
}


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def previous_acta_format(path: Path) -> str:
    try:
        return SUPPORTED_PREVIOUS_ACTA[Path(path).suffix.lower()]
    except KeyError as exc:
        formats = ", ".join(suffix.removeprefix(".") for suffix in SUPPORTED_PREVIOUS_ACTA)
        raise PreviousContextError(f"unsupported previous acta format; use {formats}") from exc


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError as exc:
        raise PreviousContextError(f"previous acta must be UTF-8 text: {path}") from exc


def canonical_acta_from_json(path: Path) -> CanonicalActa:
    try:
        return CanonicalActa.model_validate_json(_read_text(path))
    except Exception as exc:
        raise PreviousContextError(f"previous JSON acta is not canonical: {path}: {exc}") from exc


def _extract_html(path: Path) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(_read_text(path))
        parser.close()
    except Exception as exc:
        raise PreviousContextError(f"could not read previous HTML acta {path}: {exc}") from exc
    return "".join(parser.parts).strip()


def _extract_pages(path: Path) -> list[str]:
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise PreviousContextError(f"previous acta is encrypted: {path}")
        return [(page.extract_text() or "").strip() for page in reader.pages]
    except PreviousContextError:
        raise
    except Exception as exc:
        raise PreviousContextError(f"could not read previous acta {path}: {exc}") from exc


def extract_previous_context(path: Path, settings: Settings) -> dict:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise PreviousContextError(f"previous acta not found: {path}")
    source_format = previous_acta_format(path)
    canonical_acta = None
    if source_format == "application/pdf":
        pages = _extract_pages(path)
        text = "\n\n".join(page for page in pages if page).strip()
    elif source_format == "text/html":
        pages = []
        text = _extract_html(path)
    elif source_format == "application/json":
        pages = []
        canonical_acta = canonical_acta_from_json(path)
        text = json.dumps(canonical_acta.model_dump(mode="json"), ensure_ascii=False, indent=2)
    else:
        pages = []
        text = _read_text(path)
    if not text:
        raise PreviousContextError(
            f"previous acta has no extractable text layer; OCR is required: {path}"
        )
    folded = text.casefold()
    if canonical_acta is not None:
        participants = [person.model_dump(mode="json") for person in canonical_acta.participants]
    else:
        participants = []
        for person in settings.reference_participants:
            if person.name.casefold() in folded or (
                person.email and person.email.casefold() in folded
            ):
                participants.append(
                    {
                        "name": person.name,
                        "email": person.email,
                        "attendance": "unconfirmed",
                        "source": "previous_acta",
                    }
                )
    terms = [term for term in settings.glossary.canonical_terms() if term.casefold() in folded]
    return {
        "source": str(path),
        "sha256": sha256_file(path),
        "format": source_format,
        "date": canonical_acta.meeting.date.isoformat() if canonical_acta else None,
        "page_count": len(pages),
        "text": text,
        "canonical_acta": canonical_acta.model_dump(mode="json") if canonical_acta else None,
        "participants": participants,
        "canonical_terms_found": terms,
        "provenance": "previous_acta",
        "warning": "Prior participants, owners, and commitments are not current-meeting facts.",
    }
