# Model evaluation

Default candidate: official Qwen3-8B Q4_K_M through llama.cpp, with a 32,768-token context.
Transcription defaults to faster-whisper medium.

A live Qwen model has not been downloaded by the repository setup or automated test suite. This is
intentional: the GGUF is about 5 GB. After a meeting has been reviewed and finalized, replay it
against the local model:

    uv run meeting evaluate \
      --meeting-dir /Users/muramasa/Recordings/2026-09-07 \
      --config config/local-llama-cpp.yaml

The command requires `build/acta-approved.json`, reuses only local artifacts, and writes
`build/evaluation-report.json`. The report contains aggregate counts and resource measurements
only; it does not contain transcript excerpts, prompts, claim text, IDs, model responses, source
filenames, or hashes. Keep the meeting directory outside the repository as required by the privacy
boundary.

The report records:

- `citation_precision`: generated claims that text-match the approved acta and overlap its cited
  time ranges, divided by all text-matched generated claims;
- `unsupported_claims`: generated evidenced claims with no text match in the approved acta;
- `decision_as_proposal_errors` and `proposal_as_decision_errors`;
- `schema_repairs`: bounded repair attempts observed across replay model calls;
- `human_corrections`: field-level differences between the original draft and approved acta;
- `runtime_seconds`, process `peak_memory_mib`, and `model_calls`.

These metrics compare a new replay to the human-approved acta; they are a review aid, not an
automatic acceptance gate. Inspect regression trends across several meetings before changing the
model or prompt.

Promote Qwen3-14B only if the 8B model misses the agreed quality threshold. Move from llama.cpp to
vLLM only when concurrent hosted jobs justify a persistent GPU service.

Model source and GGUF inventory:
https://huggingface.co/Qwen/Qwen3-8B-GGUF
