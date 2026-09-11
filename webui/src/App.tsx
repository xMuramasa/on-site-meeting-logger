import {
  AlertCircle,
  CalendarDays,
  Check,
  ChevronRight,
  CircleStop,
  Download,
  FileAudio,
  FileCheck2,
  FileText,
  LoaderCircle,
  Mic,
  Plus,
  Radio,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldCheck,
  Sparkles,
  Upload,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import * as api from "./api";
import { AudioPreview } from "./AudioPreview";
import { audioWarning, formatDuration } from "./lib";
import { useRecorder } from "./useRecorder";

const STAGE_LABELS: Record<string, string> = {
  inspect: "Audio inspeccionado",
  transcribe: "Transcripción",
  chunk: "Fragmentos",
  extract_previous_context: "Contexto anterior",
  consolidate: "Borrador estructurado",
  generate_review: "Listo para revisión",
  approve: "Aprobado",
  render: "Documentos",
  export_pdf: "PDF",
  validate: "Validado",
};

const RECOVERY: Record<api.FailureCode, string> = {
  NO_SPEECH: "No se detectó voz. Graba o sube un audio con voz audible; no reintentes el mismo archivo.",
  MODEL_UNAVAILABLE: "El modelo local no está disponible. Revisa su instalación antes de reanudar.",
  AUDIO_DECODE_FAILED: "Convierte o vuelve a exportar el audio en un formato compatible antes de crear una nueva reunión.",
  STORAGE_UNAVAILABLE: "No se pudo guardar el trabajo local. Revisa el espacio libre y los permisos antes de reanudar.",
  JOB_INTERRUPTED: "La aplicación se interrumpió. Puedes reanudar desde la última etapa completada.",
  PROCESSING_FAILED: "No se pudo completar esta etapa. Revisa la configuración local e inténtalo de nuevo.",
};

function today() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
}

function StatusDot({ state }: { state?: string }) {
  return <span className={`status-dot ${state === "complete" ? "done" : state === "failed" ? "failed" : ""}`} />;
}

export function ProcessingFailure({ job, busy, restart }: { job: api.Job; busy: boolean; restart: () => void }) {
  const code = job.error_code || "PROCESSING_FAILED";
  const stage = STAGE_LABELS[job.stage] || job.stage;
  return <div className="error-banner"><AlertCircle size={18} /><div><strong>El procesamiento se detuvo en: {stage}</strong><span><code>{code}</code> · {RECOVERY[code]}</span></div>{job.retryable !== false && <button className="secondary-button" disabled={busy} onClick={restart}><RotateCcw size={16} /> Reanudar</button>}</div>;
}

function readinessAdvice(name: string) {
  const advice: Record<string, string> = {
    "model-endpoint": "Inicia el servidor de modelo local y confirma que responde.",
    "model-identity": "El modelo configurado no está disponible; verifica el nombre configurado.",
    "faster-whisper-model": "Instala o descarga el modelo de transcripción configurado.",
    ffmpeg: "Instala ffmpeg y ffprobe, luego vuelve a comprobar.",
    "output-permissions": "Elige una carpeta de destino local con permisos de escritura.",
    "chromium-pdf": "Instala un navegador Chromium compatible para exportar PDF.",
    "free-disk-space": "Libera espacio local antes de procesar la reunión.",
    configuration: "Corrige la configuración local y vuelve a comprobar.",
  };
  return advice[name] || "Corrige este requisito local y vuelve a comprobar.";
}

export function ReadinessPanel({
  readiness,
  busy,
  recheck,
}: {
  readiness: api.Readiness;
  busy: boolean;
  recheck: () => void;
}) {
  if (readiness.ok) return null;
  return <div className="error-banner readiness-panel"><AlertCircle size={18} /><div><strong>Antes de subir audio</strong>{readiness.checks.filter((check) => !check.ok).map((check) => <span key={check.name}><b>{check.name}:</b> {readinessAdvice(check.name)} {check.detail}</span>)}</div><button type="button" className="secondary-button" disabled={busy} onClick={recheck}><RefreshCw size={16} /> Volver a comprobar</button></div>;

}

function App() {
  const [boot, setBoot] = useState<api.Bootstrap | null>(null);
  const [items, setItems] = useState<api.MeetingSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<api.MeetingDetail | null>(null);
  const [meetingDate, setMeetingDate] = useState(today());
  const [selectedAudio, setSelectedAudio] = useState<File | null>(null);
  const [audioSource, setAudioSource] = useState<"recording" | "upload" | null>(null);
  const [previous, setPrevious] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const capture = useRecorder();

  const refresh = useCallback(async () => {
    const next = await api.meetings();
    setItems(next);
    if (selected) setDetail(await api.meeting(selected));
  }, [selected]);

  useEffect(() => {
    api.bootstrap().then(setBoot).then(refresh).catch((reason) => setError(reason.message));
  }, []); // bootstrap once

  useEffect(() => {
    const id = window.setInterval(() => refresh().catch(() => undefined), 2500);
    return () => window.clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    if (!selected) return;
    api.meeting(selected).then(setDetail).catch((reason) => setError(reason.message));
  }, [selected]);

  const current = useMemo(() => items.find((item) => item.date === selected), [items, selected]);
  useEffect(() => {
    if (!capture.file) return;
    setSelectedAudio(capture.file);
    setAudioSource("recording");
  }, [capture.file]);

  useEffect(() => {
    if (!capture.recording) return;
    setSelectedAudio(null);
    setAudioSource(null);
  }, [capture.recording]);

  async function recheckReadiness() {
    setBusy(true); setError(""); setNotice("Comprobando requisitos locales…");
    try {
      const next = await api.bootstrap();
      setBoot(next);
      await refresh();
      setNotice(next.readiness.ok ? "El entorno local está listo para procesar audio." : "Aún hay requisitos locales pendientes.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo comprobar el entorno local");
    } finally { setBusy(false); }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (capture.recording) return setError("Detén la grabación antes de crear el borrador.");
    if (!selectedAudio) return setError("Selecciona un audio o graba la reunión primero.");
    setBusy(true); setError(""); setNotice("Subiendo audio de forma local…");
    try {
      await api.uploadMeeting(meetingDate, selectedAudio, previous || undefined);
      setSelected(meetingDate);
      setNotice("Procesamiento iniciado. Puedes dejar esta pestaña abierta.");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo iniciar la reunión");
    } finally { setBusy(false); }
  }

  async function persistReview(finalize = false) {
    if (!selected || !detail?.review) return;
    setBusy(true); setError("");
    try {
      await api.saveReview(selected, detail.review);
      if (finalize) await api.finalize(selected);
      setNotice(finalize ? "Finalización iniciada." : "Revisión guardada.");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo guardar");
    } finally { setBusy(false); }
  }

  async function controlProcessing(action: "cancel" | "restart") {
    if (!selected) return;
    setBusy(true); setError("");
    try {
      await api[action](selected);
      setNotice(action === "cancel" ? "Cancelación solicitada." : "Procesamiento reanudado.");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo actualizar el procesamiento");
    } finally { setBusy(false); }
  }

  function updateReview(change: (review: api.Review) => api.Review) {
    setDetail((value) => value?.review ? { ...value, review: change(value.review) } : value);
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand"><strong>Meeting Studio</strong></div>
        <div className="local-badge"><ShieldCheck size={15} /> Solo en este Mac</div>
      </header>

      <main className="workspace">
        <aside className="sidebar">
          <div className="sidebar-heading">
            <div><span className="eyebrow">ARCHIVO</span><h2>Reuniones</h2></div>
            <button className="icon-button" onClick={() => refresh()} aria-label="Actualizar"><RefreshCw size={16} /></button>
          </div>
          <button className={`meeting-row ${selected === null ? "active" : ""}`} onClick={() => { setSelected(null); setDetail(null); }}>
            <span className="new-icon"><Plus size={18} /></span><span><strong>Nueva reunión</strong><small>Subir o grabar audio</small></span>
          </button>
          <div className="meeting-list">
            {items.map((item) => {
              const complete = item.stages.validate === "complete";
              const working = item.job?.status === "running";
              return <button key={item.date} className={`meeting-row ${selected === item.date ? "active" : ""}`} onClick={() => setSelected(item.date)}>
                <StatusDot state={complete ? "complete" : item.job?.status} />
                <span><strong>{new Date(`${item.date}T12:00:00`).toLocaleDateString("es-CL", { day: "numeric", month: "short", year: "numeric" })}</strong><small>{working ? "Procesando…" : complete ? "Acta validada" : "Revisión pendiente"}</small></span>
                <ChevronRight size={15} />
              </button>;
            })}
          </div>
          <div className="storage-note"><span>Destino local</span><code>{boot?.output_root || "…"}</code></div>
        </aside>

        <section className="content">
          {selected === null ? (
            <div className="new-meeting-grid">
              <section className="tool-heading">
                <div><h1>Nueva reunión</h1><p>Selecciona un archivo de audio o graba con el micrófono de este Mac.</p></div>
                <span className="privacy-note"><ShieldCheck size={14} /> Los archivos se procesan localmente</span>
              </section>

              <form className="capture-card" onSubmit={submit}>
                {boot && <ReadinessPanel readiness={boot.readiness} busy={busy} recheck={recheckReadiness} />}
                <div className="card-title"><div><span className="step">01</span><h2>Fuente de audio</h2></div><span className="accepted-formats">M4A · WAV · MP3 · MP4 · WEBM · OGG</span></div>
                <div className={`recorder ${capture.recording ? "is-recording" : ""}`}>
                  <div className="record-visual">
                    <button type="button" className="record-button" onClick={capture.recording ? capture.stop : capture.start} aria-label={capture.recording ? "Detener grabación" : "Grabar reunión"}>
                      {capture.recording ? <CircleStop size={25} /> : <Mic size={25} />}
                    </button>
                    <div className="record-copy"><strong>{capture.recording ? "Grabando reunión" : audioSource === "recording" && selectedAudio ? "Grabación lista" : "Grabar con este Mac"}</strong><span>{capture.recording ? formatDuration(capture.elapsed) : audioSource === "recording" && selectedAudio ? selectedAudio.name : "Usa el micrófono seleccionado en el navegador"}</span></div>
                    {capture.recording && <div className="meter" aria-label="Nivel de audio">{[.55,.8,.42,.95,.64,.38,.78,.5].map((factor, i) => <i key={i} style={{ height: `${Math.max(12, capture.level * factor * 120)}%` }} />)}</div>}
                  </div>
                  {audioSource === "recording" && selectedAudio && <button type="button" className="text-button" onClick={() => { capture.discard(); setSelectedAudio(null); setAudioSource(null); }}><RotateCcw size={14} /> Descartar</button>}
                </div>
                <p className="capture-help">El micrófono solo capta lo que oye el Mac. Para una llamada, selecciona una entrada que incluya el audio del sistema o sube la grabación de la plataforma.</p>
                <div className="or"><span>o selecciona archivos</span></div>
                <label className="file-drop">
                  <input type="file" accept="audio/*,.m4a,.mp3,.wav,.mp4,.webm,.ogg" onChange={(event) => { const file = event.target.files?.[0]; if (!file) return; capture.discard(); setSelectedAudio(file); setAudioSource("upload"); }} />
                  <span className="file-icon"><Upload size={20} /></span>
                  <span><strong>{audioSource === "upload" && selectedAudio ? `Archivo seleccionado: ${selectedAudio.name}` : "Elegir audio"}</strong><small>{audioSource === "upload" && selectedAudio ? `${(selectedAudio.size / 1048576).toFixed(1)} MB` : "M4A, WAV, MP3, MP4, WebM u OGG"}</small></span>
                </label>
                {selectedAudio && !capture.recording && <AudioPreview file={selectedAudio} />}
                {capture.recording && <p className="capture-status" role="status">Detén la grabación antes de crear el borrador.</p>}
                <div className="form-row">
                  <label><span>Fecha</span><div className="input-wrap"><CalendarDays size={16} /><input type="date" value={meetingDate} onChange={(e) => setMeetingDate(e.target.value)} required /></div></label>
                  <label><span>Acta anterior <em>opcional</em></span><div className="input-wrap file-compact"><FileText size={16} /><input type="file" accept="application/pdf,.pdf" onChange={(e) => setPrevious(e.target.files?.[0] || null)} /></div></label>
                </div>
                <button className="primary-button" disabled={!selectedAudio || capture.recording || busy || !boot?.readiness.ok}>{busy ? <LoaderCircle className="spin" size={18} /> : <Sparkles size={18} />}{busy ? "Preparando…" : "Crear borrador de acta"}</button>
                {capture.error && <p className="inline-error"><AlertCircle size={15} /> {capture.error}</p>}
                {capture.warning && <p className="inline-error"><AlertCircle size={15} /> {capture.warning}</p>}
              </form>
            </div>
          ) : (
            <div className="meeting-detail">
              <div className="detail-head"><div><span className="eyebrow accent">REUNIÓN · {selected}</span><h1>Revisión del acta</h1><p>Confirma solamente lo que una persona pueda respaldar.</p></div><div className={`job-badge ${detail?.job?.status || "idle"}`}>{detail?.job?.status === "running" && <LoaderCircle className="spin" size={15} />}{detail?.job?.status === "failed" ? "Error" : detail?.job?.status === "running" ? "Procesando" : current?.stages.validate === "complete" ? "Validada" : "Lista"}</div></div>

              {detail?.job?.status === "failed" && <ProcessingFailure job={detail.job} busy={busy} restart={() => controlProcessing("restart")} />}
              {detail?.audio_analysis && audioWarning(detail.audio_analysis.classification) && <div className="error-banner"><AlertCircle size={18} /><div><strong>Revisa la fuente de audio</strong><span>{audioWarning(detail.audio_analysis.classification)}</span></div></div>}
              {detail?.job?.status === "running" && <button className="secondary-button" disabled={busy} onClick={() => controlProcessing("cancel")}><CircleStop size={16} /> Cancelar</button>}
              {detail?.job?.status === "cancelled" && <div className="error-banner"><CircleStop size={18} /><div><strong>Procesamiento cancelado</strong><span>Puedes reanudar desde la última etapa completada.</span></div><button className="secondary-button" disabled={busy} onClick={() => controlProcessing("restart")}><RotateCcw size={16} /> Reanudar</button></div>}
              <section className="progress-card">
                {Object.entries(STAGE_LABELS).map(([key, label]) => <div className="stage" key={key}><StatusDot state={current?.stages[key]} /><span>{label}</span></div>)}
              </section>

              {!detail?.review ? <section className="empty-review"><LoaderCircle className={detail?.job?.status === "running" ? "spin" : ""} size={28} /><h2>{detail?.job?.status === "running" ? "Construyendo borrador" : "La revisión aún no está disponible"}</h2><p>Esta vista se actualizará automáticamente.</p></section> : <ReviewForm review={detail.review} update={updateReview} busy={busy} save={() => persistReview(false)} finalize={() => persistReview(true)} />}

              {!!detail?.artifacts.length && <Deliverables meetingDate={selected} artifacts={detail.artifacts} />}
            </div>
          )}
          {(notice || error) && <div className={`toast ${error ? "error" : ""}`}>{error ? <AlertCircle size={17} /> : <Check size={17} />}<span>{error || notice}</span><button onClick={() => { setError(""); setNotice(""); }}>×</button></div>}
        </section>
      </main>
    </div>
  );
}

const ROLE_LABELS: Record<api.Artifact["role"], string> = {
  minutes: "Acta",
  transcript: "Transcripción",
  digest: "Resumen",
  supporting: "Archivo de apoyo",
};

export function Deliverables({ meetingDate, artifacts }: { meetingDate: string; artifacts: api.Artifact[] }) {
  return <section className="outputs"><div className="section-title"><div><span className="step">03</span><h2>Entregables</h2></div><FileCheck2 size={21} /></div><div className="output-grid">{artifacts.map((artifact) => <a key={artifact.name} href={`/api/meetings/${meetingDate}/files/${encodeURIComponent(artifact.name)}`}><span><FileAudio size={18} /><span><strong>{artifact.final ? "Acta final" : ROLE_LABELS[artifact.role]} · {artifact.format}</strong><small>{artifact.name}</small></span></span><Download size={16} /></a>)}</div></section>;
}

export function ReviewForm({ review, update, busy, save, finalize }: { review: api.Review; update: (fn: (value: api.Review) => api.Review) => void; busy: boolean; save: () => void; finalize: () => void }) {
  return <div className="review-stack">
    <section className="review-card"><div className="section-title"><div><span className="step">02</span><h2>Confirmaciones humanas</h2></div><Radio size={21} /></div>
      <h3>Asistencia</h3><div className="participant-grid">{review.participants.map((person, index) => <div className="participant" key={`${person.name}-${index}`}><span className="avatar">{person.name.slice(0, 1)}</span><span className="person-copy"><strong>{person.name}</strong><small>{person.email || "Sin correo"}</small></span><select aria-label={`Asistencia de ${person.name}`} value={person.attended === true ? "yes" : person.attended === false ? "no" : ""} onChange={(e) => update((value) => ({ ...value, participants: value.participants.map((item, i) => i === index ? { ...item, attended: e.target.value === "yes" ? true : e.target.value === "no" ? false : null } : item) }))}><option value="">Por confirmar</option><option value="yes">Asistió</option><option value="no">No asistió</option></select></div>)}</div>
      {!!Object.keys(review.owners).length && <><h3>Responsables por confirmar</h3><div className="field-grid">{Object.entries(review.owners).map(([id, owner]) => <label key={id}><span>{id}</span><input value={owner === "unresolved" ? "" : owner} placeholder="Nombre o dejar sin resolver" onChange={(e) => update((value) => ({ ...value, owners: { ...value.owners, [id]: e.target.value || "unresolved" } }))} /></label>)}</div></>}
      {!!review.relative_date_actions.length && <><h3>Fechas relativas</h3><div className="field-grid">{review.relative_date_actions.map((action, index) => <label className="relative-date-field" key={action.action_id}><span>{action.action_id} · {action.action_text}</span><small>Expresión original: {action.due_expression}</small><input aria-label={`Fecha resuelta para ${action.action_id}`} type="date" value={action.resolved_date || ""} onChange={(e) => update((value) => ({ ...value, relative_date_actions: value.relative_date_actions.map((item, itemIndex) => itemIndex === index ? { ...item, resolved_date: e.target.value || null } : item) }))} /></label>)}</div></>}
      <h3>Nombres propios</h3><div className="proper-nouns"><textarea aria-label="Correcciones de nombres propios" value={Object.entries(review.proper_nouns).map(([from, to]) => `${from} → ${to}`).join("\n")} placeholder={'Una corrección por línea: “nombre incorrecto → Nombre Correcto”'} onChange={(e) => update((value) => ({ ...value, proper_nouns: Object.fromEntries(e.target.value.split("\n").map((line) => line.split(/\s*(?:→|=>)\s*/, 2)).filter((pair) => pair.length === 2 && pair[0] && pair[1])) }))} /><small>Usa una flecha por línea. Se aplicará al documento aprobado.</small></div>
      {!!review.quality_warnings.length && <div className="warning-list"><strong>Avisos de calidad</strong>{review.quality_warnings.map((warning) => <p key={warning}><AlertCircle size={14} />{warning}</p>)}</div>}
    </section>
    <section className="approval-card"><label className="approval-check"><input type="checkbox" checked={review.approve_for_final_render} onChange={(e) => update((value) => ({ ...value, approve_for_final_render: e.target.checked }))} /><span><ShieldCheck size={22} /><span><strong>Aprobar para render final</strong><small>Confirmo que revisé asistencia, responsables y fechas.</small></span></span></label><div className="approval-actions"><button className="secondary-button" disabled={busy} onClick={save}><Save size={17} /> Guardar</button><button className="primary-button compact" disabled={busy || !review.approve_for_final_render} onClick={finalize}>{busy ? <LoaderCircle className="spin" size={17} /> : <FileCheck2 size={17} />} Finalizar acta</button></div></section>
  </div>;
}

export default App;
