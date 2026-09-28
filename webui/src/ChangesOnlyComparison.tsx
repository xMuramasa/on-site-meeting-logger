import { formatDuration } from "./lib";

export type ComparisonCitation = { start: number; end: number; segmentIds?: number[] };
export type ComparisonEntry = {
  kind: string;
  target: string;
  label: string;
  text: string;
  fields?: Record<string, string | null | undefined>;
  citations: ComparisonCitation[];
};
export type ComparisonEdit = {
  kind: string;
  target: string;
  remove?: boolean;
  text?: string;
  fields?: Record<string, string | null | undefined>;
  evidence?: ComparisonCitation[];
};

type Change = { entry: ComparisonEntry; edit: ComparisonEdit; textChanged: boolean; fieldChanges: [string, string | null | undefined, string | null | undefined][]; citationChanges: CitationChange[] };
type CitationChange = { kind: "added" | "removed" | "replaced"; before?: ComparisonCitation; after?: ComparisonCitation };

function citationKey(citation: ComparisonCitation) {
  return `${citation.start}:${citation.end}:${(citation.segmentIds || []).join(",")}`;
}

function changedCitations(before: ComparisonCitation[], after: ComparisonCitation[]): CitationChange[] {
  const beforeKeys = new Set(before.map(citationKey));
  const afterKeys = new Set(after.map(citationKey));
  const removed = before.filter(citation => !afterKeys.has(citationKey(citation)));
  const added = after.filter(citation => !beforeKeys.has(citationKey(citation)));
  if (removed.length && added.length) {
    const shared = Math.min(removed.length, added.length);
    return [
      ...Array.from({ length: shared }, (_, index) => ({ kind: "replaced" as const, before: removed[index], after: added[index] })),
      ...added.slice(shared).map(after => ({ kind: "added" as const, after })),
      ...removed.slice(shared).map(before => ({ kind: "removed" as const, before })),
    ];
  }
  return [...added.map(after => ({ kind: "added" as const, after })), ...removed.map(before => ({ kind: "removed" as const, before }))];
}

function describeCitation(citation?: ComparisonCitation) {
  return citation ? `${formatDuration(citation.start)}–${formatDuration(citation.end)}` : "";
}

export function collectMeaningfulChanges(entries: ComparisonEntry[], edits: ComparisonEdit[]): Change[] {
  return edits.flatMap(edit => {
    const entry = entries.find(candidate => candidate.kind === edit.kind && candidate.target === edit.target);
    if (!entry) return [];
    const textChanged = edit.text !== undefined && edit.text !== entry.text;
    const fieldChanges = Object.entries(edit.fields || {}).flatMap(([field, after]) => {
      const before = entry.fields?.[field];
      return before === after ? [] : [[field, before, after] as [string, string | null | undefined, string | null | undefined]];
    });
    const citationChanges = edit.evidence ? changedCitations(entry.citations, edit.evidence) : [];
    return edit.remove || textChanged || fieldChanges.length || citationChanges.length ? [{ entry, edit, textChanged, fieldChanges, citationChanges }] : [];
  });
}

export function ChangesOnlyComparison({ entries, edits, onReturn }: { entries: ComparisonEntry[]; edits: ComparisonEdit[]; onReturn: () => void }) {
  const changes = collectMeaningfulChanges(entries, edits);
  return <section className="changes-only-comparison" aria-labelledby="changes-only-heading">
    <header className="changes-only-header">
      <div><span className="eyebrow">COMPARACIÓN</span><h2 id="changes-only-heading">Cambios frente al borrador original</h2><p>Solo se muestran modificaciones significativas. El borrador y las ediciones sin guardar no se alteran.</p></div>
      <button type="button" className="secondary-button" onClick={onReturn}>Volver al borrador completo</button>
    </header>
    <p className="changes-summary" role="status" aria-live="polite">{changes.length ? `${changes.length} ${changes.length === 1 ? "elemento modificado" : "elementos modificados"}.` : "No hay cambios significativos frente al borrador original."}</p>
    {changes.map(({ entry, edit, textChanged, fieldChanges, citationChanges }) => <article key={`${entry.kind}:${entry.target}`} className={`change-entry ${edit.remove ? "is-excluded" : ""}`}>
      <h3>{entry.label}</h3>
      {edit.remove ? <div className="change-block exclusion"><strong>Excluido del acta</strong><p>Contenido original: {entry.text}</p></div> : <>
        {textChanged && <div className="change-block"><strong>Texto modificado</strong><dl><div><dt>Original</dt><dd>{entry.text}</dd></div><div><dt>Actual</dt><dd>{edit.text}</dd></div></dl></div>}
        {fieldChanges.map(([field, before, after]) => <div className="change-block field-change" key={field}><strong>{after === null || after === "" ? `${field} excluido` : `${field} modificado`}</strong><dl><div><dt>Original</dt><dd>{before || "Sin valor"}</dd></div><div><dt>Actual</dt><dd>{after || "Excluido"}</dd></div></dl></div>)}
        {citationChanges.map((change, index) => <div className="change-block citation-change" key={`${change.kind}-${index}`}><strong>{change.kind === "added" ? "Referencia agregada" : change.kind === "removed" ? "Referencia eliminada" : "Referencia reemplazada o cambiada"}</strong><p>{change.before && <>Original: {describeCitation(change.before)}. </>}{change.after && <>Actual: {describeCitation(change.after)}.</>}</p></div>)}
      </>}
    </article>)}
  </section>;
}
