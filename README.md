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

    cd /Users/muramasa/Github/Personal/meeting-pipeline
    uv sync --extra stt

## Start the local reasoning model

Follow `deploy/llama-cpp/README.md`. The server is deliberately not started by the pipeline.

The shortest setup uses the Makefile:

    make setup

Then keep these running in separate terminals:

    make model
    make ui

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

## Process a meeting

    uv run meeting process \
      --audio /path/to/meeting.m4a \
      --previous-acta /path/to/previous-acta.pdf \
      --date 2026-09-07 \
      --output-root /Users/muramasa/Recordings \
      --config config/local-llama-cpp.yaml

This stops at `review.yaml`. Confirm attendance, proper nouns, owners, and dates, then set:

    approve_for_final_render: true

Finalize:

    uv run meeting finalize \
      --meeting-dir /Users/muramasa/Recordings/2026-09-07 \
      --config config/local-llama-cpp.yaml

Finalization renders Markdown and HTML, exports the PDF, and refuses completion if validation fails.

## Artifact boundary

Recordings, transcripts, review files, intermediate JSON, and generated actas are local meeting
artifacts, not repository content. Keep the output root outside this checkout (the default is
`/Users/muramasa/Recordings`); never pass a directory inside the repository to `--output-root`.

The repository ignores common recording formats and the `meeting-artifacts/` and `recordings/`
directories as a backstop. Run `make hygiene` before staging changes; it fails if a recording, a
known generated meeting file such as `transcript.md`, or a file under either artifact directory is
tracked. It also rejects files under the pipeline's `YYYY-MM-DD` meeting directories. The check is
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

- `docs/weekly-workflow.md`
- `docs/troubleshooting.md`
- `docs/privacy-and-retention.md`
- `docs/model-evaluation.md`
- `docs/local-web-studio.md`
