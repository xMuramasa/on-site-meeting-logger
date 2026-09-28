"""Exercise the installer in an isolated checkout with simulated system tools.

No package manager, model download, network connection, or actual Python sync is run.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

TOOL_STUB = r'''
import json
import os
import sys
from pathlib import Path

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
state = Path(os.environ["INSTALL_TEST_STATE"])
with (state / "calls.jsonl").open("a") as log:
    log.write(json.dumps({"tool": tool, "args": args, "cwd": os.getcwd(),
                         "venv": os.environ.get("UV_PROJECT_ENVIRONMENT")}) + "\n")

def install_tool(name, directory=None):
    target = (directory or state / "bin") / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(Path(__file__).read_text())
    target.chmod(0o755)

if tool == "uname":
    print(os.environ.get("INSTALL_TEST_OS", "Darwin") if args == ["-s"]
          else os.environ.get("INSTALL_TEST_ARCH", "arm64"))
elif tool == "sysctl":
    print(os.environ.get("INSTALL_TEST_ROSETTA", "0"))
elif tool == "brew":
    if args == ["--prefix"]:
        print(state)
    elif args[0] == "install":
        package = args[-1]
        if package == os.environ.get("INSTALL_TEST_BREW_FAILURE"):
            sys.exit(1)
        if package == "google-chrome":
            (state / "browser").touch()
        else:
            names = {"uv": ["uv"], "ffmpeg": ["ffmpeg", "ffprobe"],
                     "llama.cpp": ["llama-server"], "node": ["node"],
                     "oven-sh/bun/bun": ["bun"]}[package]
            for name in names:
                install_tool(name)
    else:
        raise AssertionError(args)
elif tool == "uv" and args[0] == "sync":
    install_tool("python", Path(os.environ["UV_PROJECT_ENVIRONMENT"]) / "bin")
elif tool == "python":
    if args[0] == "-c":
        sys.exit(0 if (state / "browser").exists() else 1)
    sys.stdin.read()
    sys.exit(int(os.environ.get("INSTALL_TEST_IMPORT_FAILURE", "0")))
elif tool == "bun" and args == ["run", "build"]:
    bundle = Path.cwd().parent / "src/meeting_pipeline/_web/index.html"
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text("rebuilt frontend")
'''


@pytest.fixture
def installer(tmp_path):
    project = tmp_path / "checkout with spaces"
    (project / "scripts").mkdir(parents=True)
    for relative in ["scripts/install.sh", "Makefile", "pyproject.toml", "uv.lock"]:
        shutil.copy2(ROOT / relative, project / relative)
    shutil.copytree(ROOT / "config", project / "config")
    (project / "webui").mkdir()
    bundle = project / "src/meeting_pipeline/_web/index.html"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("bundled frontend")
    state = tmp_path / "tools"
    (state / "bin").mkdir(parents=True)
    stub = f"#!{sys.executable}\n{TOOL_STUB}"
    for tool in ["uname", "sysctl", "brew"]:
        executable = state / "bin" / tool
        executable.write_text(stub)
        executable.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{state / 'bin'}:/usr/bin:/bin",
        "INSTALL_TEST_STATE": str(state),
        "DEV": "0",
    }

    def run(*arguments, via_make=False, **overrides):
        command = (
            ["/usr/bin/make", "install", *arguments]
            if via_make
            else ["/bin/bash", str(project / "scripts/install.sh"), *arguments]
        )
        return subprocess.run(
            command,
            cwd=project if via_make else tmp_path,
            env={**env, **overrides},
            capture_output=True,
            text=True,
            timeout=30,
        )

    def calls(tool):
        log = state / "calls.jsonl"
        return [
            item
            for line in (log.read_text().splitlines() if log.exists() else [])
            if (item := json.loads(line))["tool"] == tool
        ]

    return run, calls, project, state


@pytest.mark.parametrize(
    ("architecture", "extra", "module", "profile", "context"),
    [
        ("arm64", "stt-mlx", "mlx_whisper", "mac-mini.yaml", "16384"),
        ("x86_64", "stt", "faster_whisper", "local-llama-cpp.yaml", "32768"),
    ],
)
def test_install_selects_backend_and_preserves_files_on_repeat(
    installer, architecture, extra, module, profile, context
):
    run, calls, project, _ = installer
    config = project / "config" / profile
    original_config = config.read_bytes()
    bundle = project / "src/meeting_pipeline/_web/index.html"
    recording = project.parent / "MeetingWork" / "meeting.m4a"
    recording.parent.mkdir()
    recording.write_bytes(b"existing recording")

    result = run(INSTALL_TEST_ARCH=architecture)

    assert result.returncode == 0, result.stderr
    sync = calls("uv")[0]
    assert sync["args"] == ["sync", "--locked", "--python", "3.12", "--extra", extra, "--no-dev"]
    assert sync["cwd"] == str(project)
    assert sync["venv"] == str(project / ".venv")
    assert calls("python")[-1]["args"] == ["-", module, f"config/{profile}"]
    assert f"MODEL_CONTEXT={context}" in result.stdout
    assert f"CONFIG=config/{profile}" in result.stdout
    assert "make tunnel SSH_USER=" in result.stdout
    assert not calls("bun") and not calls("node")
    assert all(item["args"] == ["--version"] for item in calls("llama-server"))
    installs = [item for item in calls("brew") if item["args"][0] == "install"]
    assert [item["args"] for item in installs] == [
        ["install", "uv"], ["install", "ffmpeg"], ["install", "llama.cpp"],
        ["install", "--cask", "google-chrome"],
    ]

    result = run(INSTALL_TEST_ARCH=architecture)

    assert result.returncode == 0, result.stderr
    assert [item for item in calls("brew") if item["args"][0] == "install"] == installs
    assert config.read_bytes() == original_config
    assert recording.read_bytes() == b"existing recording"
    assert bundle.read_text() == "bundled frontend"


def test_make_dev_installs_frontend_tools_and_rebuilds_missing_bundle(installer):
    run, calls, project, state = installer
    (state / "browser").touch()  # Existing Chrome/Chromium/cache is reused.
    (project / "src/meeting_pipeline/_web/index.html").unlink()

    result = run("DEV=1", via_make=True)

    assert result.returncode == 0, result.stderr
    assert calls("uv")[0]["args"][-2:] == ["--group", "dev"]
    assert [item["args"] for item in calls("bun")] == [
        ["install", "--frozen-lockfile"], ["run", "build"],
    ]
    assert all(item["cwd"] == str(project / "webui") for item in calls("bun"))
    packages = [item["args"][-1] for item in calls("brew") if item["args"][0] == "install"]
    assert "node" in packages and "oven-sh/bun/bun" in packages
    assert "google-chrome" not in packages


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({"INSTALL_TEST_OS": "Linux"}, "supports macOS"),
        ({"INSTALL_TEST_ARCH": "x86_64", "INSTALL_TEST_ROSETTA": "1"}, "Rosetta"),
        ({"DEV": "yes"}, "DEV must be 0 or 1"),
    ],
)
def test_unsupported_environment_fails_before_installation(installer, environment, message):
    run, calls, _, _ = installer
    result = run(**environment)
    assert result.returncode != 0
    assert message in result.stderr
    assert not calls("brew") and not calls("uv")


def test_missing_homebrew_gives_setup_instructions(installer):
    run, calls, _, state = installer
    (state / "bin/brew").unlink()
    result = run()
    assert result.returncode != 0
    assert "https://brew.sh" in result.stderr
    assert not calls("uv")


@pytest.mark.parametrize("environment", [
    {"INSTALL_TEST_BREW_FAILURE": "ffmpeg"},
    {"INSTALL_TEST_IMPORT_FAILURE": "1"},
])
def test_failed_install_or_verification_never_reports_success(installer, environment):
    run, _, _, _ = installer
    result = run(**environment)
    assert result.returncode != 0
    assert "Installation complete" not in result.stdout


def test_missing_bundle_stops_normal_install_before_system_changes(installer):
    run, calls, project, _ = installer
    (project / "src/meeting_pipeline/_web/index.html").unlink()
    result = run()
    assert result.returncode != 0
    assert "make install DEV=1" in result.stderr
    assert not calls("brew")
