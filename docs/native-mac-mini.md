# Native deployment on a Mac mini M4 (16 GB)

Everything runs natively: no containers, no remote inference, no network egress for meeting
content. Two local models are involved — MLX Whisper for transcription and a llama.cpp
`llama-server` for reasoning — and they share the same 16 GB of unified memory.

Profile: `config/mac-mini.yaml`.

## 1. System dependencies

    brew install ffmpeg llama.cpp
    brew install --cask google-chrome        # or any Chromium build, for PDF export

`ffprobe` ships with `ffmpeg`. The PDF exporter also accepts an explicit path through
`pdf.chromium_path` in a config overlay.

## 2. Python dependencies

    cd /path/to/meeting-pipeline
    uv sync --extra stt-mlx

`stt-mlx` installs `mlx-whisper`, which is declared for macOS on arm64 only. On any other
platform the dependency resolves away and the pipeline refuses `provider: mlx-whisper` with a
message pointing back at `faster-whisper`; `uv sync --extra stt` still installs that fallback.

The Whisper weights (`mlx-community/whisper-large-v3-turbo`) download from Hugging Face on the
first transcription, not at install time.

## 3. Start the reasoning server (manually, in its own terminal)

The pipeline never starts this server and never stops it.

    llama-server \
      --hf-repo Qwen/Qwen3-8B-GGUF \
      --hf-file Qwen3-8B-Q4_K_M.gguf \
      --host 127.0.0.1 \
      --port 8080 \
      --ctx-size 16384 \
      --parallel 1 \
      --cache-ram 0 \
      --alias Qwen/Qwen3-8B-GGUF \
      --jinja \
      --chat-template-kwargs '{"enable_thinking":false}'

- `--jinja` is required for `--chat-template-kwargs` to reach the model's chat template.
- `--chat-template-kwargs '{"enable_thinking":false}'` is the supported way to put Qwen3 in
  non-thinking mode from the llama.cpp CLI. `--reasoning-budget 0` is the equivalent switch if
  the template ignores the kwarg; the provider also strips a leading `<think>…</think>` block,
  so a thinking model still parses, it just wastes output tokens.
- `--ctx-size 16384` matches `reasoning.context_window` in the profile. Raising one without the
  other either wastes memory or makes the preflight budget reject work the server could do.
- The API is loopback-only. Do not bind `0.0.0.0` without authentication.

Verify before processing:

    curl -fsS http://127.0.0.1:8080/v1/models

If the reported `id` differs from `reasoning.model` in the profile, either edit the profile or
start the server with `-a Qwen/Qwen3-8B-GGUF`; `meeting doctor` compares the two exactly.

## 4. Verify the deployment

    uv run meeting doctor \
      --output-root /Users/muramasa/Recordings \
      --config config/mac-mini.yaml

The `transcription-model` check reports the configured provider and model. It verifies that the
backend is importable on this machine; it does not force the weights to download.

## 5. Process a meeting

    uv run meeting process \
      --audio /path/to/meeting.m4a \
      --previous-acta /path/to/previous-meeting.md \
      --date 2026-09-07 \
      --output-root /Users/muramasa/Recordings \
      --config config/mac-mini.yaml

    uv run meeting finalize \
      --meeting-dir /Users/muramasa/Recordings/2026-09-07 \
      --config config/mac-mini.yaml

Or use the studio:

    uv run meeting serve \
      --output-root /Users/muramasa/Recordings \
      --config config/mac-mini.yaml

## Memory residency and serialization

`llama-server` holds the Qwen3-8B Q4_K_M weights plus its KV cache resident for as long as it
runs. Whisper large-v3-turbo is loaded on demand by the transcribe stage and released as soon
as that stage finishes: the adapter clears `mlx_whisper`'s process-wide `ModelHolder` and asks
MLX to drop its buffer cache. Both models ARE resident during transcription when llama-server
is running; serialization prevents additional meetings from adding more model copies, not
this overlap. Monitor memory pressure on the target Mac. To avoid overlap entirely, run the
CLI transcription stage first with llama-server stopped, then start llama-server and resume.
The server command limits concurrency to one slot and disables its additional prompt RAM cache.

MLX uses greedy decoding (`beam_size: 1` in the profile, omitted at the MLX API boundary).
MLX Whisper does not implement beam search or faster-whisper's VAD filter. Existing signal-level,
no-speech, low-confidence, and degraded-range checks remain, but they are not equivalent to VAD.

Because both models draw on the same pool, the pipeline allows **one expensive job per
deployment root**. A second meeting — another CLI shell, or the studio while the CLI runs —
waits briefly for the deployment lock and is then refused with a clear message rather than
competing for memory. Refusal is not a stage failure: nothing is marked failed, the studio
shows the meeting as blocked, and *Reanudar* picks it up once the deployment is free.

No throughput numbers are given here. Nothing in this repository has been benchmarked on this
hardware; measure on your own machine before promising anyone a turnaround time.

## Context budget

`reasoning.context_window` (16384) is split before every request: `max_output_tokens` (4096)
plus a fixed allowance for chat-template overhead is reserved, and the prompt, the JSON schema
and the full previous acta must fit in what remains. The check runs before the HTTP call and
uses a character-ratio estimate (`reasoning.chars_per_token`), not a tokenizer — lower that
value to be more conservative.

If a request does not fit, the run fails with the estimated and available token counts. Nothing
is truncated: shorten the previous acta, lower `chunking.target_tokens`, or raise both
`--ctx-size` and `reasoning.context_window` together.

Fixed costs under this profile, measured with the same estimator (`chars_per_token: 3.6`):

| | extraction | consolidation |
|---|---|---|
| input budget | 12032 | 12032 |
| JSON schema instruction | 417 | 2248 |
| system instructions | 235 | 212 |
| prompt template (empty slots) | 622 | 624 |
| variable part | one chunk (target 2400) | all extracted facts |
| left for the previous acta | ≈ 7900 | ≈ 8900 minus the facts |

The consolidation schema is the largest fixed cost, and the extracted-facts JSON grows with
meeting length — a long meeting plus a long previous acta is what will hit the ceiling first.
For scale, the acta fixture in this repository (4 sections, 6 actions) is ≈ 1900 output tokens,
so `max_output_tokens: 4096` leaves roughly double that headroom.

## Optional: Qwen2.5-7B-Instruct instead of Qwen3-8B

Qwen2.5-7B-Instruct is a non-reasoning instruct model, so no thinking switch is needed at all.
It is an option, not a recommendation — this repository has not compared the two on real actas.

`Qwen/Qwen2.5-7B-Instruct-GGUF` publishes Q4_K_M as a two-shard split
(`qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf` and `…-00002-of-00002.gguf`), so use the
repository quant selector and let llama.cpp resolve the shards:

    llama-server \
      --hf-repo Qwen/Qwen2.5-7B-Instruct-GGUF:Q4_K_M \
      --host 127.0.0.1 \
      --port 8080 \
      --ctx-size 16384 \
      --parallel 1 \
      --cache-ram 0 \
      --alias Qwen/Qwen2.5-7B-Instruct-GGUF \
      --jinja

`--config` accepts exactly one overlay, merged onto the packaged defaults. To switch models,
copy `config/mac-mini.yaml` and change the one line:

    cp config/mac-mini.yaml my-qwen25.yaml
    # my-qwen25.yaml
    # reasoning:
    #   model: Qwen/Qwen2.5-7B-Instruct-GGUF

Then confirm the server reports that exact id before processing:

    curl -fsS http://127.0.0.1:8080/v1/models
    uv run meeting doctor --config my-qwen25.yaml --output-root /Users/muramasa/Recordings
