# Meeting Pipeline

Local-first pipeline that turns a meeting recording plus an optional previous acta into:

- timestamped transcript JSON and Markdown;
- evidence-backed structured acta JSON;
- reviewer approval file;
- branded Markdown and HTML;
- validated US Letter PDF.

The pipeline never infers speaker identity. Previous-acta participants remain unconfirmed,
and unknown owners or dates stay unresolved until review.

## Requirements

- Python 3.11+
- `uv`
- `ffmpeg` and `ffprobe`
- Chrome/Chromium for PDF export
- Optional: llama.cpp and Qwen3-8B GGUF for local reasoning

## Install

On macOS, install [Homebrew](https://brew.sh) and follow its shell PATH instructions, then run
from this checkout:

    make install

This installs missing system tools and locked Python dependencies in `.venv`, selecting MLX
Whisper on Apple Silicon or faster-whisper on Intel. It reuses an existing Chromium browser or
installs Chrome for PDF export. Normal installation uses the bundled frontend and requires no
Bun or Node.js. Python 3.12 is downloaded by uv if needed.

For development tools and a fresh frontend build:

    make install DEV=1

The installer can be rerun without overwriting configuration or recordings. It prints startup
commands for the detected platform and instructions for connecting from another computer.
See [installation details](docs/installation.md) for prerequisites and verification limits.

For manual installation on other platforms, install Python 3.11+, uv, ffmpeg/ffprobe, a
Chromium browser, and a reasoning endpoint, then run `uv sync --extra stt`. On Apple Silicon,
the equivalent Python-only command is `uv sync --extra stt-mlx`.

## Start the local reasoning model

Use the commands printed by the installer. On Apple Silicon, keep these running in separate
terminals:

    make model MODEL_CONTEXT=16384
    make ui CONFIG=config/mac-mini.yaml OUTPUT_ROOT="$HOME/MeetingWork"

On Intel, use the local profile and 32K model context:

    make model
    make ui OUTPUT_ROOT="$HOME/MeetingWork"

The pipeline never starts the model server itself. Model weights download on first use. See
[the Mac mini guide](docs/native-mac-mini.md) or [llama.cpp setup](deploy/llama-cpp/README.md).

Open the studio with `make open`. Run `make help` for development, test, build, and diagnostic
targets. Paths and ports can be overridden without editing the file, for example:

    make ui OUTPUT_ROOT=/path/to/recordings PORT=9000

## Local web studio

The browser UI can select an existing audio file or record from the Mac microphone, attach the
previous acta, monitor processing, review uncertain fields, approve the canonical data, and
download the final documents. It binds only to the loopback interface:

    uv run meeting serve \
      --output-root /Users/muramasa/Recordings \
      --config config/local-llama-cpp.yaml

Then open `http://127.0.0.1:8765`. The browser asks for microphone permission only when **Grabar**
is selected. The app does not capture system audio; browser/Teams/Zoom system audio requires a
separate virtual audio device or an exported recording.

To access a studio running on a Mac mini, enable Remote Login on the mini and run this on
your client computer:

    make tunnel SSH_USER=your-mini-username SERVER_IP=192.168.1.50

Keep the tunnel terminal open, then open `http://127.0.0.1:8765` on the client (or run
`make open` in another terminal). The model and studio must already be running on the mini;
see [the Mac mini setup](docs/native-mac-mini.md). Recording uses the client's microphone,
and downloads are saved on the client. The current pipeline also retains its working files on
the mini. See [remote access details](docs/local-web-studio.md#access-from-another-computer)
for custom ports.

## Process a meeting

    uv run meeting process \
      --audio /path/to/meeting.m4a \
      --previous-acta /path/to/previous-acta.pdf \
      --date 2026-09-07 \
      --output-root /Users/muramasa/Recordings \
      --config config/local-llama-cpp.yaml

New meetings use `YYYY-MM-DD--UUID` folders; `process` prints the generated artifact paths.
Reuse that directory for subsequent commands. Existing date-only meeting folders still work.
Use `--title` to name a meeting and `--meeting-id` to explicitly resume its identity.

This stops at `review.yaml`. Confirm attendance, proper nouns, owners, and dates, then set:

    approve_for_final_render: true

Finalize:

    uv run meeting finalize \
      --meeting-dir /path/to/meeting-directory \
      --config config/local-llama-cpp.yaml

Finalization renders Markdown and HTML, exports the PDF, and refuses completion if validation fails.

## Branding profiles

`branding` is a configuration overlay, so the packaged templates remain neutral. It supports an
organization name, inline SVG or text logo, print colors, confidentiality label, printable footer
with page numbering, and an output filename prefix. Keep organization-specific profiles outside
the repository and use one wherever a single config overlay is accepted:

    uv run meeting serve --config /path/to/organization.yaml

Change only its `branding` values. An embedded SVG keeps final HTML and PDFs self-contained and
printable offline.

## Artifact boundary

Recordings, transcripts, review files, intermediate JSON, and generated actas are local meeting
artifacts, not repository content. Keep the output root outside this checkout (the default is
`/Users/muramasa/Recordings`); never pass a directory inside the repository to `--output-root`.

The repository ignores common recording formats and the `meeting-artifacts/` and `recordings/`
directories as a backstop. Run `make hygiene` before staging changes; it fails if a recording, a
known generated meeting file such as `transcript.md`, or a file under either artifact directory is
tracked. It also rejects files under the pipeline's legacy and UUID meeting directories. The check is
read-only and never deletes files.

## Useful commands

    uv run meeting inspect --audio /path/to/meeting.m4a
    uv run meeting transcribe --meeting-dir /path/to/YYYY-MM-DD
    uv run meeting extract-context --meeting-dir /path/to/YYYY-MM-DD
    uv run meeting draft --meeting-dir /path/to/YYYY-MM-DD
    uv run meeting validate --meeting-dir /path/to/YYYY-MM-DD

## Development

    uv run pytest -q
    uv run ruff check .

See:

- `docs/native-mac-mini.md`
- `docs/weekly-workflow.md`
- `docs/troubleshooting.md`
- `docs/privacy-and-retention.md`
- `docs/model-evaluation.md`
- `docs/local-web-studio.md`

See [recording recovery and evidence review](docs/studio-reliability.md) for browser-local
recovery, same-day meetings, extraction checkpoints, and browser/release checks.
