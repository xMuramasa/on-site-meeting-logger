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

## Access from another computer

On the Mac mini, follow [the native setup guide](native-mac-mini.md), start the reasoning
server and studio, and enable **System Settings → General → Sharing → Remote Login** for
your account. Keep the mini awake while processing.

On the client computer, run from this checkout:

    make tunnel SSH_USER=your-mini-username SERVER_IP=192.168.1.50

Replace the username and IP address with the mini's values. SSH handles authentication using
your configured key or an interactive password prompt. Leave the terminal running, then open
`http://127.0.0.1:8765` or run `make open` in another terminal. Press Ctrl+C to close the tunnel.
The target forwards the client's loopback port to the studio's loopback port on the mini; it
does not install dependencies or start the remote app.

If port 8765 is occupied, use the same `PORT` on both computers so the studio's trusted Origin
checks match the browser URL:

    # On the mini, with the reasoning server already running:
    make ui CONFIG=config/mac-mini.yaml OUTPUT_ROOT="$HOME/MeetingWork" PORT=9000

    # On the client:
    make tunnel SSH_USER=your-mini-username SERVER_IP=192.168.1.50 PORT=9000
    # In another client terminal:
    make open PORT=9000

The browser uses the client's microphone and saves downloads on the client. The localhost URL
supports browser microphone access through the tunnel. Uploaded audio and pipeline artifacts
are still stored under the mini's output root; this target does not add automatic cleanup or a
transcription-only mode.

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

## Recovery and evidence review

The studio now saves browser-local recording recovery data, supports multiple meetings per day,
and displays the draft alongside a searchable transcript and clickable audio citations. See
[the reliability guide](studio-reliability.md) for recovery limits, structured corrections,
legacy directory compatibility, and browser checks.
