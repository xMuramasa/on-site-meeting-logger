"""Locate packaged assets (config, prompts, templates).

They live at the repository root for the operator to edit, and are force-included into
the wheel under `_bundled/` so an installed copy keeps working.
"""

from __future__ import annotations

from pathlib import Path

from .errors import ConfigError

_MODULE_DIR = Path(__file__).resolve().parent
_ROOTS = (_MODULE_DIR.parents[1], _MODULE_DIR / "_bundled")


def resource(*parts: str) -> Path:
    for root in _ROOTS:
        candidate = root.joinpath(*parts)
        if candidate.exists():
            return candidate
    raise ConfigError(f"packaged resource not found: {'/'.join(parts)}")
