import { AlertCircle, CheckCircle2, LoaderCircle, Mic, RotateCcw, SkipForward } from "lucide-react";
import { useEffect, useState } from "react";
import { useMicPreflight } from "./useMicPreflight";

function SamplePlayer({ file }: { file: File }) {
  const [url, setUrl] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const next = URL.createObjectURL(file);
    setUrl(next);
    setFailed(false);
    return () => URL.revokeObjectURL(next);
  }, [file]);
  if (!url) return null;
  return <>
    <audio key={url} aria-label="Reproducir prueba de micrófono" controls preload="auto" src={url} onError={() => setFailed(true)} />
    {failed && <p role="alert" className="inline-error"><AlertCircle size={15} /> El navegador no pudo reproducir la muestra. Vuelve a probar o graba igualmente y revisa la vista previa al terminar.</p>}
  </>;
}

/**
 * Optional check shown before a meeting recording. The meeting recorder stays
 * available in every state, so skipping or failing the test never blocks it.
 */
export function MicPreflight({ selectedInputId, disabled }: { selectedInputId: string; disabled: boolean }) {
  const test = useMicPreflight({ selectedInputId, busy: disabled });

  if (test.status === "skipped") {
    return <div className="mic-preflight is-collapsed" aria-label="Prueba de micrófono">
      <span>Prueba de micrófono omitida. Puedes grabar la reunión igualmente.</span>
      <button type="button" className="text-button" disabled={disabled} onClick={test.reset}><Mic size={14} /> Probar micrófono</button>
    </div>;
  }

  const active = test.status === "requesting" || test.status === "capturing";
  return <section className={`mic-preflight ${test.status === "error" ? "has-error" : ""}`} aria-label="Prueba de micrófono">
    <div className="mic-preflight-head">
      <div>
        <strong>Prueba de micrófono <em>opcional</em></strong>
        <span>Graba unos segundos con la entrada seleccionada y escúchalos antes de empezar. La muestra no se guarda.</span>
      </div>
      {test.status === "ready" && <CheckCircle2 size={18} aria-hidden="true" />}
    </div>
    {test.status === "requesting" && <p role="status"><LoaderCircle className="spin" size={14} /> Solicitando acceso al micrófono…</p>}
    {test.status === "capturing" && <div className="mic-preflight-live" role="status">
      <span>Habla ahora · {test.remaining} s</span>
      <span className="mic-preflight-meter" aria-hidden="true"><i style={{ width: `${Math.min(100, Math.round(test.level * 160))}%` }} /></span>
    </div>}
    {test.status === "ready" && test.sample && <div className="mic-preflight-result">
      <p role="status">Escucha la muestra. Si se oye clara, puedes grabar la reunión.</p>
      <SamplePlayer file={test.sample} />
      {test.warning && <p className="inline-error"><AlertCircle size={15} /> {test.warning}</p>}
    </div>}
    {test.status === "error" && test.failure && <p role="alert" className="inline-error" data-kind={test.failure.kind}><AlertCircle size={15} /> {test.failure.message}</p>}
    <div className="mic-preflight-actions">
      {active
        ? <button type="button" className="secondary-button" onClick={test.cancel}>Cancelar prueba</button>
        : <button type="button" className="secondary-button" disabled={disabled} onClick={() => { void test.run(); }}>
          {test.status === "idle" ? <><Mic size={15} /> Probar micrófono</> : <><RotateCcw size={15} /> Repetir prueba</>}
        </button>}
      <button type="button" className="text-button" disabled={active} onClick={test.skip}><SkipForward size={14} /> Omitir prueba</button>
    </div>
  </section>;
}
