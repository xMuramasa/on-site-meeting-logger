import "fake-indexeddb/auto";
import { afterEach, expect, it } from "vitest";
import { discardRecording, listRecordings, recoverRecording, saveBlock, saveSession, type RecordingSession } from "./recordingStore";
const session = (): RecordingSession => ({ id: crypto.randomUUID(), created: new Date().toISOString(), samples: 0, sampleRate: 16000, nextBlock: 0, complete: false, persistent: true });
afterEach(async () => { for (const item of await listRecordings()) await discardRecording(item.id); });
it("recovers committed PCM after losing the capture object", async () => {
  const original = session(); await saveSession(original);
  await saveBlock(original, new Int16Array([100, -100, 0, 32767]).buffer);
  const recovered = await recoverRecording((await listRecordings())[0].id);
  const bytes = await recovered.arrayBuffer(); const view = new DataView(bytes);
  expect(recovered.type).toBe("audio/wav"); expect(bytes.byteLength).toBe(52);
  expect(view.getUint32(24, true)).toBe(16000); expect(view.getUint32(40, true)).toBe(8);
  expect(view.getInt16(44, true)).toBe(100); expect(view.getInt16(46, true)).toBe(-100);
});
it("keeps independent sessions and discards only the selected recording", async () => {
  const a = session(), b = session(); await saveSession(a); await saveSession(b);
  await saveBlock(a, new Int16Array([1, 2]).buffer); await discardRecording(a.id);
  expect((await listRecordings()).map(item => item.id)).toEqual([b.id]);
  await expect(recoverRecording(a.id)).rejects.toThrow("disponible");
});
it("does not manufacture audio for an empty interrupted recording", async () => {
  const a = session(); await saveSession(a); await expect(recoverRecording(a.id)).rejects.toThrow("bloques completos");
});
