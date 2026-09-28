/** Browser-local, transactional recovery storage. Audio never enters localStorage. */
export type RecordingSession = {
  id: string; created: string; sampleRate: number; samples: number; nextBlock: number;
  complete: boolean; file?: Blob | ArrayBuffer; mimeType?: string; filename?: string; persistent: boolean;
};
const DATABASE = "meeting-studio-recordings";
function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore("sessions", { keyPath: "id" });
      request.result.createObjectStore("blocks", { keyPath: ["id", "index"] });
    };
    request.onerror = () => reject(request.error);
    request.onsuccess = () => resolve(request.result);
  });
}
async function transaction<T>(mode: IDBTransactionMode, run: (tx: IDBTransaction, done: (value: T) => void) => void): Promise<T> {
  const db = await open();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(["sessions", "blocks"], mode);
    let value: T;
    tx.oncomplete = () => { db.close(); resolve(value); };
    tx.onabort = () => { db.close(); reject(tx.error || new Error("No se pudo guardar el audio")); };
    tx.onerror = () => {}; // onabort is the final transaction outcome
    try { run(tx, (next) => { value = next; }); } catch (error) { tx.abort(); reject(error); }
  });
}
export function listRecordings(): Promise<RecordingSession[]> {
  return transaction("readonly", (tx, done) => {
    const req = tx.objectStore("sessions").getAll();
    req.onsuccess = () => done(req.result.sort((a: RecordingSession, b: RecordingSession) => b.created.localeCompare(a.created)));
  });
}
export function saveSession(session: RecordingSession): Promise<void> {
  return transaction("readwrite", (tx, done) => { tx.objectStore("sessions").put(session); done(); });
}
export function saveBlock(session: RecordingSession, data: ArrayBuffer): Promise<void> {
  const next = { ...session, samples: session.samples + data.byteLength / 2, nextBlock: session.nextBlock + 1 };
  return transaction<void>("readwrite", (tx, done) => {
    tx.objectStore("blocks").put({ id: session.id, index: session.nextBlock, data });
    tx.objectStore("sessions").put(next);
    done();
  }).then(() => { Object.assign(session, next); });
}
export function discardRecording(id: string): Promise<void> {
  return transaction("readwrite", (tx, done) => {
    tx.objectStore("sessions").delete(id);
    tx.objectStore("blocks").delete(IDBKeyRange.bound([id, 0], [id, Number.MAX_SAFE_INTEGER]));
    done();
  });
}
export function wavHeader(samples: number, sampleRate: number): ArrayBuffer {
  if (samples * 2 > 0xffffffff - 36) throw new Error("La grabación supera el límite WAV de 4 GiB");
  const header = new ArrayBuffer(44); const view = new DataView(header);
  function label(at: number, value: string) { [...value].forEach((c, i) => view.setUint8(at + i, c.charCodeAt(0))); }
  label(0, "RIFF"); view.setUint32(4, 36 + samples * 2, true); label(8, "WAVE");
  label(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  label(36, "data"); view.setUint32(40, samples * 2, true); return header;
}
export async function recoverRecording(id: string): Promise<File> {
  const { session, blocks } = await transaction<{ session: RecordingSession; blocks: { index: number; data: ArrayBuffer }[] }>("readonly", (tx, done) => {
    const session = tx.objectStore("sessions").get(id);
    const blocks = tx.objectStore("blocks").getAll(IDBKeyRange.bound([id, 0], [id, Number.MAX_SAFE_INTEGER]));
    blocks.onsuccess = () => done({ session: session.result, blocks: blocks.result });
  });
  if (!session) throw new Error("La grabación ya no está disponible");
  if (session.file) return new File([session.file], session.filename || "grabacion.webm", { type: session.mimeType || (session.file instanceof Blob ? session.file.type : "audio/webm") });
  if (!session.samples || blocks.length !== session.nextBlock || blocks.some((b, i) => b.index !== i)) throw new Error("No hay bloques completos para recuperar");
  return new File([wavHeader(session.samples, session.sampleRate), ...blocks.map(b => b.data)], `recuperada-${id.slice(0, 8)}.wav`, { type: "audio/wav" });
}
