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
