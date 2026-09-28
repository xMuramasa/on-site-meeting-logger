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
  time ranges, divided by matched claims; null when there are no matches;
- `unmatched_generated_claims`: generated claims without an unused normalized text match;
- `lexical_matches`, generated/reference counts, and lexical action/decision recall;
- `unmatched_reference_actions` and `unmatched_reference_decisions`;
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

## Version 2: lexical measurements and human review

New reports carry `report_version: 2` and `matching_method: normalized_exact_text`.
Do not compare their classification counts directly with version 1: the decision/proposal
error labels now describe the reference-to-generated direction correctly. The original
reports remain unchanged on disk until another evaluation replaces the report.

`citation_precision` is null when there are no lexical matches. Reports expose the generated
and reference sample counts, one-to-one lexical matches, unmatched generated claims, unmatched
reference decisions/actions, and lexical decision/action recall. Duplicate generated claims
cannot reuse a reference claim. An unmatched sentence is **not** proof of an unsupported claim:
it may be a valid paraphrase. Lexical recall is not semantic recall.

For each model/prompt candidate, use a fixed set of privately reviewed meetings and record this
rubric locally: (1) missed decisions, (2) missed actions, (3) invented claims, (4) incorrect
owners/dates, (5) proposal/decision confusion, (6) citations that do not support the claim.
Count each reference decision/action once. A supported paraphrase is correct regardless of the
lexical score. Compare correction counts and processing time with the same baseline; do not
promote a candidate on a lexical score alone. No LLM judge is run automatically.

The labelled cases in `tests/fixtures/evaluation-cases.json` and regression tests cover zero matches, omitted and duplicate actions, paraphrases,
wrong citations, and classification direction. These tests validate measurement behavior;
real model quality still requires the private rubric above. Recordings, prompts, reviewed actas,
and any detailed reviewer notes stay outside this repository.
