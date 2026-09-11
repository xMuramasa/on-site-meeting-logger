/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRecorder } from "./useRecorder";

let container: HTMLDivElement;
let root: Root;
const enumerateDevices = vi.fn();
const getUserMedia = vi.fn();

function Probe() {
  const recorder = useRecorder();
  return <>
    <output data-testid="inputs">{JSON.stringify(recorder.inputs)}</output>
    <output data-testid="selected">{recorder.selectedInputId}</output>
    <output data-testid="labels">{String(recorder.inputLabelsAvailable)}</output>
    <button type="button" data-action="select" onClick={() => recorder.selectInput("usb-mic")}>Seleccionar USB</button>
    <button type="button" data-action="start" onClick={() => void recorder.start()}>Grabar</button>
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
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: {
      enumerateDevices,
      getUserMedia,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    },
  });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
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
