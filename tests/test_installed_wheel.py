"""Release check: install the built wheel into an isolated target outside the checkout."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def test_installed_wheel_renders_from_bundled_resources(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    wheels = sorted((repo / "dist").glob("meeting_pipeline-*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        pytest.skip("run uv build before the installed-wheel release check")
    target = tmp_path / "installed"
    env = {**os.environ, "UV_CACHE_DIR": str(tmp_path / "uv-cache")}
    result = subprocess.run(
        [
            shutil.which("uv") or "uv",
            "pip",
            "install",
            "--target",
            str(target),
            "--no-deps",
            str(wheels[-1]),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    # The target takes precedence over the developer environment's editable installation.
    env["PYTHONPATH"] = str(target)
    fixture = tmp_path / "fixture.json"
    fixture.write_bytes((repo / "tests" / "fixtures" / "valid-acta.json").read_bytes())
    script = """
from pathlib import Path
import meeting_pipeline
from meeting_pipeline.config import load_config
from meeting_pipeline.models import CanonicalActa
from meeting_pipeline.rendering import render_documents
from meeting_pipeline.resources import resource
assert "installed" in str(Path(meeting_pipeline.__file__).resolve())
assert "_bundled" in str(resource("templates"))
assert resource("prompts", "extract-chunk.md").is_file()
acta = CanonicalActa.model_validate_json(Path("fixture.json").read_text())
files = render_documents(acta, Path("outputs"), load_config().branding)
assert files["html"].is_file()
assert files["markdown"].is_file()
assert (Path(meeting_pipeline.__file__).parent / "_web" / "index.html").is_file()
"""
    checked = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert checked.returncode == 0, checked.stderr
