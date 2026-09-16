# Troubleshooting

## The transcription model is missing

`meeting doctor` reports this as `transcription-model` and names the configured provider.

- `faster-whisper`: run `uv sync --extra stt`.
- `mlx-whisper`: run `uv sync --extra stt-mlx`. It is macOS/arm64 only; on any other machine
  switch `transcription.provider` back to `faster-whisper`.

Weights download on first use. If Hugging Face Xet stalls, retry with `HF_HUB_DISABLE_XET=1`
and keep the partially downloaded cache intact.

## mlx-whisper rejects vad_filter or device

`mlx_whisper.transcribe` has no voice-activity filter and always runs on Apple Silicon unified
memory, so the config refuses `vad_filter: true` and any `device` other than `auto` rather than
accepting them and ignoring them. Set `vad_filter: false` (the upstream `no_speech_threshold`
and `logprob_threshold` fallbacks still feed the low-confidence and degraded ranges), or use
`provider: faster-whisper` if you need the Silero VAD.

## Another meeting is already using the models

Only one expensive job runs per output root, because the transcription and reasoning models
share one machine's memory. The CLI prints the refusal and exits non-zero; the studio refuses
the request with a 409 naming the busy meeting, and a background job that loses the race is
recorded as **blocked**, not failed. Wait for the running meeting (or cancel it) and press
*Reanudar*. Nothing is lost: no stage is marked failed and the run resumes where it stopped.

## A request does not fit the context window

The pipeline estimates prompt + JSON schema + reserved output tokens before every request and
refuses instead of truncating the transcript or the previous acta. The error reports the
estimated and available token counts. Shorten the previous acta, lower
`chunking.target_tokens`, or raise `reasoning.context_window` **and** the server's `--ctx-size`
together. `reasoning.chars_per_token` tunes the estimate; lower it to be more conservative.

## Model endpoint is unavailable

Check `curl -fsS http://127.0.0.1:8080/v1/models` for llama.cpp or port 8000 for vLLM.
Confirm `reasoning.base_url`, model name, and `MEETING_MODEL_API_KEY` when authentication is enabled.

## Structured JSON fails

The client performs one bounded repair attempt. Inspect `build/model-response.json` and the
reported schema error. Do not bypass schema validation; use a stronger model, reduce chunk size,
or correct the prompt/configuration.

## Previous PDF has no text

The current MVP requires a text-layer PDF. OCR the document first, then retry. It never treats an
empty extraction as usable context.

## Chromium is not found

Set `pdf.chromium_path` in an overlay YAML to the absolute browser executable. The exporter also
checks standard macOS/Linux paths and Puppeteer/Playwright caches.

## Final files already exist

The pipeline protects approved output. Confirm the target meeting directory, then use `--force`
only if replacing those exact generated documents is intended.

## Validation rejects a trailing page

Inspect the HTML print preview. Reduce oversized unbreakable tables/callouts or adjust template
spacing; do not disable the check merely to make the run green.
