SHELL := /bin/bash

DEV ?= 0
OUTPUT_ROOT ?= /Users/muramasa/Recordings
CONFIG ?= config/local-llama-cpp.yaml
PORT ?= 8765
SSH_USER ?=
SERVER_IP ?=
export SSH_USER SERVER_IP
MODEL_REPO ?= Qwen/Qwen3-8B-GGUF
MODEL_FILE ?= Qwen3-8B-Q4_K_M.gguf
MODEL_PORT ?= 8080
MODEL_CONTEXT ?= 32768

.PHONY: help install setup model output-dir ui open tunnel frontend-install frontend-dev frontend-build test lint hygiene check build doctor

help: ## Show available commands
	@printf "Meeting Pipeline\n\n"
	@printf "Install on macOS: make install (or make install DEV=1)\n"
	@printf "Use the installer's platform-specific startup commands.\n\n"
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: export DEV := $(DEV)
install: ## Install the macOS app and missing system tools (DEV=1 adds development tools)
	bash scripts/install.sh

setup: ## Install Python/faster-whisper and frontend dependencies with existing tools
	uv sync --extra stt
	cd webui && bun install

model: ## Start Qwen3-8B through llama.cpp on localhost:8080
	llama-server \
		--hf-repo $(MODEL_REPO) \
		--hf-file $(MODEL_FILE) \
		--host 127.0.0.1 \
		--port $(MODEL_PORT) \
		--ctx-size $(MODEL_CONTEXT) \
		--parallel 1 \
		--cache-ram 0 \
		--alias $(MODEL_REPO) \
		--jinja \
		--chat-template-kwargs '{"enable_thinking":false}'

output-dir: ## Create the meeting-artifact output directory
	mkdir -p "$(OUTPUT_ROOT)"

ui: output-dir ## Start the local web studio on localhost:8765
	uv run --no-dev meeting serve \
		--output-root $(OUTPUT_ROOT) \
		--config $(CONFIG) \
		--port $(PORT)

open: ## Open the running studio in the default browser
	open "http://127.0.0.1:$(PORT)"

tunnel: ## Connect to a remote studio: make tunnel SSH_USER=name SERVER_IP=192.168.1.50
	@if [ -z "$$SSH_USER" ] || [ -z "$$SERVER_IP" ]; then \
		printf '%s\n' 'Usage: make tunnel SSH_USER=name SERVER_IP=192.168.1.50 [PORT=8765]' >&2; \
		exit 2; \
	fi
	@printf '%s\n' 'After SSH connects, open http://127.0.0.1:$(PORT)' 'Keep this terminal open; press Ctrl+C to disconnect.'
	ssh -N -o ExitOnForwardFailure=yes \
		-o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
		-L "127.0.0.1:$(PORT):127.0.0.1:$(PORT)" \
		-l "$$SSH_USER" -- "$$SERVER_IP"

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
	uv run --no-dev meeting doctor --output-root $(OUTPUT_ROOT) --config $(CONFIG)
