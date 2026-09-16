from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_local_deployment_profile_points_to_openai_compatible_server():
    data = yaml.safe_load((ROOT / "config" / "local-llama-cpp.yaml").read_text())
    assert data["reasoning"]["base_url"].endswith("/v1")
    assert data["reasoning"]["model"]


def test_vllm_image_is_pinned_and_private_by_default():
    compose = yaml.safe_load((ROOT / "deploy" / "vllm" / "docker-compose.yaml").read_text())
    service = compose["services"]["model"]
    assert service["image"].startswith("vllm/vllm-openai:v")
    assert ":latest" not in service["image"]
    assert service["ports"][0].startswith("127.0.0.1:")
    assert "Qwen/Qwen3-8B" in " ".join(service["command"])


def test_llama_cpp_runbook_names_exact_official_gguf():
    text = (ROOT / "deploy" / "llama-cpp" / "README.md").read_text()
    assert "Qwen/Qwen3-8B-GGUF" in text
    assert "Qwen3-8B-Q4_K_M.gguf" in text
    assert "127.0.0.1" in text


def test_mac_mini_profile_fits_a_16gb_native_deployment():
    from meeting_pipeline.config import load_config

    settings = load_config(ROOT / "config" / "mac-mini.yaml")

    assert settings.transcription.provider == "mlx-whisper"
    assert settings.transcription.model == "mlx-community/whisper-large-v3-turbo"
    assert settings.transcription.vad_filter is False
    assert settings.reasoning.base_url == "http://127.0.0.1:8080/v1"
    assert settings.reasoning.context_window == 16384
    assert settings.reasoning.temperature <= 0.2
    # The packaged defaults (6000-token chunks + 6144 output) cannot fit a 16K window.
    reserved = settings.reasoning.max_output_tokens + settings.chunking.target_tokens
    assert reserved < settings.reasoning.context_window * 0.6


def test_native_mac_mini_runbook_documents_exact_commands_and_no_auto_start():
    text = (ROOT / "docs" / "native-mac-mini.md").read_text(encoding="utf-8")

    assert "uv sync --extra stt-mlx" in text
    assert "--config config/mac-mini.yaml" in text
    assert "llama-server" in text
    assert "--chat-template-kwargs" in text and "enable_thinking" in text
    assert "brew install ffmpeg" in text
    assert "uv run meeting doctor" in text
    assert "Qwen/Qwen2.5-7B-Instruct-GGUF" in text
    # The pipeline must never be documented as starting the model server itself.
    assert "never starts" in text.casefold() or "does not start" in text.casefold()
