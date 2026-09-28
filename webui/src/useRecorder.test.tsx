/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRecorder } from "./useRecorder";

// jsdom has no IndexedDB or AudioWorklet; recovery persistence is covered by recordingStore tests.
vi.mock("./recordingRecovery", () => ({
  startRecovery: vi.fn(async () => ({
    session: { id: "test-session", persistent: true },
    finish: vi.fn(async () => undefined),
    complete: vi.fn(async () => undefined),
  })),
}));
vi.mock("./recordingStore", () => ({
  listRecordings: vi.fn(async () => []),
  discardRecording: vi.fn(async () => undefined),
  recoverRecording: vi.fn(),
}));

let container: HTMLDivElement;
let root: Root;
const enumerateDevices = vi.fn();
const getUserMedia = vi.fn();
const wakeLockRequest = vi.fn();
const wakeLockRelease = vi.fn();
const wakeLockListeners = new Map<string, () => void>();

let lastRecorder: TestMediaRecorder | null = null;

class TestMediaRecorder {
  constructor() { lastRecorder = this; }
  static isTypeSupported() { return true; }
  state: "inactive" | "recording" = "inactive";
  mimeType = "audio/webm";
  ondataavailable: ((event: BlobEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onstop: (() => void) | null = null;
  start() { this.state = "recording"; }
  stop() { this.state = "inactive"; this.onstop?.(); }
}

const stream = { getTracks: () => [{ stop: vi.fn() }] };

function Probe() {
  const recorder = useRecorder();
  return <>
    <output data-testid="inputs">{JSON.stringify(recorder.inputs)}</output>
    <output data-testid="selected">{recorder.selectedInputId}</output>
    <output data-testid="labels">{String(recorder.inputLabelsAvailable)}</output>
    <output data-testid="recording">{String(recorder.recording)}</output>
    <output data-testid="wake-lock-warning">{recorder.wakeLockWarning}</output>
    <output data-testid="storage">{JSON.stringify(recorder.storage)}</output>
    <output data-testid="error">{recorder.error}</output>
    <button type="button" data-action="select" onClick={() => recorder.selectInput("usb-mic")}>Seleccionar USB</button>
    <button type="button" data-action="start" onClick={() => void recorder.start()}>Grabar</button>
    <button type="button" data-action="stop" onClick={recorder.stop}>Detener</button>
  </>;
}

async function renderProbe() {
  await act(async () => {
    root.render(<Probe />);
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function waitFor(check: () => boolean) {
  for (let attempt = 0; attempt < 10; attempt += 1) {
    if (check()) return;
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  }
  throw new Error("Condition was not met");
}

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  enumerateDevices.mockReset();
  getUserMedia.mockReset();
  wakeLockRequest.mockReset();
  wakeLockRelease.mockReset();
  wakeLockListeners.clear();
  wakeLockRequest.mockResolvedValue({
    addEventListener: (event: string, listener: () => void) => wakeLockListeners.set(event, listener),
    release: wakeLockRelease,
  });
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: {
      enumerateDevices,
      getUserMedia,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    },
  });
  Object.defineProperty(navigator, "wakeLock", {
    configurable: true,
    value: { request: wakeLockRequest },
  });
  vi.stubGlobal("MediaRecorder", TestMediaRecorder);
  vi.stubGlobal("AudioContext", class {
    close = vi.fn();
    resume = vi.fn(async () => undefined);
    createAnalyser = () => ({
      fftSize: 0,
      frequencyBinCount: 1,
      getByteFrequencyData: (values: Uint8Array) => values.fill(0),
      getByteTimeDomainData: (values: Uint8Array) => values.fill(128),
    });
    createMediaStreamSource = () => ({ connect: vi.fn() });
  });
  vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe("useRecorder audio inputs", () => {
  it("lists microphones without requesting permission and explains anonymous labels", async () => {
    enumerateDevices.mockResolvedValue([
      { kind: "audioinput", deviceId: "default", label: "" },
      { kind: "videoinput", deviceId: "camera", label: "Camera" },
      { kind: "audioinput", deviceId: "usb-mic", label: "" },
    ]);

    await renderProbe();
    await waitFor(() => container.querySelector('[data-testid="inputs"]')?.textContent?.includes("usb-mic") || false);

    expect(enumerateDevices).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-testid="inputs"]')?.textContent).toContain("usb-mic");
    expect(container.querySelector('[data-testid="labels"]')?.textContent).toBe("false");
  });

  it("uses the selected input when recording starts", async () => {
    enumerateDevices.mockResolvedValue([{ kind: "audioinput", deviceId: "usb-mic", label: "Micrófono USB" }]);

    await renderProbe();
    await waitFor(() => container.querySelector('[data-testid="inputs"]')?.textContent?.includes("usb-mic") || false);
    await act(async () => (container.querySelector('[data-action="select"]') as HTMLButtonElement).click());
    getUserMedia.mockResolvedValue({ getTracks: () => [] });
    await act(async () => (container.querySelector('[data-action="start"]') as HTMLButtonElement).click());

    expect(container.querySelector('[data-testid="selected"]')?.textContent).toBe("usb-mic");
    expect(getUserMedia).toHaveBeenCalledWith(expect.objectContaining({ audio: expect.objectContaining({ deviceId: { exact: "usb-mic" } }) }));
  });

  it("falls back to the system input when a selected microphone is unplugged", async () => {
    enumerateDevices
      .mockResolvedValueOnce([
        { kind: "audioinput", deviceId: "default", label: "MacBook Microphone" },
        { kind: "audioinput", deviceId: "usb-mic", label: "Micrófono USB" },
      ])
      .mockResolvedValueOnce([{ kind: "audioinput", deviceId: "default", label: "MacBook Microphone" }]);

    await renderProbe();
    await waitFor(() => container.querySelector('[data-testid="inputs"]')?.textContent?.includes("usb-mic") || false);
    await act(async () => (container.querySelector('[data-action="select"]') as HTMLButtonElement).click());

    const deviceChange = (navigator.mediaDevices.addEventListener as ReturnType<typeof vi.fn>).mock.calls.find(([event]) => event === "devicechange")?.[1] as () => void;
    await act(async () => deviceChange());
    await waitFor(() => container.querySelector('[data-testid="inputs"]')?.textContent?.includes("MacBook") || false);

    expect(container.querySelector('[data-testid="selected"]')?.textContent).toBe("default");
  });
});

describe("useRecorder screen wake lock", () => {
  async function startRecording() {
    enumerateDevices.mockResolvedValue([]);
    getUserMedia.mockResolvedValue(stream);
    await renderProbe();
    await act(async () => (container.querySelector('[data-action="start"]') as HTMLButtonElement).click());
    await waitFor(() => container.querySelector('[data-testid="recording"]')?.textContent === "true");
  }

  it("requests a screen wake lock while recording when supported", async () => {
    await startRecording();

    expect(wakeLockRequest).toHaveBeenCalledWith("screen");
    expect(container.querySelector('[data-testid="wake-lock-warning"]')?.textContent).toBe("");
  });

  it("continues recording and explains the best-effort limitation when wake lock is unsupported", async () => {
    Object.defineProperty(navigator, "wakeLock", { configurable: true, value: undefined });

    await startRecording();

    expect(container.querySelector('[data-testid="recording"]')?.textContent).toBe("true");
    expect(container.querySelector('[data-testid="wake-lock-warning"]')?.textContent).toContain("no puede mantener la pantalla activa");
  });

  it("continues recording and reports a denied wake lock", async () => {
    wakeLockRequest.mockRejectedValue(new DOMException("denied", "NotAllowedError"));

    await startRecording();
    await waitFor(() => Boolean(container.querySelector('[data-testid="wake-lock-warning"]')?.textContent));

    expect(container.querySelector('[data-testid="recording"]')?.textContent).toBe("true");
    expect(container.querySelector('[data-testid="wake-lock-warning"]')?.textContent).toContain("no se pudo mantener la pantalla activa");
  });

  it("releases the wake lock when recording stops", async () => {
    await startRecording();

    await act(async () => (container.querySelector('[data-action="stop"]') as HTMLButtonElement).click());

    expect(wakeLockRelease).toHaveBeenCalledTimes(1);
  });

  it("reacquires a released wake lock after the recording tab becomes visible", async () => {
    await startRecording();
    wakeLockListeners.get("release")?.();
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });

    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    await waitFor(() => wakeLockRequest.mock.calls.length === 2);

    expect(wakeLockRequest).toHaveBeenNthCalledWith(2, "screen");
  });
});

describe("useRecorder storage and input loss", () => {
  it("reports recording capacity from the browser storage estimate", async () => {
    enumerateDevices.mockResolvedValue([]);
    Object.defineProperty(navigator, "storage", {
      configurable: true,
      value: { estimate: vi.fn().mockResolvedValue({ quota: 1_200_000, usage: 240_000 }) },
    });

    await renderProbe();
    await waitFor(() => container.querySelector('[data-testid="storage"]')?.textContent?.includes("available") || false);

    expect(JSON.parse(container.querySelector('[data-testid="storage"]')?.textContent || "{}")).toEqual({
      status: "available", availableBytes: 960_000, recordingSeconds: 10,
    });
    Object.defineProperty(navigator, "storage", { configurable: true, value: undefined });
  });

  it("stops the recorder and explains recovery when the microphone disconnects", async () => {
    enumerateDevices.mockResolvedValue([]);
    const listeners = new Map<string, () => void>();
    getUserMedia.mockResolvedValue({ getTracks: () => [{ stop: vi.fn(), addEventListener: (event: string, listener: () => void) => listeners.set(event, listener) }] });
    await renderProbe();
    await act(async () => (container.querySelector('[data-action="start"]') as HTMLButtonElement).click());
    await waitFor(() => container.querySelector('[data-testid="recording"]')?.textContent === "true");

    await act(async () => listeners.get("ended")?.());
    await waitFor(() => container.querySelector('[data-testid="recording"]')?.textContent === "false");

    expect(lastRecorder?.state).toBe("inactive");
    expect(container.querySelector('[data-testid="error"]')?.textContent).toContain("El micrófono se desconectó");
  });
});
