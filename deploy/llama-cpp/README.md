# Local llama.cpp model server

The default local profile uses the official `Qwen/Qwen3-8B-GGUF` repository and
`Qwen3-8B-Q4_K_M.gguf` (5,027,783,488 bytes). The repository exposes a native
32,768-token context. Source:
https://huggingface.co/Qwen/Qwen3-8B-GGUF

Install llama.cpp on macOS:

    brew install llama.cpp

Start the server manually when processing a meeting:

    llama-server \
      --hf-repo Qwen/Qwen3-8B-GGUF \
      --hf-file Qwen3-8B-Q4_K_M.gguf \
      --host 127.0.0.1 \
      --port 8080 \
      --ctx-size 32768 \
      --jinja

The first invocation downloads about 5 GB. The pipeline never starts this server
by itself. Check readiness:

    curl -fsS http://127.0.0.1:8080/v1/models

Then run the pipeline with:

    uv run meeting process ... --config config/local-llama-cpp.yaml

The API is loopback-only. Do not bind it to `0.0.0.0` without authentication and
a private network or authenticated reverse proxy.
