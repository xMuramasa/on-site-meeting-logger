import subprocess
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
LEGACY_BRAND = "my" + "nu"


def test_project_identity_is_generic():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["name"] == "meeting-pipeline"
    assert project["project"]["scripts"] == {"meeting": "meeting_pipeline.cli:app"}
    assert (ROOT / "config/meeting.yaml").is_file()
    assert (ROOT / "src/meeting_pipeline").is_dir()
    assert not (ROOT / f"config/{LEGACY_BRAND}.yaml").exists()
    assert not (ROOT / f"src/{LEGACY_BRAND}_meeting_pipeline").exists()


def test_tracked_files_contain_no_legacy_brand_name():
    tracked = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    offenders = []
    for relative in tracked:
        path = ROOT / relative
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if LEGACY_BRAND in text.lower() or LEGACY_BRAND in relative.lower():
            offenders.append(relative)

    assert not offenders, offenders


def test_web_studio_uses_shadcn_neutral_palette():
    styles = (ROOT / "webui/src/styles.css").read_text(encoding="utf-8")

    assert "--background: #ffffff" in styles
    assert "--foreground: #0a0a0a" in styles
    assert "--primary: #171717" in styles
    assert "--border: #e5e5e5" in styles
    assert "purple" not in styles.lower()


def test_default_config_has_no_organization_specific_roster():
    config = yaml.safe_load((ROOT / "config/meeting.yaml").read_text(encoding="utf-8"))

    assert config["reference_participants"] == []
    assert config["glossary"] == {}
