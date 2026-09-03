import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def make(*targets: str) -> str:
    result = subprocess.run(
        ["make", "--dry-run", *targets],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout


def test_model_target_is_loopback_only_and_uses_official_gguf():
    output = make("model")
    assert "llama-server" in output
    assert "Qwen/Qwen3-8B-GGUF" in output
    assert "Qwen3-8B-Q4_K_M.gguf" in output
    assert "--host 127.0.0.1" in output


def test_ui_target_uses_local_profile_and_recordings_directory():
    output = make("ui")
    assert "meeting serve" in output
    assert "--output-root /Users/muramasa/Recordings" in output
    assert "--config config/local-llama-cpp.yaml" in output


def test_check_target_runs_python_and_frontend_gates():
    output = make("check")
    assert "uv run pytest" in output
    assert "uv run ruff check" in output
    assert "bun run test" in output
    assert "bun run build" in output
