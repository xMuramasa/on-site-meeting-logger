import { useEffect, useState } from "react";

export function AudioPreview({ file }: { file: File }) {
  const [url, setUrl] = useState("");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const next = URL.createObjectURL(file);
    setUrl(next);
    setFailed(false);
    return () => URL.revokeObjectURL(next);
  }, [file]);

  return <section aria-label="Vista previa de audio">
    <h3>Escucha antes de procesar</h3>
    <p>{file.name}</p>
    <p>Comprueba que se escuchen las voces. Si solo hay silencio, revisa el micrófono o el archivo original.</p>
    {url && <audio key={url} aria-label="Reproducir audio seleccionado" controls preload="metadata" src={url} onError={() => setFailed(true)} style={{ width: "100%" }} />}
    {failed && <p role="alert" className="inline-error">El navegador no puede reproducir este archivo. Compruébalo con un reproductor local antes de procesarlo; esto no significa que esté en silencio.</p>}
    <p>La vista previa es local y no sube el archivo. El micrófono no captura automáticamente el audio del sistema.</p>
  </section>;
}
