SHELL := /bin/bash

OUTPUT_ROOT ?= /Users/muramasa/Recordings
CONFIG ?= config/local-llama-cpp.yaml
PORT ?= 8765
MODEL_REPO ?= Qwen/Qwen3-8B-GGUF
MODEL_FILE ?= Qwen3-8B-Q4_K_M.gguf
MODEL_PORT ?= 8080
MODEL_CONTEXT ?= 32768

.PHONY: help setup model ui open frontend-install frontend-dev frontend-build test lint hygiene check build doctor

help: ## Show available commands
	@printf "Meeting Pipeline\n\n"
	@printf "Run the model and UI in separate terminals:\n"
	@printf "  make model\n"
	@printf "  make ui\n\n"
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Install Python, transcription, and frontend dependencies
	uv sync --extra stt
	cd webui && bun install

model: ## Start Qwen3-8B through llama.cpp on localhost:8080
	llama-server \
		--hf-repo $(MODEL_REPO) \
		--hf-file $(MODEL_FILE) \
		--host 127.0.0.1 \
		--port $(MODEL_PORT) \
		--ctx-size $(MODEL_CONTEXT) \
		--jinja

ui: ## Start the local web studio on localhost:8765
	uv run meeting serve \
		--output-root $(OUTPUT_ROOT) \
		--config $(CONFIG) \
		--port $(PORT)

open: ## Open the running studio in the default browser
	open "http://127.0.0.1:$(PORT)"

frontend-install: ## Install frontend dependencies with Bun
	cd webui && bun install

frontend-dev: ## Start the Vite frontend for UI development
	cd webui && bun run dev

frontend-build: ## Type-check and build the embedded frontend
	cd webui && bun run build

test: ## Run Python and frontend tests
	uv run pytest -o addopts='' -q
	cd webui && bun run test

lint: ## Run Python lint and frontend type checking
	uv run ruff check .
	cd webui && bun run typecheck

hygiene: ## Confirm meeting artifacts are not tracked by Git
	uv run python scripts/check_repository_hygiene.py

check: ## Run all test, lint, type-check, and frontend build gates
	uv run pytest -o addopts='' -q
	uv run ruff check .
	$(MAKE) hygiene
	cd webui && bun run test
	cd webui && bun run build

build: frontend-build ## Build the Python wheel and source archive
	uv build

doctor: ## Check required local executables
	@command -v uv >/dev/null && echo "uv: ok" || { echo "uv: missing"; exit 1; }
	@command -v ffmpeg >/dev/null && echo "ffmpeg: ok" || { echo "ffmpeg: missing"; exit 1; }
	@command -v ffprobe >/dev/null && echo "ffprobe: ok" || { echo "ffprobe: missing"; exit 1; }
	@command -v llama-server >/dev/null && echo "llama-server: ok" || { echo "llama-server: missing"; exit 1; }
	@command -v bun >/dev/null && echo "bun: ok" || { echo "bun: missing"; exit 1; }
