# Local web studio

The studio is a loopback-only interface over the same filesystem pipeline used by the CLI. No
meeting database or cloud account is introduced.

## Operator workflow

1. Start the llama.cpp endpoint described in `deploy/llama-cpp/README.md`.
2. Start the studio with `uv run meeting serve --config config/local-llama-cpp.yaml`.
3. Open `http://127.0.0.1:8765`.
4. Choose an existing audio file or grant microphone permission and record.
5. Select an optional previous-acta PDF and the meeting date.
6. Start processing and wait for the review form.
7. Confirm attendance, owners, dates, and proper-noun corrections. Unsupported facts should stay
   unresolved.
8. Approve and finalize, then download the validated outputs.

Browser recordings use the best format exposed by MediaRecorder: M4A/MP4 where supported, then
WebM/Opus as a fallback. ffmpeg/faster-whisper decode these formats downstream.

## Local security

- Uvicorn binds to `127.0.0.1`; it is not reachable from the LAN.
- Host validation reduces DNS-rebinding exposure.
- Mutations require a per-process CSRF token and a trusted local Origin.
- The frontend has a restrictive Content Security Policy and no CDN assets.
- Uploads are copied into the immutable meeting source directory and temporary upload files are
  removed.
- The browser receives no model API key.

Every stage start, completion, failure, and cancellation is persisted in the meeting's
`manifest.json`. The studio derives job display from that state, so a server restart retains
failure details and the last completed stage. Cancel requests a cooperative stop; Reanudar clears
that request and resumes from durable current artifacts. Only one pipeline process can change a
meeting at a time.

## Frontend development

    cd webui
    bun install
    bun run dev

Vite proxies `/api` to port 8765. For a production bundle:

    bun run test
    bun run build

The build is written to `src/meeting_pipeline/_web` and included in the Python wheel.