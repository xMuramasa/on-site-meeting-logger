import { useEffect, useRef, useState } from "react";
import * as api from "./api";
import { formatDuration } from "./lib";

type Entry = { kind: api.ContentEdit["kind"]; target: string; label: string; claim: api.Claim };
export function entries(draft: api.Draft): Entry[] {
  const result: Entry[] = [];
  draft.sections.forEach((section, si) => {
    section.paragraphs.forEach((claim, pi) => result.push({ kind: "paragraph", target: `${si}:${pi}`, label: section.title, claim }));
    section.actions.forEach(claim => result.push({ kind: "action", target: claim.id, label: `${section.title} · ${claim.id}`, claim }));
  });
  for (const [kind, claims] of [["decision", draft.decisions], ["proposal", draft.proposals], ["risk", draft.risks], ["question", draft.open_questions]] as const) {
    claims.forEach(claim => result.push({ kind, target: claim.id, label: claim.id, claim }));
  }
  return result;
}
const LABELS = { paragraph: "Contexto", action: "Acción", decision: "Decisión", proposal: "Propuesta", risk: "Riesgo", question: "Pregunta" };
export function EvidenceWorkspace({ meetingId, review, update, onDraft, previewRequest, busy = false }: {
  meetingId: string; review: api.Review; update: (change: (review: api.Review) => api.Review) => void;
  busy?: boolean;
  previewRequest?: api.Review | null;
  onDraft?: (labels: Record<string, string>) => void;
}) {
  const [document, setDocument] = useState<{ draft: api.Draft; draft_hash: string } | null>(null);
  const [transcript, setTranscript] = useState<api.Transcript | null>(null);
  const [query, setQuery] = useState(""); const [error, setError] = useState("");
  const [preview, setPreview] = useState<api.Draft | null>(null);
  const player = useRef<HTMLAudioElement>(null); const stopAt = useRef<number | null>(null);
  useEffect(() => {
    let active = true;
    Promise.all([api.getDraft(meetingId), api.getTranscript(meetingId)]).then(([draft, text]) => {
      if (!active) return;
      setDocument(draft); setTranscript(text);
      onDraft?.(Object.fromEntries(draft.draft.sections.flatMap(s => s.actions.map(a => [a.id, a.outcome || a.id]))));
    }).catch(reason => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, [meetingId, review.draft_hash]);
  useEffect(() => { setPreview(null); }, [review]);
  useEffect(() => {
    if (!previewRequest) return;
    let active = true; setError("");
    api.previewReview(meetingId, previewRequest).then(value => { if (active) setPreview(value); }).catch(reason => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, [meetingId, previewRequest]);
  function seek(range: api.Evidence) {
    if (!player.current) return;
    player.current.currentTime = range.start; stopAt.current = range.end;
    void player.current.play().catch(() => setError("No se pudo reproducir el audio. Descárgalo para escucharlo localmente."));
  }
  function edit(entry: Entry, patch: Partial<api.ContentEdit>) {
    if (!document) return;
    update(value => {
      const next = [...(value.content_edits || [])];
      const index = next.findIndex(e => e.kind === entry.kind && e.target === entry.target);
      const changed = { ...(next[index] || { kind: entry.kind, target: entry.target }), ...patch };
      if (index < 0) next.push(changed); else next[index] = changed;
      const owners = { ...value.owners };
      if ("owner" in patch || "clear_owner" in patch) delete owners[entry.target];
      const dates = "due_date" in patch || "clear_due_date" in patch ? value.relative_date_actions.filter(a => a.action_id !== entry.target) : value.relative_date_actions;
      return { ...value, owners, relative_date_actions: dates, content_edits: next, draft_hash: document.draft_hash, approve_for_final_render: false };
    });
  }
  const citations = (ranges: api.Evidence[]) => ranges.map((range, i) => <button type="button" className="citation" key={i} onClick={() => seek(range)}>{formatDuration(range.start)}–{formatDuration(range.end)}</button>);
  return <fieldset disabled={busy} className="evidence-workspace">
    <h2>Revisar borrador y evidencia</h2>
    {error && <p role="alert" className="inline-error">{error}</p>}
    <audio aria-label="Audio de la reunión" ref={player} controls preload="metadata" src={`/api/meetings/${meetingId}/audio`} onTimeUpdate={() => { if (player.current && stopAt.current !== null && player.current.currentTime >= stopAt.current) { player.current.pause(); stopAt.current = null; } }} />
    <a href={`/api/meetings/${meetingId}/audio`} download>Descargar audio original</a>
    {document && <div className="evidence-columns"><div className="draft-editor">
      <h3>{document.draft.meeting.title}</h3><p>{document.draft.participants.map(p => p.name).join(", ")}</p><p>{document.draft.participants_note}</p><p>{document.draft.continuity_note}</p>
      {entries(document.draft).map(entry => {
        const change = review.content_edits?.find(e => e.kind === entry.kind && e.target === entry.target);
        const evidence = change?.evidence ?? entry.claim.evidence;
        const original = entry.claim.text || entry.claim.outcome || entry.claim.statement || entry.claim.question || "";
        return <article className={`claim-editor ${change?.remove ? "removed" : ""}`} key={`${entry.kind}:${entry.target}`}>
          <strong>{LABELS[entry.kind]} · {entry.label}</strong>
          <label><input type="checkbox" checked={change?.remove || false} onChange={e => edit(entry, { remove: e.target.checked })} /> Excluir del acta</label>
          {!change?.remove && <><textarea aria-label={`Texto ${entry.target}`} value={change?.text ?? original} onChange={e => edit(entry, { text: e.target.value })} />
            {citations(evidence)}
            <details><summary>Editar referencias de audio</summary>{evidence.map((range, i) => <div className="evidence-range" key={i}>
              <label>Inicio (s)<input aria-label={`Inicio ${entry.target} ${i + 1}`} type="number" min="0" step="0.1" value={range.start} onChange={e => edit(entry, { evidence: evidence.map((r, j) => i === j ? { ...r, start: Number(e.target.value), segment_ids: [] } : r) })} /></label>
              <label>Fin (s)<input aria-label={`Fin ${entry.target} ${i + 1}`} type="number" min="0" step="0.1" value={range.end} onChange={e => edit(entry, { evidence: evidence.map((r, j) => i === j ? { ...r, end: Number(e.target.value), segment_ids: [] } : r) })} /></label>
              <button type="button" onClick={() => edit(entry, { evidence: evidence.filter((_, j) => i !== j) })}>Quitar referencia</button>
            </div>)}<button type="button" onClick={() => edit(entry, { evidence: [...evidence, { start: player.current?.currentTime || 0, end: player.current?.currentTime || 0, segment_ids: [] }] })}>Agregar referencia</button></details>
            {entry.kind === "action" && <div className="action-fields">
              <label>Responsable<input aria-label={`Responsable ${entry.target}`} value={change?.clear_owner ? "" : change?.owner ?? (review.owners[entry.target] === "unresolved" ? "" : review.owners[entry.target]) ?? entry.claim.owner ?? ""} onChange={e => edit(entry, { owner: e.target.value || null, clear_owner: !e.target.value })} /></label>
              <label>Fecha<input aria-label={`Fecha ${entry.target}`} type="date" value={change?.clear_due_date ? "" : change?.due_date ?? entry.claim.due_date ?? ""} onChange={e => edit(entry, { due_date: e.target.value || null, clear_due_date: !e.target.value })} /></label>
              <label>Criterio de aceptación<input aria-label={`Aceptación ${entry.target}`} value={change?.acceptance ?? entry.claim.acceptance ?? ""} onChange={e => edit(entry, { acceptance: e.target.value })} /></label>
              <label>Depende de<select multiple aria-label={`Dependencias ${entry.target}`} value={change?.dependencies ?? entry.claim.dependencies ?? []} onChange={e => edit(entry, { dependencies: Array.from(e.target.selectedOptions, o => o.value) })}>{document.draft.sections.flatMap(s => s.actions).filter(a => a.id !== entry.target).map(a => <option key={a.id} value={a.id}>{a.id} · {a.outcome}</option>)}</select></label>
            </div>}
          </>}
        </article>;
      })}
      {document.draft.prior_follow_ups.map((item, i) => <p key={i}><strong>{item.item}</strong> {item.detail} {citations(item.evidence)}</p>)}
      {document.draft.quality_warnings.map((warning, i) => <p key={i}>{warning.note}</p>)}<p>{document.draft.sources_note}</p>
      {preview && <section aria-label="Vista previa corregida"><h3>Vista previa corregida</h3>{entries(preview).map(entry => <p key={`${entry.kind}:${entry.target}`}><strong>{entry.label}</strong> {entry.claim.text || entry.claim.outcome || entry.claim.statement || entry.claim.question} {entry.kind === "action" && <span> · {entry.claim.owner || "Responsable por confirmar"} · {entry.claim.due_date || "Sin fecha"}</span>}{citations(entry.claim.evidence)}</p>)}</section>}
    </div><aside className="transcript-panel"><h3>Transcripción</h3><input aria-label="Buscar en transcripción" placeholder="Buscar palabras" value={query} onChange={e => setQuery(e.target.value)} />
      {transcript?.segments.filter(s => s.text.toLocaleLowerCase().includes(query.toLocaleLowerCase())).map(segment => {
        const uncertain = [...transcript.low_confidence_ranges, ...transcript.degraded_ranges].some(range => range.start < segment.end && range.end > segment.start);
        return <p key={segment.id} className={uncertain ? "uncertain" : ""}>{citations([{ start: segment.start, end: segment.end, segment_ids: [segment.id] }])}{uncertain && <span> Revisar audio · </span>}{segment.text}</p>;
      })}
    </aside></div>}
  </fieldset>;
}
