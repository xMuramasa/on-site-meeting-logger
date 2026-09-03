"""Extract prior-acta text while preserving its provenance."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from .config import Settings
from .errors import PreviousContextError
from .manifest import sha256_file


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
    pages = _extract_pages(path)
    text = "\n\n".join(page for page in pages if page).strip()
    if not text:
        raise PreviousContextError(
            f"previous acta has no extractable text layer; OCR is required: {path}"
        )
    folded = text.casefold()
    participants = []
    for person in settings.reference_participants:
        if person.name.casefold() in folded or (person.email and person.email.casefold() in folded):
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
        "page_count": len(pages),
        "text": text,
        "participants": participants,
        "canonical_terms_found": terms,
        "provenance": "previous_acta",
        "warning": "Prior participants, owners, and commitments are not current-meeting facts.",
    }
