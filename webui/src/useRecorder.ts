import { useEffect, useRef, useState } from "react";
import { recordingExtension } from "./lib";

export function useRecorder() {
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const audioContext = useRef<AudioContext | null>(null);
  const timer = useRef<number | null>(null);
  const frame = useRef<number | null>(null);

  const cleanup = () => {
    if (timer.current) window.clearInterval(timer.current);
    if (frame.current) cancelAnimationFrame(frame.current);
    stream.current?.getTracks().forEach((track) => track.stop());
    void audioContext.current?.close();
    timer.current = null;
    frame.current = null;
    stream.current = null;
    audioContext.current = null;
    setLevel(0);
  };

  useEffect(() => cleanup, []);

  const start = async () => {
    setError("");
    setFile(null);
    try {
      const media = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      stream.current = media;
      const candidates = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"];
      const mimeType = candidates.find((type) => MediaRecorder.isTypeSupported(type)) || "";
      const chunks: BlobPart[] = [];
      const instance = new MediaRecorder(media, mimeType ? { mimeType } : undefined);
      recorder.current = instance;
      instance.ondataavailable = (event) => event.data.size && chunks.push(event.data);
      instance.onstop = () => {
        const actualType = instance.mimeType || mimeType || "audio/webm";
        const blob = new Blob(chunks, { type: actualType });
        setFile(new File([blob], `grabacion.${recordingExtension(actualType)}`, { type: actualType }));
        cleanup();
      };
      const context = new AudioContext();
      audioContext.current = context;
      const analyser = context.createAnalyser();
      analyser.fftSize = 256;
      context.createMediaStreamSource(media).connect(analyser);
      const values = new Uint8Array(analyser.frequencyBinCount);
      const sample = () => {
        analyser.getByteFrequencyData(values);
        setLevel(values.reduce((sum, value) => sum + value, 0) / values.length / 255);
        frame.current = requestAnimationFrame(sample);
      };
      sample();
      setElapsed(0);
      timer.current = window.setInterval(() => setElapsed((value) => value + 1), 1000);
      instance.start(1000);
      setRecording(true);
    } catch (reason) {
      cleanup();
      setError(reason instanceof Error ? reason.message : "No se pudo acceder al micrófono");
    }
  };

  const stop = () => {
    if (recorder.current?.state === "recording") recorder.current.stop();
    setRecording(false);
  };
  const discard = () => { setFile(null); setElapsed(0); setError(""); };
  return { recording, elapsed, level, file, error, start, stop, discard };
}
