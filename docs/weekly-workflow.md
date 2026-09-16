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

## Carrying context to the next meeting

Meetings are independent. Nothing is chained implicitly: there is no conversational memory and
no "latest meeting" lookup. The only continuity is the file you pass explicitly.

After finalizing week N, its approved Markdown acta is the input for week N+1:

    uv run meeting process \
      --audio /path/to/week-n+1.m4a \
      --previous-acta /Users/muramasa/Recordings/2026-09-07/Acta_Reunion_Semanal_2026-09-07.md \
      --date 2026-09-14 \
      --config config/local-llama-cpp.yaml

In the studio, attach the same file under **Acta anterior**. PDF, Markdown, HTML, and canonical
JSON are accepted; the approved Markdown is usually the cleanest input.

The supplied file is reference data, never authority:

- it is passed inside an `untrusted_previous_acta` fence in the *user* message, never as a
  system instruction, and any delimiter inside it is neutralized before sending;
- it is supplied in full — if it does not fit the context budget the run fails with the token
  counts instead of quietly dropping the end of the document;
- its participants stay `unconfirmed`, and an owner that appears only there is recorded as
  `continuity_based` with the provenance spelled out, never as an explicit current owner.

If you supply nothing, the pipeline records "No se proporcionó acta anterior." and continues.
