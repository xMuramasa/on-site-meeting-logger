import { useEffect, useState } from "react";
import { AlertCircle, FileCheck2, LoaderCircle, Radio, Save, ShieldCheck } from "lucide-react";
import type * as api from "./api";

export function parseCorrections(raw: string): Record<string, string> {
  const pairs: [string, string][] = []; const seen = new Set<string>();
  raw.split("\n").forEach((line, index) => {
    if (!line.trim()) return;
    const parts = line.split(/→|=>/).map(value => value.trim());
    if (parts.length !== 2 || !parts[0] || !parts[1]) throw new Error(`Línea ${index + 1}: escribe nombre incorrecto → Nombre Correcto`);
    if (seen.has(parts[0])) throw new Error(`Línea ${index + 1}: el nombre original está repetido`);
    seen.add(parts[0]); pairs.push([parts[0], parts[1]]);
  });
  return Object.fromEntries(pairs);
}

export function ReviewForm({ review, update, busy, dirty, save, finalize, preview, actionLabels = {} }: { preview?: (review: api.Review) => void; actionLabels?: Record<string, string>; review: api.Review; update: (fn: (value: api.Review) => api.Review) => void; busy: boolean; dirty: boolean; save: (review?: api.Review) => void; finalize: (review?: api.Review) => void }) {
  const [raw, setRaw] = useState(() => Object.entries(review.proper_nouns).map(([a, b]) => `${a} → ${b}`).join("\n"));
  const [correctionError, setCorrectionError] = useState("");
  const serialized = JSON.stringify(review.proper_nouns);
  useEffect(() => { setRaw(Object.entries(JSON.parse(serialized)).map(([a, b]) => `${a} → ${b}`).join("\n")); }, [serialized]);
  function edit(change: (value: api.Review) => api.Review) { update(value => ({ ...change(value), approve_for_final_render: false })); }
  function commit(final: boolean, previewOnly = false) {
    try {
      const prepared = { ...review, proper_nouns: parseCorrections(raw) };
      setCorrectionError("");
      if (previewOnly) { preview?.(prepared); return; }
      update(() => prepared);
      (final ? finalize : save)(prepared);
    } catch (error) { setCorrectionError((error as Error).message); }
  }
  return <fieldset disabled={busy} className="review-stack">
    <section className="review-card"><div className="section-title"><div><span className="step">02</span><h2>Confirmaciones humanas</h2></div><Radio size={21} /></div>
      <h3>Asistencia</h3><div className="participant-grid">{review.participants.map((person, index) => <div className="participant" key={`${person.name}-${index}`}><span className="avatar">{person.name.slice(0, 1)}</span><span className="person-copy"><strong>{person.name}</strong><small>{person.email || "Sin correo"}</small></span><select aria-label={`Asistencia de ${person.name}`} value={person.attended === true ? "yes" : person.attended === false ? "no" : ""} onChange={(e) => edit((value) => ({ ...value, participants: value.participants.map((item, i) => i === index ? { ...item, attended: e.target.value === "yes" ? true : e.target.value === "no" ? false : null } : item) }))}><option value="">Por confirmar</option><option value="yes">Asistió</option><option value="no">No asistió</option></select></div>)}</div>
      {!!Object.keys(review.owners).length && <><h3>Responsables por confirmar</h3><div className="field-grid">{Object.entries(review.owners).map(([id, owner]) => <label key={id}><span>{id}{actionLabels[id] ? ` · ${actionLabels[id]}` : ""}</span><input value={owner === "unresolved" ? "" : owner} placeholder="Nombre o dejar sin resolver" onChange={(e) => edit((value) => ({ ...value, owners: { ...value.owners, [id]: e.target.value || "unresolved" } }))} /></label>)}</div></>}
      {!!review.relative_date_actions.length && <><h3>Fechas relativas</h3><div className="field-grid">{review.relative_date_actions.map((action, index) => <label className="relative-date-field" key={action.action_id}><span>{action.action_id} · {action.action_text}</span><small>Expresión original: {action.due_expression}</small><input aria-label={`Fecha resuelta para ${action.action_id}`} type="date" value={action.resolved_date || ""} onChange={(e) => edit((value) => ({ ...value, relative_date_actions: value.relative_date_actions.map((item, itemIndex) => itemIndex === index ? { ...item, resolved_date: e.target.value || null } : item) }))} /></label>)}</div></>}
      {correctionError && <p role="alert" className="inline-error">{correctionError}</p>}<h3>Nombres propios</h3><div className="proper-nouns"><textarea aria-label="Correcciones de nombres propios" value={raw} placeholder="Una corrección por línea: nombre incorrecto → Nombre Correcto" onChange={(e) => { setRaw(e.target.value); setCorrectionError(""); update(value => ({ ...value, approve_for_final_render: false })); }} /><small>Usa una flecha por línea. Se aplicará al documento aprobado.</small></div>
      {!!review.quality_warnings.length && <div className="warning-list"><strong>Avisos de calidad</strong>{review.quality_warnings.map((warning) => <p key={warning}><AlertCircle size={14} />{warning}</p>)}</div>}
    </section>
    <section className="approval-card"><div><label className="approval-check"><input type="checkbox" checked={review.approve_for_final_render} onChange={(e) => update((value) => ({ ...value, approve_for_final_render: e.target.checked }))} /><span><ShieldCheck size={22} /><span><strong>Aprobar para render final</strong><small>Confirmo que revisé asistencia, responsables y fechas.</small></span></span></label>{dirty && <p className="stale-output" role="status">Cambios sin guardar: los entregables finales actuales quedarán desactualizados al guardar.</p>}</div><div className="approval-actions">{preview && <button type="button" className="secondary-button" disabled={busy} onClick={() => commit(false, true)}>Vista previa de correcciones</button>}<button className="secondary-button" disabled={busy} onClick={() => commit(false)}><Save size={17} /> Guardar cambios</button><button className="primary-button compact" disabled={busy || !review.approve_for_final_render} onClick={() => commit(true)}>{busy ? <LoaderCircle className="spin" size={17} /> : <FileCheck2 size={17} />} Guardar y finalizar</button></div></section>
  </fieldset>;
}

