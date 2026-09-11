import { useCallback, useEffect, useRef, useState } from "react";
import { audioWarning, recordingExtension } from "./lib";

export function recorderErrorMessage(reason: unknown) {
  if (reason instanceof DOMException) {
    if (reason.name === "NotAllowedError" || reason.name === "SecurityError") {
      return "El navegador bloqueó el micrófono. Permite el acceso e inténtalo de nuevo.";
    }
    if (reason.name === "NotFoundError" || reason.name === "DevicesNotFoundError") {
      return "No hay un micrófono disponible. Conecta o selecciona uno y vuelve a intentarlo.";
    }
  }
  return reason instanceof Error ? reason.message : "No se pudo acceder al micrófono";
}

export function useRecorder() {
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [warning, setWarning] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const audioContext = useRef<AudioContext | null>(null);
  const timer = useRef<number | null>(null);
  const frame = useRef<number | null>(null);
  const peak = useRef(0);
  const session = useRef(0);

  const cleanup = useCallback(() => {
    if (timer.current) window.clearInterval(timer.current);
    if (frame.current) cancelAnimationFrame(frame.current);
    stream.current?.getTracks().forEach((track) => track.stop());
    void audioContext.current?.close();
    timer.current = null;
    frame.current = null;
    stream.current = null;
    audioContext.current = null;
    setLevel(0);
  }, []);

  useEffect(() => () => {
    session.current += 1;
    if (recorder.current?.state === "recording") recorder.current.stop();
    cleanup();
  }, [cleanup]);

  const start = async () => {
    const currentSession = session.current + 1;
    session.current = currentSession;
    if (recorder.current?.state === "recording") recorder.current.stop();
    cleanup();
    setError("");
    setWarning("");
    setFile(null);
    peak.current = 0;
    try {
      const media = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      if (session.current !== currentSession) {
        media.getTracks().forEach((track) => track.stop());
        return;
      }
      stream.current = media;
      const candidates = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"];
      const mimeType = candidates.find((type) => MediaRecorder.isTypeSupported(type)) || "";
      const chunks: BlobPart[] = [];
      const instance = new MediaRecorder(media, mimeType ? { mimeType } : undefined);
      recorder.current = instance;
      instance.ondataavailable = (event) => event.data.size && chunks.push(event.data);
      instance.onerror = () => {
        if (session.current !== currentSession) return;
        cleanup();
        recorder.current = null;
        setRecording(false);
        setError("La grabación se interrumpió. Revisa el micrófono y vuelve a intentarlo.");
      };
      instance.onstop = () => {
        if (session.current !== currentSession) return;
        const actualType = instance.mimeType || mimeType || "audio/webm";
        const blob = new Blob(chunks, { type: actualType });
        setFile(new File([blob], `grabacion.${recordingExtension(actualType)}`, { type: actualType }));
        setWarning(audioWarning(peak.current <= 0.01 ? "silent" : peak.current <= 0.04 ? "quiet" : "normal") || "");
        cleanup();
        recorder.current = null;
        setRecording(false);
      };
      const context = new AudioContext();
      audioContext.current = context;
      const analyser = context.createAnalyser();
      analyser.fftSize = 256;
      context.createMediaStreamSource(media).connect(analyser);
      const values = new Uint8Array(analyser.frequencyBinCount);
      const sample = () => {
        analyser.getByteFrequencyData(values);
        const nextLevel = values.reduce((sum, value) => sum + value, 0) / values.length / 255;
        peak.current = Math.max(peak.current, nextLevel);
        setLevel(nextLevel);
        frame.current = requestAnimationFrame(sample);
      };
      sample();
      setElapsed(0);
      timer.current = window.setInterval(() => setElapsed((value) => value + 1), 1000);
      instance.start(1000);
      setRecording(true);
    } catch (reason) {
      cleanup();
      if (session.current === currentSession) setError(recorderErrorMessage(reason));
    }
  };

  const stop = () => {
    if (recorder.current?.state === "recording") recorder.current.stop();
    setRecording(false);
  };
  const discard = () => {
    session.current += 1;
    if (recorder.current?.state === "recording") recorder.current.stop();
    recorder.current = null;
    cleanup();
    setRecording(false);
    setFile(null);
    setElapsed(0);
    setError("");
    setWarning("");
  };
  return { recording, elapsed, level, file, error, warning, start, stop, discard };
}
