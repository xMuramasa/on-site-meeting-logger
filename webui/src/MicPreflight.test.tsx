/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MicPreflight } from "./MicPreflight";
import { micTestFailure } from "./useMicPreflight";

let container: HTMLDivElement;
let root: Root;
const getUserMedia = vi.fn();
const trackStop = vi.fn();
let recorders: TestMediaRecorder[] = [];
let waveform = 200;

class TestMediaRecorder {
  static isTypeSupported(type: string) { return type === "audio/webm"; }
  state: "inactive" | "recording" = "inactive";
  mimeType = "audio/webm";
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onerror: (() => void) | null = null;
  onstop: (() => void) | null = null;
  constructor() { recorders.push(this); }
  start() { this.state = "recording"; }
  stop() {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["sample"], { type: "audio/webm" }) });
    this.onstop?.();
  }
}

function stream() {
  return { getTracks: () => [{ stop: trackStop, addEventListener: vi.fn() }] };
}

async function render(props: { selectedInputId?: string; disabled?: boolean } = {}) {
  await act(async () => root.render(<MicPreflight selectedInputId={props.selectedInputId ?? "default"} disabled={props.disabled ?? false} />));
}

function button(label: string) {
  const match = [...container.querySelectorAll("button")].find((item) => item.textContent?.includes(label));
  if (!match) throw new Error(`Missing button ${label}; text: ${container.textContent}`);
  return match as HTMLButtonElement;
}

async function click(label: string) {
  await act(async () => { button(label).click(); await Promise.resolve(); });
}

async function finishCapture() {
  await act(async () => { vi.advanceTimersByTime(4000); await Promise.resolve(); });
}

beforeEach(() => {
  vi.useFakeTimers();
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  getUserMedia.mockReset();
  trackStop.mockReset();
  recorders = [];
  waveform = 200;
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } });
  vi.stubGlobal("MediaRecorder", TestMediaRecorder);
  vi.stubGlobal("AudioContext", class {
    close = vi.fn(async () => undefined);
    resume = vi.fn(async () => undefined);
    createAnalyser = () => ({ fftSize: 0, getByteTimeDomainData: (values: Uint8Array) => values.fill(waveform) });
    createMediaStreamSource = () => ({ connect: vi.fn() });
  });
  vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:mic-test"), revokeObjectURL: vi.fn() });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("MicPreflight", () => {
  it("captures a short sample from the selected input and offers playback", async () => {
    getUserMedia.mockResolvedValue(stream());
    await render({ selectedInputId: "usb-mic" });

    await click("Probar micrófono");
    expect(getUserMedia).toHaveBeenCalledWith({ audio: expect.objectContaining({ deviceId: { exact: "usb-mic" } }) });
    expect(container.textContent).toContain("Habla ahora");

    await finishCapture();

    const audio = container.querySelector('audio[aria-label="Reproducir prueba de micrófono"]') as HTMLAudioElement;
    expect(audio?.getAttribute("src")).toBe("blob:mic-test");
    expect(audio.controls).toBe(true);
    expect(trackStop).toHaveBeenCalled();
    expect(container.textContent).toContain("Escucha la muestra");
    expect(container.textContent).not.toContain("No se detectó audio audible");
  });

  it("warns when the captured sample is silent", async () => {
    waveform = 128;
    getUserMedia.mockResolvedValue(stream());
    await render();
    await click("Probar micrófono");
    await finishCapture();

    expect(container.textContent).toContain("No se detectó audio audible");
  });

  it("retries with a fresh capture and releases the previous sample URL", async () => {
    getUserMedia.mockResolvedValue(stream());
    await render();
    await click("Probar micrófono");
    await finishCapture();
    await click("Repetir prueba");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mic-test");
    await finishCapture();

    expect(getUserMedia).toHaveBeenCalledTimes(2);
    expect(recorders).toHaveLength(2);
    expect(container.querySelector("audio")).not.toBeNull();
  });

  it("lets the user skip the test without requesting the microphone", async () => {
    await render();
    await click("Omitir prueba");

    expect(getUserMedia).not.toHaveBeenCalled();
    expect(container.textContent).toContain("Puedes grabar la reunión igualmente");
    await click("Probar micrófono");
    expect(container.textContent).toContain("Omitir prueba");
  });

  it("explains a denied permission with remediation and allows retry", async () => {
    getUserMedia.mockRejectedValueOnce(new DOMException("denied", "NotAllowedError")).mockResolvedValueOnce(stream());
    await render();
    await click("Probar micrófono");

    const alert = container.querySelector('[role="alert"]');
    expect(alert?.getAttribute("data-kind")).toBe("permission");
    expect(alert?.textContent).toContain("Privacidad y seguridad");

    await click("Repetir prueba");
    await finishCapture();
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(container.querySelector("audio")).not.toBeNull();
  });

  it("reports an unavailable device", async () => {
    getUserMedia.mockRejectedValue(new DOMException("gone", "NotFoundError"));
    await render({ selectedInputId: "usb-mic" });
    await click("Probar micrófono");

    expect(container.querySelector('[role="alert"]')?.getAttribute("data-kind")).toBe("unavailable");
  });

  it("reports a capture error when the recorder fails mid-test", async () => {
    getUserMedia.mockResolvedValue(stream());
    await render();
    await click("Probar micrófono");
    await act(async () => recorders[0]?.onerror?.());

    expect(container.querySelector('[role="alert"]')?.getAttribute("data-kind")).toBe("capture");
    expect(trackStop).toHaveBeenCalled();
  });

  it("releases the microphone when the meeting recording starts", async () => {
    getUserMedia.mockResolvedValue(stream());
    await render();
    await click("Probar micrófono");
    await render({ disabled: true });

    expect(trackStop).toHaveBeenCalled();
    expect(container.querySelector("audio")).toBeNull();
    expect(button("Probar micrófono").disabled).toBe(true);
  });

  it("discards a sample when a different input is selected", async () => {
    getUserMedia.mockResolvedValue(stream());
    await render();
    await click("Probar micrófono");
    await finishCapture();
    await render({ selectedInputId: "usb-mic" });

    expect(container.querySelector("audio")).toBeNull();
    expect(button("Probar micrófono")).toBeTruthy();
  });
});

describe("micTestFailure", () => {
  it("classifies browser media errors", () => {
    expect(micTestFailure(new DOMException("", "SecurityError")).kind).toBe("permission");
    expect(micTestFailure(new DOMException("", "OverconstrainedError")).kind).toBe("unavailable");
    expect(micTestFailure(new DOMException("", "NotReadableError")).kind).toBe("busy");
    expect(micTestFailure(new Error("boom")).kind).toBe("capture");
  });
});
