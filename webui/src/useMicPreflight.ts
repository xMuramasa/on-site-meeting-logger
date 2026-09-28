import { useCallback, useEffect, useRef, useState } from "react";
import { audioWarning, recordingExtension } from "./lib";

/** Length of the preflight sample. Short enough to be quick, long enough to say a sentence. */
export const MIC_TEST_MS = 4000;

export type MicTestStatus = "idle" | "requesting" | "capturing" | "ready" | "error" | "skipped";
export type MicTestErrorKind = "permission" | "unavailable" | "busy" | "unsupported" | "capture";

export type MicTestFailure = { kind: MicTestErrorKind; message: string };

/** Map getUserMedia/MediaRecorder failures to an actionable, user-facing explanation. */
export function micTestFailure(reason: unknown): MicTestFailure {
  const name = reason instanceof DOMException || reason instanceof Error ? reason.name : "";
  if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError") {
    return {
      kind: "permission",
      message: "El navegador o macOS bloqueó el micrófono. Permite el acceso en el icono de permisos de la barra de direcciones y en Ajustes del Sistema › Privacidad y seguridad › Micrófono; luego vuelve a probar.",
    };
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError") {
    return {
      kind: "unavailable",
      message: "El micrófono seleccionado no está disponible. Conéctalo o elige otra entrada y vuelve a probar.",
    };
  }
  if (name === "NotReadableError" || name === "TrackStartError" || name === "AbortError") {
    return {
      kind: "busy",
      message: "No se pudo leer el micrófono. Puede estar en uso por otra aplicación; ciérrala o elige otra entrada y vuelve a probar.",
    };
  }
  if (name === "UnsupportedError") {
    return { kind: "unsupported", message: "Este navegador no permite probar ni grabar con micrófono. Usa Chrome o Safari actualizados, o sube un archivo de audio." };
  }
  return { kind: "capture", message: "La prueba de micrófono falló. Vuelve a intentarlo; si persiste, elige otra entrada o sube un archivo de audio." };
}

function unsupported() {
  const error = new Error("unsupported");
  error.name = "UnsupportedError";
  return error;
}

type Options = { selectedInputId: string; busy: boolean; durationMs?: number };

/**
 * Pre-recording microphone check. It captures a short in-memory sample from the
 * selected input so the user can play it back. It never touches the recovery
 * store: nothing is written to IndexedDB and the stream is released before the
 * real recording starts.
 */
export function useMicPreflight({ selectedInputId, busy, durationMs = MIC_TEST_MS }: Options) {
  const [status, setStatus] = useState<MicTestStatus>("idle");
  const statusRef = useRef(status);
  statusRef.current = status;
  const [failure, setFailure] = useState<MicTestFailure | null>(null);
  const [sample, setSample] = useState<File | null>(null);
  const [level, setLevel] = useState(0);
  const [warning, setWarning] = useState("");
  const [remaining, setRemaining] = useState(0);
  const attempt = useRef(0);
  const stream = useRef<MediaStream | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const context = useRef<AudioContext | null>(null);
  const timers = useRef<number[]>([]);
  const frame = useRef<number | null>(null);
  const peak = useRef(0);

  const release = useCallback(() => {
    timers.current.forEach((id) => window.clearTimeout(id));
    timers.current = [];
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
    const active = recorder.current;
    recorder.current = null;
    if (active && active.state === "recording") {
      active.onstop = null;
      try { active.stop(); } catch { /* already stopped */ }
    }
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
    void context.current?.close()?.catch?.(() => undefined);
    context.current = null;
    setLevel(0);
  }, []);

  const fail = useCallback((current: number, reason: unknown) => {
    if (attempt.current !== current) return;
    release();
    setSample(null);
    setWarning("");
    setFailure(micTestFailure(reason));
    setStatus("error");
  }, [release]);

  const cancel = useCallback(() => {
    attempt.current += 1;
    release();
    setStatus((current) => (current === "requesting" || current === "capturing" ? "idle" : current));
  }, [release]);

  const run = useCallback(async () => {
    if (busy) return;
    const current = attempt.current + 1;
    attempt.current = current;
    release();
    setSample(null);
    setFailure(null);
    setWarning("");
    setRemaining(Math.ceil(durationMs / 1000));
    peak.current = 0;
    setStatus("requesting");
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") throw unsupported();
      const media = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          ...(selectedInputId === "default" ? {} : { deviceId: { exact: selectedInputId } }),
        },
      });
      if (attempt.current !== current) { media.getTracks().forEach((track) => track.stop()); return; }
      stream.current = media;
      const candidates = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"];
      const mimeType = candidates.find((type) => MediaRecorder.isTypeSupported?.(type)) || "";
      const chunks: BlobPart[] = [];
      const instance = new MediaRecorder(media, mimeType ? { mimeType } : undefined);
      recorder.current = instance;
      instance.ondataavailable = (event) => { if (event.data?.size) chunks.push(event.data); };
      instance.onerror = () => fail(current, new Error("capture"));
      instance.onstop = () => {
        if (attempt.current !== current) return;
        const type = instance.mimeType || mimeType || "audio/webm";
        const blob = new Blob(chunks, { type });
        recorder.current = null;
        release();
        if (!blob.size) { fail(current, new Error("empty")); return; }
        setSample(new File([blob], `prueba-microfono.${recordingExtension(type)}`, { type }));
        setWarning(audioWarning(peak.current <= 0.01 ? "silent" : peak.current <= 0.04 ? "quiet" : "normal") || "");
        setStatus("ready");
      };
      media.getTracks().forEach((track) => track.addEventListener?.("ended", () => {
        if (attempt.current !== current || instance.state !== "recording") return;
        const error = new Error("ended");
        error.name = "NotFoundError";
        fail(current, error);
      }));
      // Level metering is best-effort; a missing AudioContext must not fail the test.
      try {
        if (typeof AudioContext !== "undefined") {
          const audio = new AudioContext();
          context.current = audio;
          const analyser = audio.createAnalyser();
          analyser.fftSize = 256;
          audio.createMediaStreamSource(media).connect(analyser);
          const values = new Uint8Array(analyser.fftSize);
          const sampleLevel = () => {
            if (attempt.current !== current) return;
            analyser.getByteTimeDomainData(values);
            const next = values.reduce((highest, value) => Math.max(highest, Math.abs(value - 128) / 127), 0);
            peak.current = Math.max(peak.current, next);
            setLevel(next);
            frame.current = requestAnimationFrame(sampleLevel);
          };
          void audio.resume?.()?.catch?.(() => undefined);
          sampleLevel();
        }
      } catch { /* metering unavailable */ }
      instance.start(250);
      setStatus("capturing");
      for (let second = 1; second < Math.ceil(durationMs / 1000); second += 1) {
        timers.current.push(window.setTimeout(() => { if (attempt.current === current) setRemaining(Math.ceil(durationMs / 1000) - second); }, second * 1000));
      }
      timers.current.push(window.setTimeout(() => {
        if (attempt.current !== current || instance.state !== "recording") return;
        instance.stop();
      }, durationMs));
    } catch (reason) {
      fail(current, reason);
    }
  }, [busy, durationMs, fail, release, selectedInputId]);

  const skip = useCallback(() => {
    attempt.current += 1;
    release();
    setSample(null);
    setFailure(null);
    setWarning("");
    setStatus("skipped");
  }, [release]);

  const reset = useCallback(() => {
    attempt.current += 1;
    release();
    setSample(null);
    setFailure(null);
    setWarning("");
    setStatus("idle");
  }, [release]);

  // Free the microphone before a real recording starts so both never compete for the input.
  useEffect(() => { if (busy) cancel(); }, [busy, cancel]);

  // A sample from a different input no longer describes the selected microphone.
  const previousInput = useRef(selectedInputId);
  useEffect(() => {
    if (previousInput.current === selectedInputId) return;
    previousInput.current = selectedInputId;
    if (statusRef.current === "skipped") return;
    attempt.current += 1;
    release();
    setSample(null);
    setFailure(null);
    setWarning("");
    setStatus("idle");
  }, [release, selectedInputId]);

  useEffect(() => () => { attempt.current += 1; release(); }, [release]);

  return { status, failure, sample, level, warning, remaining, run, cancel, skip, reset };
}
