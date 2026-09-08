# Privacy and retention

Record meetings only with participant consent. Audio, transcripts, extracted context, model
responses, review files, and actas can contain personal or commercially sensitive information.

Local mode keeps files on the Mac and sends text only to the loopback llama.cpp endpoint. A remote
`reasoning.base_url` sends transcript chunks and prior-acta excerpts to that service; this must be
an explicit deployment decision.

For hosted use:

- encrypt storage and transport;
- keep the model endpoint private and authenticated;
- use per-user authorization for meeting artifacts;
- log access and approval events without logging transcript contents or credentials;
- define deletion periods for source audio, intermediate model data, and final actas;
- keep API keys in environment/secret storage, never YAML or Git;
- test backup restoration and deletion propagation.

The CLI never deletes source material automatically. Retention deletion should be a separate,
audited operation rather than part of processing.

## Repository boundary

Keep all meeting output outside the repository. This includes source recordings, transcripts,
model extraction data, `review.yaml`, validation reports, and rendered actas. The default output
root is `/Users/muramasa/Recordings`; choose another local directory only when it is also outside
the checkout.

Before staging repository changes, run `make hygiene`. It only reads Git's tracked-file list and
fails when it finds a recording, a known generated meeting file, or content below
`meeting-artifacts/`, `recordings/`, or a `YYYY-MM-DD` meeting directory; it never removes
meeting data.
