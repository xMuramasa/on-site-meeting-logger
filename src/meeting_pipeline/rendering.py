"""Render Markdown and branded HTML from one approved canonical acta.

Both formats come from the same `CanonicalActa`, so they cannot drift: every action,
decision, proposal, risk, and question ID appears in both, and `validation.py` asserts it.
No external font, script, image, or stylesheet is referenced — the PDF must render
identically on a machine with no network.
"""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup, escape

from .config import BrandingSettings
from .errors import RenderError
from .manifest import write_text_atomic
from .models import CanonicalActa, EvidenceRange, evidence_label
from .reasoning import render_digest
from .resources import resource

PROVENANCE_LABELS = {
    "previous_acta": "Solo acta anterior",
    "current_meeting": "Esta reunión",
    "both": "Acta anterior + esta reunión",
}

# Anything that would pull a byte over the network at print time.
EXTERNAL_ASSET_PATTERN = re.compile(
    r"""(?ix)
    (?: https?: | // (?!\s) | \bsrc\s*=\s*["']?\s*(?:https?:|//)
      | \burl\s*\(\s*["']?\s*(?:https?:|//)
      | \b@import\b | <script\b | <iframe\b )
    """
)


def _ts(ranges: list[EvidenceRange]) -> str:
    return evidence_label(list(ranges))


def _inline(text: str) -> Markup:
    """Escape, then honour the small Markdown subset the model writes in prose."""
    safe = str(escape(text))
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
    safe = re.sub(r"`(.+?)`", r"<code>\1</code>", safe)
    return Markup(safe)  # noqa: S704 - inputs are escaped above


def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(resource("templates"))),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=False,
        lstrip_blocks=False,
        autoescape=lambda name: bool(name and name.endswith("html.j2")),
    )
    env.filters["ts"] = _ts
    env.filters["evidence"] = _ts
    env.filters["inline"] = _inline
    env.filters["inline_html"] = _inline
    return env


def _context(acta: CanonicalActa, branding: BrandingSettings) -> dict:
    return {
        "acta": acta,
        "branding": branding,
        "follow_up_number": max((s.number for s in acta.sections), default=0) + 1,
        "provenance_label": lambda key: PROVENANCE_LABELS.get(key, key),
    }


def artifact_stem(acta: CanonicalActa, branding: BrandingSettings) -> str:
    return f"{branding.filename_prefix}_{acta.meeting.date.isoformat()}"


def render_markdown(acta: CanonicalActa, branding: BrandingSettings | None = None) -> str:
    branding = branding or BrandingSettings()
    text = _environment().get_template("acta.md.j2").render(**_context(acta, branding))
    # Markdown hard line breaks in the metadata block: two trailing spaces.
    text = re.sub(r"^(\*\*(?:Reunión|Fecha):\*\* .*?)$", r"\1  ", text, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", text).lstrip("\n")


def render_html(acta: CanonicalActa, branding: BrandingSettings | None = None) -> str:
    branding = branding or BrandingSettings()
    html = _environment().get_template("acta.html.j2").render(**_context(acta, branding))
    offender = EXTERNAL_ASSET_PATTERN.search(html)
    if offender:
        raise RenderError(
            f"rendered HTML references an external asset ({offender.group(0)!r}); "
            "the acta must be self-contained for offline printing"
        )
    return html


def render_documents(
    acta: CanonicalActa, out_dir: Path, branding: BrandingSettings | None = None
) -> dict[str, Path]:
    """Write configured-name Markdown and HTML documents plus digest.md."""
    out_dir = Path(out_dir)
    branding = branding or BrandingSettings()
    stem = artifact_stem(acta, branding)
    return {
        "markdown": write_text_atomic(out_dir / f"{stem}.md", render_markdown(acta, branding)),
        "html": write_text_atomic(out_dir / f"{stem}.html", render_html(acta, branding)),
        "digest": write_text_atomic(out_dir / "digest.md", render_digest(acta, [])),
    }
