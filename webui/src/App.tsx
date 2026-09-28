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
  Hourglass,
  LoaderCircle,
  Mic,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Upload,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "./api";
import { AudioPreview } from "./AudioPreview";
import { audioWarning, formatDuration } from "./lib";
import { useRecorder } from "./useRecorder";
import { ReviewForm } from "./ReviewForm";
import { EvidenceWorkspace } from "./EvidenceWorkspace";
export { ReviewForm } from "./ReviewForm";

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
  return <span className={`status-dot ${state === "complete" ? "done" : state === "failed" ? "failed" : state === "queued" ? "queued" : ""}`} />;
}

function meetingStatus(item: api.MeetingSummary) {
  if (item.stages.validate === "complete") return "Acta validada · lista para descargar";
  if (item.job?.status === "queued") return `En cola · posición ${item.job.position ?? "?"} · puedes cancelar`;
  if (item.job?.status === "running") return "Procesando · puedes cancelar";
  if (item.job?.status === "blocked") return "En espera · otra reunión usa los modelos";
  if (item.job?.status === "failed") return "Falló · revisa y reanuda";
  if (item.job?.status === "cancelled") return "Cancelada · puedes reanudar";
  return "Revisión pendiente · confirma y guarda";
}

export function DeploymentBlocked({ job, busy, restart }: { job: api.Job; busy: boolean; restart: () => void }) {
  const stage = STAGE_LABELS[job.stage] || job.stage;
  return <div className="error-banner"><Hourglass size={18} /><div><strong>En espera antes de: {stage}</strong><span>Otra reunión está usando los modelos locales de esta instalación. Nada se perdió; reanuda cuando termine.</span></div><button className="secondary-button" disabled={busy} onClick={restart}><RotateCcw size={16} /> Reanudar</button></div>;
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
    "transcription-model": "Instala o descarga el modelo de transcripción configurado.",
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
  const [previewRequest, setPreviewRequest] = useState<api.Review | null>(null);
  const [actionLabels, setActionLabels] = useState<Record<string, string>>({});
  const [title, setTitle] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const uploadId = useRef(crypto.randomUUID());
  const [meetingDate, setMeetingDate] = useState(today());
  const [selectedAudio, setSelectedAudio] = useState<File | null>(null);
  const [audioSource, setAudioSource] = useState<"recording" | "upload" | null>(null);
  const [previous, setPrevious] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [reviewDirty, setReviewDirty] = useState(false);
  const capture = useRecorder();
  const selectedRef = useRef(selected);
  const reviewDirtyRef = useRef(reviewDirty);

  selectedRef.current = selected;
  reviewDirtyRef.current = reviewDirty;

  const refresh = useCallback(async (forceDetail = false) => {
    try {
      const next = await api.meetings();
      setItems(next);
      const meetingDate = selectedRef.current;
      if (meetingDate && (!reviewDirtyRef.current || forceDetail)) {
        const nextDetail = await api.meeting(meetingDate);
        if (
          selectedRef.current === meetingDate
          && (!reviewDirtyRef.current || forceDetail)
        ) setDetail(nextDetail);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo actualizar las reuniones");
    }
  }, []);

  useEffect(() => {
    api.bootstrap().then(setBoot).then(() => refresh()).catch((reason) => setError(reason.message));
  }, []); // bootstrap once

  useEffect(() => {
    const id = window.setInterval(() => { void refresh(); }, 2500);
    return () => window.clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    if (!selected) return;
    if (reviewDirty) return;
    let active = true;
    api.meeting(selected)
      .then((nextDetail) => { if (active) setDetail(nextDetail); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "No se pudo abrir la reunión"); });
    return () => { active = false; };
  }, [reviewDirty, selected]);

  useEffect(() => {
    if (!reviewDirty && !capture.recording && !capture.stopping && !(selectedAudio && audioSource === "recording")) return;
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [reviewDirty, capture.recording, capture.stopping, selectedAudio, audioSource]);

  function selectMeeting(date: string | null) {
    if (date === selected) return;
    if (capture.starting || capture.recording || capture.stopping) { setError("Detén la grabación antes de cambiar de reunión."); return; }
    if (selectedAudio && audioSource === "recording" && !window.confirm("La grabación sigue guardada en este navegador. ¿Quieres cambiar de reunión antes de subirla?")) return;
    if (reviewDirty && !window.confirm("Tienes cambios sin guardar. ¿Quieres descartarlos y cambiar de reunión?")) return;
    setReviewDirty(false);
    setSelected(date);
    setDetail(null);
    setPreviewRequest(null);
    setActionLabels({});
  }

  const current = useMemo(() => items.find((item) => (item.id || item.date) === selected), [items, selected]);
  const audioInputs = capture.inputs || [];
  const selectedInputId = capture.selectedInputId || "default";
  const inputSupported = capture.inputSupported ?? true;
  const inputLabelsAvailable = capture.inputLabelsAvailable ?? true;
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
      const accepted = await api.uploadMeeting(meetingDate, selectedAudio, previous || undefined, capture.sessionId || uploadId.current, title || undefined);
      setSelected(accepted?.id || meetingDate);
      await capture.uploaded?.();
      setSelectedAudio(null); setAudioSource(null);
      uploadId.current = crypto.randomUUID();
      setNotice("Procesamiento iniciado. Puedes dejar esta pestaña abierta.");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo iniciar la reunión");
    } finally { setBusy(false); }
  }

  async function persistReview(finalize = false, prepared?: api.Review) {
    if (!selected || !detail?.review) return;
    setBusy(true); setError("");
    try {
      await api.saveReview(selected, prepared || detail.review);
      setReviewDirty(false);
      if (finalize) {
        try {
          await api.finalize(selected);
        } catch (reason) {
          setError(
            "La revisión se guardó, pero no se pudo iniciar la finalización: "
            + (reason instanceof Error ? reason.message : "error desconocido"),
          );
          await refresh(true);
          return;
        }
      }
      setNotice(finalize ? "Finalización iniciada. Los entregables se actualizarán cuando termine el proceso." : "Revisión guardada. Los entregables anteriores ya no están vigentes.");
      await refresh(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo guardar");
    } finally { setBusy(false); }
  }

  async function controlProcessing(action: "cancel" | "restart") {
    if (!selected) return;
    setBusy(true); setError("");
    try {
      await api[action](selected);
      const queued = detail?.job?.status === "queued";
      setNotice(action === "cancel" ? (queued ? "Reunión quitada de la cola." : "Cancelación solicitada.") : "Procesamiento reanudado.");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo actualizar el procesamiento");
    } finally { setBusy(false); }
  }

  function updateReview(change: (review: api.Review) => api.Review) {
    setDetail((value) => value?.review ? { ...value, review: change(value.review) } : value);
    setReviewDirty(true);
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand"><strong>Meeting Studio</strong></div>
        <div className="local-badge"><ShieldCheck size={15} /> Estudio local</div>
      </header>

      <main className="workspace">
        <aside className="sidebar">
          <div className="sidebar-heading">
            <div><span className="eyebrow">ARCHIVO</span><h2>Reuniones</h2></div>
            <button className="icon-button" onClick={() => refresh()} aria-label="Actualizar"><RefreshCw size={16} /></button>
          </div>
          <button className={`meeting-row ${selected === null ? "active" : ""}`} onClick={() => selectMeeting(null)}>
            <span className="new-icon"><Plus size={18} /></span><span><strong>Nueva reunión</strong><small>Subir o grabar audio</small></span>
          </button>
          <div className="meeting-list">
            <input aria-label="Buscar reuniones" placeholder="Buscar título o fecha" value={search} onChange={e => setSearch(e.target.value)} />
            <select aria-label="Filtrar estado" value={statusFilter} onChange={e => setStatusFilter(e.target.value)}><option value="">Todos los estados</option><option value="validated">Validadas</option><option value="pending">Pendientes</option><option value="failed">Con errores</option></select>
            {items.filter(item => `${item.title || ""} ${item.date}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()) && (!statusFilter || (statusFilter === "validated" ? item.stages.validate === "complete" : statusFilter === "failed" ? item.job?.status === "failed" : item.stages.validate !== "complete"))).map((item) => {
              const complete = item.stages.validate === "complete";
              return <button key={item.id || item.date} className={`meeting-row ${selected === (item.id || item.date) ? "active" : ""}`} onClick={() => selectMeeting(item.id || item.date)}>
                <StatusDot state={complete ? "complete" : item.job?.status} />
                <span>{item.title && <strong>{item.title}</strong>}<strong>{new Date(`${item.date}T12:00:00`).toLocaleDateString("es-CL", { day: "numeric", month: "short", year: "numeric" })}</strong><small>{meetingStatus(item)}</small></span>
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
                {!!capture.sessions?.length && <section className="recovery-list"><h3>Grabaciones guardadas en este navegador</h3>{capture.sessions.filter(item => item.id !== capture.sessionId).map(item => <div key={item.id}><span>{new Date(item.created).toLocaleString("es-CL")} · {formatDuration(item.samples / item.sampleRate)}</span><button type="button" disabled={capture.recording || capture.starting || capture.stopping || busy} onClick={() => { void capture.recover(item.id); }}>Recuperar</button><button type="button" disabled={capture.recording || capture.starting || capture.stopping || busy} onClick={() => { void capture.removeSession(item.id); }}>Descartar</button></div>)}</section>}
                {boot && <ReadinessPanel readiness={boot.readiness} busy={busy} recheck={recheckReadiness} />}
                <div className="card-title"><div><span className="step">01</span><h2>Fuente de audio</h2></div><span className="accepted-formats">M4A · WAV · MP3 · MP4 · WEBM · OGG</span></div>
                <div className={`recorder ${capture.recording ? "is-recording" : ""}`}>
                  <div className="record-visual">
                    <button type="button" className="record-button" disabled={capture.starting || capture.stopping || busy} onClick={capture.recording ? capture.stop : capture.start} aria-label={capture.recording ? "Detener grabación" : "Grabar reunión"}>
                      {capture.recording ? <CircleStop size={25} /> : <Mic size={25} />}
                    </button>
                    <div className="record-copy"><strong>{capture.recording ? "Grabando reunión" : audioSource === "recording" && selectedAudio ? "Grabación lista" : "Grabar con este Mac"}</strong><span>{capture.recording ? formatDuration(capture.elapsed) : audioSource === "recording" && selectedAudio ? selectedAudio.name : "Usa el micrófono seleccionado en el navegador"}</span></div>
                    {capture.recording && <div className="meter" aria-label="Nivel de audio">{[.55,.8,.42,.95,.64,.38,.78,.5].map((factor, i) => <i key={i} style={{ height: `${Math.max(12, capture.level * factor * 120)}%` }} />)}</div>}
                  </div>
                  {audioSource === "recording" && selectedAudio && <button type="button" className="text-button" disabled={busy || capture.stopping} onClick={() => { capture.discard(); setSelectedAudio(null); setAudioSource(null); }}><RotateCcw size={14} /> Descartar</button>}
                </div>
                {!capture.recording && capture.storage && <p className="storage-capacity" role="status">{capture.storage.status === "available" ? <>Espacio del navegador para grabar: {(capture.storage.availableBytes / 1_073_741_824).toFixed(1)} GB · capacidad aproximada de guardado local: {formatDuration(capture.storage.recordingSeconds)}.</> : <>Este navegador no informa cuánto espacio queda para el guardado local.</>}</p>}
                <p role="status">{capture.starting ? "Preparando micrófono y guardado local…" : capture.stopping ? "Guardando grabación…" : capture.recording ? `Guardado local: ${formatDuration(capture.savedSeconds || 0)}` : ""}</p><div className="input-selector">
                  <label htmlFor="microphone-input"><span>Entrada de micrófono</span><select id="microphone-input" aria-label="Entrada de micrófono" value={selectedInputId} disabled={capture.recording || capture.starting || capture.stopping || busy || !inputSupported} onChange={(event) => capture.selectInput(event.target.value)}>
                    <option value="default">Entrada predeterminada del sistema</option>
                    {audioInputs.filter((device) => device.deviceId !== "default").map((device, index) => <option key={device.deviceId} value={device.deviceId}>{device.label || `Micrófono ${index + 1}`}</option>)}
                  </select></label>
                  <small>{capture.recording ? "Detén la grabación antes de cambiar de micrófono." : "La entrada se usará cuando inicies la grabación."}</small>
                </div>
                {!inputSupported && <p className="inline-error"><AlertCircle size={15} /> Este navegador no permite enumerar ni usar entradas de micrófono.</p>}
                {!inputLabelsAvailable && inputSupported && <p className="capture-help input-note">Los nombres de los micrófonos aparecerán después de permitir el acceso. Abrir el selector no solicita permiso.</p>}
                {capture.inputError && <p className="inline-error"><AlertCircle size={15} /> {capture.inputError}</p>}
                <p className="capture-help">Este selector solo selecciona una entrada de audio; no captura automáticamente el audio del sistema. Si instalaste un dispositivo loopback, podría aparecer aquí. Para una llamada, selecciona una entrada que incluya el audio del sistema o sube la grabación de la plataforma.</p>
                <div className="or"><span>o selecciona archivos</span></div>
                <label className="file-drop">
                  <input disabled={capture.recording || capture.starting || capture.stopping || busy} type="file" accept="audio/*,.m4a,.mp3,.wav,.mp4,.webm,.ogg" onChange={(event) => { const file = event.target.files?.[0]; if (!file) return; capture.detach?.(); uploadId.current = crypto.randomUUID(); setSelectedAudio(file); setAudioSource("upload"); }} />
                  <span className="file-icon"><Upload size={20} /></span>
                  <span><strong>{audioSource === "upload" && selectedAudio ? `Archivo seleccionado: ${selectedAudio.name}` : "Elegir audio"}</strong><small>{audioSource === "upload" && selectedAudio ? `${(selectedAudio.size / 1048576).toFixed(1)} MB` : "M4A, WAV, MP3, MP4, WebM u OGG"}</small></span>
                </label>
                {selectedAudio && !capture.recording && <AudioPreview file={selectedAudio} />}
                {capture.recording && <p className="capture-status" role="status">Detén la grabación antes de crear el borrador.</p>}
                {capture.wakeLockWarning && <p className="capture-help" role="status">{capture.wakeLockWarning}</p>}
                <div className="form-row">
                  <label><span>Título</span><input disabled={busy} aria-label="Título de la reunión" placeholder="Reunión semanal" maxLength={200} value={title} onChange={e => setTitle(e.target.value)} /></label><label><span>Fecha</span><div className="input-wrap"><CalendarDays size={16} /><input disabled={busy} type="date" value={meetingDate} onChange={(e) => setMeetingDate(e.target.value)} required /></div></label>
                  <label><span>Acta anterior <em>opcional</em></span><div className="input-wrap file-compact"><FileText size={16} /><input disabled={busy} type="file" accept=".pdf,.md,.markdown,.html,.htm,.json" onChange={(e) => setPrevious(e.target.files?.[0] || null)} /></div></label>
                </div>
                <button className="primary-button" disabled={!selectedAudio || capture.recording || capture.stopping || busy || !boot?.readiness.ok}>{busy ? <LoaderCircle className="spin" size={18} /> : <Sparkles size={18} />}{busy ? "Preparando…" : "Crear borrador de acta"}</button>
                {capture.error && <p className="inline-error"><AlertCircle size={15} /> {capture.error}</p>}
                {capture.warning && <p className="inline-error"><AlertCircle size={15} /> {capture.warning}</p>}
              </form>
            </div>
          ) : (
            <div className="meeting-detail">
              <div className="detail-head"><div><span className="eyebrow accent">REUNIÓN · {detail?.date || current?.date}</span><h1>Revisión del acta</h1><p>Confirma solamente lo que una persona pueda respaldar.</p></div><div className={`job-badge ${detail?.job?.status || "idle"}`}>{detail?.job?.status === "running" && <LoaderCircle className="spin" size={15} />}{detail?.job?.status === "failed" ? "Error" : detail?.job?.status === "queued" ? `En cola · #${detail.job.position ?? "?"}` : detail?.job?.status === "cancelled" ? "Cancelada" : detail?.job?.status === "running" ? "Procesando" : detail?.job?.status === "blocked" ? "En espera" : current?.stages.validate === "complete" ? "Validada" : "Lista"}</div></div>

              {detail?.job?.status === "blocked" && <DeploymentBlocked job={detail.job} busy={busy} restart={() => controlProcessing("restart")} />}
              {detail?.job?.status === "failed" && <ProcessingFailure job={detail.job} busy={busy} restart={() => controlProcessing("restart")} />}
              {detail?.audio_analysis && audioWarning(detail.audio_analysis.classification) && <div className="error-banner"><AlertCircle size={18} /><div><strong>Revisa la fuente de audio</strong><span>{audioWarning(detail.audio_analysis.classification)}</span></div></div>}
              {detail?.job?.status === "queued" && <div className="queue-banner" role="status"><div><strong>En cola · posición {detail.job.position ?? "?"}</strong><span>{detail.job.position === 1 ? "Es la próxima reunión en procesarse." : `Se procesará después de ${(detail.job.position ?? 1) - 1} reunión(es) en orden de llegada.`}</span></div><button className="secondary-button" disabled={busy} onClick={() => controlProcessing("cancel")}><CircleStop size={16} /> Quitar de la cola</button></div>}
              {detail?.job?.status === "running" && <button className="secondary-button" disabled={busy} onClick={() => controlProcessing("cancel")}><CircleStop size={16} /> Cancelar</button>}
              {detail?.job?.status === "cancelled" && <div className="error-banner"><CircleStop size={18} /><div><strong>Procesamiento cancelado</strong><span>Puedes reanudar desde la última etapa completada.</span></div><button className="secondary-button" disabled={busy} onClick={() => controlProcessing("restart")}><RotateCcw size={16} /> Reanudar</button></div>}
              {detail?.job?.total_chunks ? <p role="status">{detail.job.phase === "consolidate" ? "Consolidando borrador" : `Fragmentos: ${detail.job.completed_chunks || 0} de ${detail.job.total_chunks}`}</p> : null}
              <section className="progress-card">
                {Object.entries(STAGE_LABELS).map(([key, label]) => <div className="stage" key={key}><StatusDot state={current?.stages[key]} /><span>{label}</span></div>)}
              </section>

              {detail?.review_error && <p role="alert" className="error-banner">{detail.review_error}</p>}
              {!detail?.review ? <section className="empty-review" aria-live="polite"><LoaderCircle className={detail?.job?.status === "running" ? "spin" : ""} size={28} /><h2>{detail?.job?.status === "running" ? "Construyendo borrador" : "La revisión aún no está disponible"}</h2><p>{detail?.job?.status === "cancelled" ? "Reanuda el procesamiento para volver a generar el borrador." : "Esta vista se actualizará automáticamente."}</p></section> : <div><EvidenceWorkspace busy={busy} previewRequest={previewRequest} onDraft={setActionLabels} key={`evidence:${selected}`} meetingId={selected} review={detail.review} update={updateReview} /><ReviewForm preview={setPreviewRequest} actionLabels={actionLabels} key={`review:${selected}`} review={detail.review} update={updateReview} busy={busy} dirty={reviewDirty} save={review => persistReview(false, review)} finalize={review => persistReview(true, review)} /></div>}

              {!!detail?.artifacts.length && <Deliverables meetingDate={selected} artifacts={detail.artifacts} />}
            </div>
          )}
          {(notice || error) && <div className={`toast ${error ? "error" : ""}`} role="status" aria-live="polite">{error ? <AlertCircle size={17} /> : <Check size={17} />}<span>{error || notice}</span><button aria-label="Cerrar aviso" onClick={() => { setError(""); setNotice(""); }}>×</button></div>}
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


export default App;
