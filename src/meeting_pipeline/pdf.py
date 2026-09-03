"""Headless Chromium PDF export.

Chromium is discovered rather than bundled: explicit config first, then the usual macOS
and Linux installs, then the Puppeteer/Playwright caches most machines already have.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .config import PdfSettings
from .errors import PdfError

SYSTEM_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/snap/bin/chromium",
)

CACHE_ROOTS = (
    (Path.home() / ".cache" / "puppeteer", ("chrome-headless-shell", "chrome")),
    (Path.home() / "Library" / "Caches" / "ms-playwright", ("chromium_headless_shell", "chromium")),
    (Path.home() / ".cache" / "ms-playwright", ("chromium_headless_shell", "chromium")),
)

BINARY_NAMES = (
    "chrome-headless-shell",
    "headless_shell",
    "Google Chrome for Testing",
    "Chromium",
    "chrome",
    "chromium",
)


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _search_cache(root: Path, prefixes: tuple[str, ...]) -> Path | None:
    if not root.is_dir():
        return None
    for prefix in prefixes:
        # Newest build directory first, so an old cached revision never wins.
        builds = sorted(
            (p for p in root.iterdir() if p.is_dir() and p.name.startswith(prefix)),
            reverse=True,
        )
        for build in builds:
            for name in BINARY_NAMES:
                for candidate in build.rglob(name):
                    if _is_executable(candidate):
                        return candidate
    return None


def discover_chromium(explicit: str | None = None) -> Path:
    """Return a usable Chromium executable or explain exactly where we looked."""
    if explicit:
        path = Path(explicit).expanduser()
        if not _is_executable(path):
            raise PdfError(f"configured chromium_path is not an executable file: {path}")
        return path

    for candidate in SYSTEM_CANDIDATES:
        path = Path(candidate)
        if _is_executable(path):
            return path

    for root, prefixes in CACHE_ROOTS:
        found = _search_cache(root, prefixes)
        if found is not None:
            return found

    raise PdfError(
        "no Chromium found. Install Google Chrome or Chromium, or set `pdf.chromium_path` "
        "in the config. Looked at: "
        + ", ".join([*SYSTEM_CANDIDATES, *(str(root) for root, _ in CACHE_ROOTS)])
    )


def export_pdf(html_path: Path, pdf_path: Path, settings: PdfSettings) -> Path:
    """Print `html_path` to `pdf_path` with browser headers and footers disabled."""
    html_path = Path(html_path)
    pdf_path = Path(pdf_path)
    if not html_path.is_file():
        raise PdfError(f"HTML source not found: {html_path}")
    chromium = discover_chromium(settings.chromium_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    args = [
        str(chromium),
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--no-first-run",
        "--no-default-browser-check",
        # Nothing in the acta needs the network; make that a hard guarantee.
        "--disable-extensions",
        "--disable-background-networking",
        "--no-pdf-header-footer",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=6000",
        f"--print-to-pdf={pdf_path}",
        html_path.resolve().as_uri(),
    ]
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=settings.timeout_seconds, check=False
        )
    except FileNotFoundError as exc:
        raise PdfError(f"could not execute Chromium at {chromium}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PdfError(f"Chromium timed out after {settings.timeout_seconds}s") from exc
    if result.returncode != 0 and not pdf_path.is_file():
        raise PdfError(
            f"Chromium failed to export {pdf_path.name}: {(result.stderr or '').strip()[:500]}"
        )
    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise PdfError(f"Chromium produced no PDF at {pdf_path}")
    return pdf_path
