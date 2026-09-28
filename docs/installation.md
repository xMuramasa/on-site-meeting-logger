# macOS installation

Run these commands from a complete checkout of the project on the Mac that will process audio:

    make install
    # Or include development tools and rebuild the frontend:
    make install DEV=1

The script can also be run as `bash scripts/install.sh` or `bash scripts/install.sh --dev`.
It supports the system Bash on macOS. Homebrew must already be installed and available in PATH;
follow [Homebrew's installation instructions](https://docs.brew.sh/Installation). macOS may first
ask you to install Apple's Command Line Tools when you run `make`.

## What gets installed

| Component | Normal installation | With `DEV=1` |
| --- | --- | --- |
| System tools | Missing uv, ffmpeg/ffprobe, llama.cpp | Same |
| Python | Python 3.12 if needed; project dependencies in `.venv` | Also pytest, pytest-cov, Ruff |
| Transcription | MLX Whisper on Apple Silicon; faster-whisper on Intel | Same |
| PDF browser | Reuse a browser found by the app; install Google Chrome if none is found | Same |
| Frontend | Use the bundled files | Install missing Node.js and Bun, install frontend dependencies, type-check and build |

Python dependencies use `uv.lock` with `uv sync --locked`; development frontend dependencies
use `bun.lock` with `bun install --frozen-lockfile`. The installer skips tools already available
in PATH and does not rewrite either lockfile. Homebrew supplies missing system packages using
its current formulae and manages their dependencies. Development requires Node.js 20.19+ or
22.12+; an incompatible existing Node installation produces instructions to update it.

Normal installation excludes the Python development dependency group. `make ui` and
`make doctor` also exclude it. For other CLI commands, use `uv run --no-dev ...` to keep
excluding development dependencies; plain `uv run` uses uv's default dependency groups.

Run from a native terminal on Apple Silicon. The installer detects Rosetta and asks you to
restart in a native terminal so the MLX backend is selected correctly. Other operating systems
receive a message pointing to the manual installation in the README.

Rerunning installation checks for existing tools and synchronizes the project environment. It
preserves configuration and meeting files; `DEV=1` regenerates the bundled frontend. A regular
installation after a development installation removes Python development packages from `.venv`.
Source audio and model weights are not downloaded by the installer.

## Verification and startup

Installation verifies tool execution, Python imports, the selected transcription backend,
configuration loading, browser discovery, and the presence of the frontend. It does not start
servers, download model weights, or perform an end-to-end transcription or PDF export.

On Apple Silicon, start the services in separate terminals:

    make model MODEL_CONTEXT=16384
    make ui CONFIG=config/mac-mini.yaml OUTPUT_ROOT="$HOME/MeetingWork"

Once the model server is ready, check the complete deployment:

    make doctor CONFIG=config/mac-mini.yaml OUTPUT_ROOT="$HOME/MeetingWork"

On Intel, omit `MODEL_CONTEXT=16384` and `CONFIG=config/mac-mini.yaml` to use the 32K context
and faster-whisper defaults. The `make model` alias matches the model identity expected by both
profiles. Its single slot and disabled prompt RAM cache match the native Mac mini runbook.

The current web workflow needs the reasoning model and a PDF browser, even if you only want to
download a transcript. Installation does not introduce transcription-only processing or automatic
deletion. Audio and generated files remain in the selected output directory.

## Connect from another computer

Enable **System Settings → General → Sharing → Remote Login** for your account on the mini.
Keep the mini awake and both services running. In a checkout on the client computer, run:

    make tunnel SSH_USER=your-mini-username SERVER_IP=192.168.1.50

Leave the tunnel running and open `http://127.0.0.1:8765` on the client. The client only needs
Make and SSH to establish the tunnel; it does not need the installer or model dependencies.
See [remote access details](local-web-studio.md#access-from-another-computer) for custom ports.

Dependency-manager references: [uv synchronization](https://docs.astral.sh/uv/concepts/projects/sync/)
and [Bun installation](https://bun.com/docs/installation).
