import workletUrl from "./recovery.worklet.js?url";
import { saveBlock, saveSession, type RecordingSession } from "./recordingStore";

export async function startRecovery(context: AudioContext, stream: MediaStream, onSaved: (seconds: number) => void, onFailure: (error: unknown) => void) {
  let persistent = false;
  try { persistent = await navigator.storage?.persist?.() || false; } catch { /* permission denied is supported */ }
  const session: RecordingSession = { id: crypto.randomUUID(), created: new Date().toISOString(), sampleRate: context.sampleRate, samples: 0, nextBlock: 0, complete: false, persistent };
  await saveSession(session);
  await context.audioWorklet.addModule(workletUrl);
  const node = new AudioWorkletNode(context, "meeting-recovery");
  const source = context.createMediaStreamSource(stream); const silent = context.createGain(); silent.gain.value = 0;
  source.connect(node).connect(silent).connect(context.destination);
  let queue = Promise.resolve(); let failed = false; let resolveFlush: (() => void) | undefined;
  node.onprocessorerror = () => { if (!failed) { failed = true; onFailure(new Error("Se interrumpió el guardado de audio")); } };
  node.port.onmessage = event => {
    if (event.data.flushed) { resolveFlush?.(); return; }
    if (event.data.data && !failed) {
      queue = queue.then(() => saveBlock(session, event.data.data)).then(() => onSaved(session.samples / session.sampleRate));
      queue = queue.catch(error => { if (!failed) { failed = true; onFailure(error); } });
    }
  };
  let finishing: Promise<void> | null = null;
  const finish = () => finishing ||= (async () => {
    await new Promise<void>(resolve => {
      const timeout = window.setTimeout(resolve, 2000);
      resolveFlush = () => { clearTimeout(timeout); resolve(); };
      node.port.postMessage("flush");
    });
    source.disconnect(); node.disconnect(); silent.disconnect();
    await queue;
  })();
  return {
    session, finish,
    async complete(file: File) {
      await finish();
      // ArrayBuffer also persists in WebKit contexts that cannot clone recorder-backed Blobs.
      const next = { ...session, complete: true, file: await file.arrayBuffer(), mimeType: file.type, filename: file.name };
      await saveSession(next); Object.assign(session, next);
    },
  };
}
