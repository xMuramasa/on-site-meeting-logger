# Hosted vLLM profile

Requirements: Linux, Docker with NVIDIA Container Toolkit, and enough VRAM for
Qwen3-8B plus a 32K KV cache. Copy `model.env.example` to a private `.env`, set a
strong `VLLM_API_KEY`, and run:

    docker compose --env-file .env up -d

The port is bound to `127.0.0.1` by default. Set the same secret in the pipeline
environment:

    export MEETING_MODEL_API_KEY="$VLLM_API_KEY"

Use `config/hosted-vllm.yaml`. Verify `/health` and `/v1/models` before sending
meeting data. The image is pinned; review vLLM release notes before changing it.
