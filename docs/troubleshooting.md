# Troubleshooting

## faster-whisper is missing

Run `uv sync --extra stt`. Model weights download on first use. If Hugging Face Xet stalls,
retry with `HF_HUB_DISABLE_XET=1` and keep the partially downloaded cache intact.

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
