# Weekly workflow

1. Obtain participant consent and record one complete `.m4a`, `.wav`, or `.mp3` file.
2. Start the configured reasoning endpoint. For local operation, use the llama.cpp runbook.
3. Run `meeting process` with the meeting date and optional previous acta.
4. Inspect `transcript.md`, especially ranges listed as low confidence.
5. Edit `review.yaml`:
   - set each participant's `attended` to true, false, or leave null;
   - replace unresolved owner values only when confirmed;
   - resolve relative dates only when the concrete date is known;
   - record proper-noun replacements as source spelling to canonical spelling;
   - keep unresolved information unresolved.
6. Set `approve_for_final_render: true`.
7. Run `meeting finalize` with the same config.
8. Check `build/validation-report.json`; finalization fails when required checks fail.
9. Distribute only the approved PDF/HTML/Markdown, not `acta-draft.json`.

The manifest makes stages resumable. Re-running the same command skips stages whose source,
configuration, prompts, and artifacts are unchanged. Use `--force` only to replace stale final
rendered artifacts; source recordings are never overwritten.
