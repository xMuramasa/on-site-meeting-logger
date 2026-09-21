#!/bin/bash
# macOS installer; compatible with the Bash 3.2 shipped by Apple.
set -euo pipefail

fail() {
    printf 'Installation error: %s\n' "$*" >&2
    exit 1
}

dev_mode="${DEV:-0}"
for argument in "$@"; do
    case "$argument" in
        --dev) dev_mode=1 ;;
        -h|--help)
            printf '%s\n' 'Usage: bash scripts/install.sh [--dev]' \
                'Or: make install [DEV=1]' \
                'Installs the macOS app; --dev also installs frontend development tools.'
            exit 0
            ;;
        *) fail "Unknown option: $argument. Use --help for usage." ;;
    esac
done
case "$dev_mode" in
    0|1) ;;
    *) fail 'DEV must be 0 or 1.' ;;
esac

[[ "$(uname -s)" == Darwin ]] || fail 'This installer supports macOS. See README.md for manual installation.'
case "$(uname -m)" in
    arm64)
        stt_extra=stt-mlx
        stt_module=mlx_whisper
        profile=config/mac-mini.yaml
        model_context=16384
        ;;
    x86_64)
        if [[ "$(sysctl -in sysctl.proc_translated 2>/dev/null || true)" == 1 ]]; then
            fail 'Open a native Apple Silicon terminal (disable Rosetta) and rerun make install.'
        fi
        stt_extra=stt
        stt_module=faster_whisper
        profile=config/local-llama-cpp.yaml
        model_context=32768
        ;;
    *) fail 'Supported Mac architectures are arm64 and x86_64.' ;;
esac

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
[[ -f pyproject.toml && -f uv.lock ]] || fail 'Run the installer from a complete project checkout.'
if [[ "$dev_mode" == 0 && ! -s src/meeting_pipeline/_web/index.html ]]; then
    fail 'The bundled frontend is missing. Run make install DEV=1 to build it.'
fi

command -v brew >/dev/null 2>&1 || fail \
    'Homebrew is required. Install it from https://brew.sh, follow its PATH instructions, then rerun make install.'
brew_prefix="$(brew --prefix)"
export PATH="$brew_prefix/bin:$brew_prefix/sbin:$PATH"

ensure_tool() {
    local executable="$1" formula="$2"
    if ! command -v "$executable" >/dev/null 2>&1; then
        printf 'Installing %s with Homebrew...\n' "$formula"
        brew install "$formula"
    fi
    command -v "$executable" >/dev/null 2>&1 || fail \
        "$executable is unavailable after installing $formula. Check Homebrew's PATH and linking."
}

printf 'Installing Meeting Pipeline with %s (%s).\n' "$stt_extra" "$profile"
ensure_tool uv uv
ensure_tool ffmpeg ffmpeg
ensure_tool ffprobe ffmpeg
ensure_tool llama-server llama.cpp

# Lock application dependencies and keep installation inside this checkout's virtualenv.
# uv downloads Python 3.12 automatically when a compatible interpreter is not installed.
sync_args=(sync --locked --python 3.12 --extra "$stt_extra")
if [[ "$dev_mode" == 1 ]]; then
    sync_args+=(--group dev)
else
    sync_args+=(--no-dev)
fi
UV_PROJECT_ENVIRONMENT="$project_root/.venv" uv "${sync_args[@]}"
python_bin="$project_root/.venv/bin/python"

# Reuse the app's browser discovery, including existing Chromium browser/cache installs.
browser_check='from meeting_pipeline.pdf import discover_chromium; print(discover_chromium())'
if ! "$python_bin" -c "$browser_check" >/dev/null 2>&1; then
    printf '%s\n' 'Installing Google Chrome for PDF export...'
    brew install --cask google-chrome
fi
"$python_bin" -c "$browser_check" || fail 'The app could not find a Chromium browser for PDF export.'

if [[ "$dev_mode" == 1 ]]; then
    ensure_tool node node
    node -e '
        const [major, minor] = process.versions.node.split(".").map(Number);
        const compatible = (major === 20 && minor >= 19) || (major === 22 && minor >= 12) || major > 22;
        process.exit(compatible ? 0 : 1);
    ' || \
        fail 'Frontend tools need Node.js 20.19+ or 22.12+. Update Node.js and rerun make install DEV=1.'
    ensure_tool bun oven-sh/bun/bun
    (
        cd webui
        bun install --frozen-lockfile
        bun run build
    )
fi

printf '%s\n' 'Verifying installed dependencies...'
uv --version
ffmpeg -version >/dev/null
ffprobe -version >/dev/null
llama-server --version >/dev/null 2>&1
"$python_bin" - "$stt_module" "$profile" <<'PY'
import importlib
import sys
from pathlib import Path

from meeting_pipeline.config import load_config
from meeting_pipeline.web import create_app  # noqa: F401: verify web dependencies import

importlib.import_module(sys.argv[1])
settings = load_config(Path(sys.argv[2]))
assert Path("src/meeting_pipeline/_web/index.html").is_file(), "Frontend build missing"
print(f"Transcription backend ready: {settings.transcription.provider}")
PY

printf '\n%s\n' 'Installation complete. Run the following from this checkout:'
printf '\n  # Terminal 1: reasoning server\n  make model MODEL_CONTEXT=%s\n' "$model_context"
printf '\n  # Terminal 2: web app\n  make ui CONFIG=%s OUTPUT_ROOT="$HOME/MeetingWork"\n' "$profile"
printf '\n  # Once the model server is ready, check the deployment:\n  make doctor CONFIG=%s OUTPUT_ROOT="$HOME/MeetingWork"\n' "$profile"
cat <<'TEXT'

On this Mac, open http://127.0.0.1:8765 (or run make open).

From another computer:
  1. Enable System Settings > General > Sharing > Remote Login on the mini for your account.
  2. In a checkout on the client, replace the placeholders and run:
     make tunnel SSH_USER=YOUR_MINI_USERNAME SERVER_IP=MINI_IP_ADDRESS
  3. Leave the tunnel open and browse to http://127.0.0.1:8765 on the client.

Keep the mini awake while processing. Model weights download on first use.
Audio and results stay in the mini's output directory until you delete them.
TEXT
