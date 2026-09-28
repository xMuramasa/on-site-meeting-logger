# Recording recovery, evidence review, and meeting identities

New meetings have UUIDs and folders named `YYYY-MM-DD--UUID`; existing date-only folders stay
in place. The archive uses IDs, supports title/date search and status filtering, and accepts
multiple recordings on the same date. New final filenames include the first eight ID characters.
The CLI accepts `--meeting-id` and `--title`. Without an ID it reuses a unique date/source/prior-acta
match; an ambiguous match requires an explicit ID. Existing stage commands still accept
`--meeting-dir`.

The browser reuses an upload ID on retry. A matching request returns the original meeting; a
conflicting source or metadata returns HTTP 409. One deployment still processes one meeting at
a time. Other recordings remain available locally until processing can start.

## Recording recovery

Capture stores mono PCM16 recovery blocks in IndexedDB every five seconds, alongside the normal
MediaRecorder output. The saved-position indicator advances only after a successful transaction.
At 48 kHz, the recovery track uses approximately 330 MiB per hour. The browser requests persistent
storage but can deny it or run out of quota. On a write failure capture stops and offers available
audio for download; committed blocks remain recoverable. The final partial block is flushed on a
normal stop. A crash can lose the uncommitted tail, and browser scheduling delays mean the tail
is not guaranteed to be limited to five seconds.

Reopen the same browser profile and origin (same localhost name and port) to find saved sessions.
Select **Recuperar** to preview, download, or upload. Interrupted sessions recover as WAV; completed
sessions retain the normal recorded format. Recovery remains available when backend readiness
fails. A dropped SSH tunnel does not interrupt browser-local capture. Reloading while disconnected
requires reconnecting to load the application; recording storage remains on the client.

Only an acknowledged upload or **Descartar** removes local recovery data. Clearing browser data
also removes it. Server recordings are never automatically deleted. Switching to another file or
starting another recording leaves the previous recovery session available.

## Evidence review

The review workspace displays the original draft, transcript, uncertain audio ranges, and audio.
Click a citation to play its range. Existing claim text, citations, action acceptance criteria,
dependencies, owners, and dates can be corrected; an incorrect claim can be excluded. Section
structure and claim types stay fixed. Removing an action requires clearing dependencies on it.

Corrections are stored in `review.yaml`, bound to the original draft's hash. A changed draft returns
HTTP 409 rather than applying old edits to new content. Preview does not approve or render final
files. Content changes clear approval in the interface. Save marks old outputs stale; explicitly
approve again and finalize to regenerate them. Existing YAML reviews without content edits remain
supported. Claim text and provenance are never silently rewritten in the source draft.

The merge order is content edits, existing attendance/owner/date overrides, then name replacements.
Name corrections keep incomplete input while typing and validate each line on save. Malformed
review files appear as explicit errors in the studio.

## Processing recovery and checks

Validated extraction chunks are atomically checkpointed. Retry reuses matching chunks and reports
completed/total progress. Changes to extraction inputs invalidate those checkpoints; a change to
the consolidation prompt alone does not. Cancellation takes effect after the current model call
or its timeout and preserves completed extraction work. Transcription resumes at stage boundaries.

Run `make check`, then `cd webui && bun run test:browser` after installing Playwright browsers with
`bunx playwright install chromium webkit`. Browser tests use a real HTTP server, ffmpeg, filesystem
artifacts, and PDF export with synthetic audio and deterministic model adapters. They do not
measure live model accuracy. Build the wheel with `uv build`, then run
`uv run pytest tests/test_installed_wheel.py` to validate installation outside the checkout.

Before releasing, manually verify microphone selection, a five-minute recording, normal stop,
reload/crash recovery, denied storage, a disconnected SSH tunnel, and download playback in macOS
Chrome and Safari using a consenting test recording. Automated Chromium and WebKit exercise the real recorder with synthetic audio, including
reload recovery, denied persistence, a disconnected backend, and quota failures. Physical
microphone behavior remains a manual check.
