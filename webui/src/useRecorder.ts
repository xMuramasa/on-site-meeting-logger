import { useCallback, useEffect, useRef, useState } from "react";
import { LiveAudioDiagnostics, liveAudioWarning } from "./audioDiagnostics";
import { audioWarning, recordingExtension } from "./lib";
import { storageEstimateState, type StorageEstimateState } from "./recordingCapacity";
import { startRecovery } from "./recordingRecovery";
import { discardRecording, listRecordings, recoverRecording, type RecordingSession } from "./recordingStore";

export type AudioInput = Pick<MediaDeviceInfo, "deviceId" | "label">;

type WakeLockSentinel = {
  addEventListener: (type: "release", listener: () => void) => void;
  release: () => Promise<void>;
};

type WakeLockNavigator = Navigator & {
  wakeLock?: { request: (type: "screen") => Promise<WakeLockSentinel> };
};

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
  const [starting, setStarting] = useState(false);
  const startingRef = useRef(false);
  const [savedSeconds, setSavedSeconds] = useState(0);
  const [sessions, setSessions] = useState<RecordingSession[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const recovery = useRef<Awaited<ReturnType<typeof startRecovery>> | null>(null);
  const [storage, setStorage] = useState<StorageEstimateState>({ status: "unknown" });
  const refreshStorage = useCallback(() => {
    if (!navigator.storage?.estimate) return;
    void navigator.storage.estimate()
      .then((value) => setStorage(storageEstimateState(value)))
      .catch(() => setStorage({ status: "unknown" }));
  }, []);
  const refreshSessions = useCallback(() => {
    void listRecordings().then(setSessions).catch(() => {});
    refreshStorage();
  }, [refreshStorage]);
  useEffect(refreshSessions, [refreshSessions]);
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [warning, setWarning] = useState("");
  const [wakeLockWarning, setWakeLockWarning] = useState("");
  const [inputs, setInputs] = useState<AudioInput[]>([]);
  const [selectedInputId, setSelectedInputId] = useState("default");
  const [inputError, setInputError] = useState("");
  const [inputSupported, setInputSupported] = useState(true);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const audioContext = useRef<AudioContext | null>(null);
  const timer = useRef<number | null>(null);
  const frame = useRef<number | null>(null);
  const diagnostics = useRef(new LiveAudioDiagnostics());
  const peak = useRef(0);
  const session = useRef(0);
  const wakeLock = useRef<WakeLockSentinel | null>(null);

  const refreshInputs = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) {
      setInputSupported(false);
      return;
    }
    try {
      const nextInputs = (await navigator.mediaDevices.enumerateDevices())
        .filter((device) => device.kind === "audioinput")
        .map(({ deviceId, label }) => ({ deviceId, label }));
      setInputs(nextInputs);
      setSelectedInputId((current) => {
        if (current === "default" || nextInputs.some((device) => device.deviceId === current)) return current;
        setInputError("El micrófono seleccionado ya no está disponible. Se usará la entrada predeterminada del sistema.");
        return "default";
      });
    } catch {
      setInputError("No se pudieron actualizar los micrófonos disponibles.");
    }
  }, []);

  const releaseWakeLock = useCallback(() => {
    const sentinel = wakeLock.current;
    wakeLock.current = null;
    if (sentinel) void Promise.resolve(sentinel.release()).catch(() => undefined);
  }, []);

  const requestWakeLock = useCallback(async (currentSession: number) => {
    const api = (navigator as WakeLockNavigator).wakeLock;
    if (!api?.request) {
      setWakeLockWarning("La grabación continúa, pero este navegador no puede mantener la pantalla activa. No se garantiza la captura si el equipo se suspende.");
      return;
    }
    try {
      const sentinel = await api.request("screen");
      if (session.current !== currentSession || recorder.current?.state !== "recording") {
        void Promise.resolve(sentinel.release()).catch(() => undefined);
        return;
      }
      wakeLock.current = sentinel;
      sentinel.addEventListener("release", () => {
        if (wakeLock.current === sentinel) wakeLock.current = null;
      });
      setWakeLockWarning("");
    } catch {
      setWakeLockWarning("La grabación continúa, pero no se pudo mantener la pantalla activa. Mantén esta pestaña visible y evita que el equipo se suspenda.");
    }
  }, []);

  const cleanup = useCallback(() => {
    releaseWakeLock();
    if (timer.current) window.clearInterval(timer.current);
    if (frame.current) cancelAnimationFrame(frame.current);
    stream.current?.getTracks().forEach((track) => track.stop());
    void audioContext.current?.close();
    timer.current = null;
    frame.current = null;
    stream.current = null;
    audioContext.current = null;
    setLevel(0);
  }, [releaseWakeLock]);

  useEffect(() => () => {
    session.current += 1;
    if (recorder.current?.state === "recording") recorder.current.stop();
    cleanup();
  }, [cleanup]);

  useEffect(() => {
    void refreshInputs();
    if (!navigator.mediaDevices?.addEventListener) return;
    navigator.mediaDevices.addEventListener("devicechange", refreshInputs);
    return () => navigator.mediaDevices.removeEventListener("devicechange", refreshInputs);
  }, [refreshInputs]);

  useEffect(() => {
    const reacquireWakeLock = () => {
      if (document.visibilityState === "visible" && recorder.current?.state === "recording" && !wakeLock.current) {
        void requestWakeLock(session.current);
      }
    };
    document.addEventListener("visibilitychange", reacquireWakeLock);
    return () => document.removeEventListener("visibilitychange", reacquireWakeLock);
  }, [requestWakeLock]);

  const start = async () => {
    if (startingRef.current || recording || stopping) return;
    startingRef.current = true;
    setStarting(true);
    const currentSession = session.current + 1;
    session.current = currentSession;
    if (recorder.current?.state === "recording") recorder.current.stop();
    cleanup();
    setError("");
    setWarning("");
    setSavedSeconds(0);
    setWakeLockWarning("");
    setFile(null);
    setSessionId(null);
    diagnostics.current = new LiveAudioDiagnostics();
    peak.current = 0;
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("Este navegador no permite grabar con micrófono.");
      const media = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          ...(selectedInputId === "default" ? {} : { deviceId: { exact: selectedInputId } }),
        },
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
      instance.onstop = async () => {
        if (session.current !== currentSession) return;
        const actualType = instance.mimeType || mimeType || "audio/webm";
        const blob = new Blob(chunks, { type: actualType });
        setStopping(true);
        const completedFile = new File([blob], `grabacion.${recordingExtension(actualType)}`, { type: actualType });
        try { await recovery.current?.complete(completedFile); }
        catch { setError(current => current || "No se pudo guardar la grabación completa. Descarga el audio disponible."); }
        if (session.current !== currentSession) return;
        setFile(completedFile);
        setWarning(audioWarning(peak.current <= 0.01 ? "silent" : peak.current <= 0.04 ? "quiet" : "normal") || "");
        cleanup();
        recorder.current = null;
        setRecording(false);
        setStopping(false);
        refreshSessions();
      };
      const context = new AudioContext();
      audioContext.current = context;
      recovery.current = await startRecovery(context, media, setSavedSeconds, () => {
        setError("El guardado local falló. La grabación se detuvo; descarga el audio disponible.");
        if (instance.state === "recording") { setStopping(true); instance.stop(); }
      });
      if (session.current !== currentSession) { await recovery.current.finish(); cleanup(); return; }
      setSessionId(recovery.current.session.id);
      // A disconnected input ends its tracks without a MediaRecorder error; stop so saved blocks stay recoverable.
      media.getTracks().forEach((track) => track.addEventListener?.("ended", () => {
        if (session.current !== currentSession || instance.state !== "recording") return;
        setError("El micrófono se desconectó. La grabación se detuvo y el audio guardado sigue disponible para recuperación.");
        setStopping(true);
        instance.stop();
      }));
      if (!recovery.current.session.persistent) setWarning("El navegador no garantizó almacenamiento persistente. Descarga la grabación al terminar.");
      await context.resume();
      const analyser = context.createAnalyser();
      analyser.fftSize = 256;
      context.createMediaStreamSource(media).connect(analyser);
      const values = new Uint8Array(analyser.fftSize);
      const sample = () => {
        analyser.getByteTimeDomainData(values);
        const nextLevel = values.reduce((highest, value) => Math.max(highest, Math.abs(value - 128) / 127), 0);
        peak.current = Math.max(peak.current, nextLevel);
        setLevel(nextLevel);
        const condition = diagnostics.current.observe(nextLevel, performance.now());
        if (condition === "normal") setWarning("");
        if (condition === "silence" || condition === "clipping") setWarning(liveAudioWarning(condition));
        frame.current = requestAnimationFrame(sample);
      };
      sample();
      setElapsed(0);
      timer.current = window.setInterval(() => setElapsed((value) => value + 1), 1000);
      instance.start(1000);
      setRecording(true);
      void requestWakeLock(currentSession);
    } catch (reason) {
      cleanup();
      if (session.current === currentSession) {
        setError(recorderErrorMessage(reason));
        refreshSessions();
      }
    } finally {
      startingRef.current = false;
      setStarting(false);
    }
  };

  const stop = () => {
    if (recorder.current?.state === "recording") { setStopping(true); recorder.current.stop(); }
    releaseWakeLock();
  };
  const discard = () => {
    if (recording || stopping) return;
    if (sessionId) void discardRecording(sessionId).then(refreshSessions).catch(() => setError("No se pudo descartar la grabación"));
    setSessionId(null);
    session.current += 1;
    if (recorder.current?.state === "recording") recorder.current.stop();
    recorder.current = null;
    cleanup();
    setRecording(false);
    setFile(null);
    setElapsed(0);
    setError("");
    setWarning("");
    setWakeLockWarning("");
  };
  const selectInput = (deviceId: string) => {
    if (recording) return;
    setSelectedInputId(deviceId);
    setInputError("");
  };
  return {
    recording, elapsed, level, file, error, warning, wakeLockWarning, start, stop, discard,
    savedSeconds, sessions, sessionId, stopping, starting, storage,
    async recover(id: string) {
      try { setFile(await recoverRecording(id)); setSessionId(id); }
      catch (reason) { setError(recorderErrorMessage(reason)); }
    },
    async removeSession(id: string) {
      try { await discardRecording(id); refreshSessions(); }
      catch { setError("No se pudo descartar la grabación. Puedes intentarlo nuevamente."); }
    },
    async uploaded() {
      try { if (sessionId) await discardRecording(sessionId); }
      catch { setWarning("La reunión se guardó en el servidor. La copia de recuperación sigue en este navegador."); }
      setSessionId(null); setFile(null); refreshSessions();
    },
    detach() { if (!recording && !stopping) { setFile(null); setSessionId(null); } },
    inputs, selectedInputId, inputError, inputSupported,
    inputLabelsAvailable: inputs.some((device) => Boolean(device.label)),
    selectInput,
  };
}
