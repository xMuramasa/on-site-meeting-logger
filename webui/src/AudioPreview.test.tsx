// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { AudioPreview } from "./AudioPreview";

it("previews local audio, replaces its URL, and releases URLs on unmount", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const create = vi.fn().mockReturnValueOnce("blob:first").mockReturnValueOnce("blob:second");
  const revoke = vi.fn();
  vi.stubGlobal("URL", { createObjectURL: create, revokeObjectURL: revoke });
  const container = document.createElement("div");
  const root = createRoot(container);
  try {
    const first = new File(["audio"], "upload.wav", { type: "audio/wav" });
    await act(async () => root.render(<AudioPreview file={first} />));
    expect(container.querySelector("audio")?.getAttribute("src")).toBe("blob:first");
    expect(container.querySelector("audio")?.controls).toBe(true);
    expect(container.querySelector("audio")?.autoplay).toBe(false);
    expect(container.textContent).toContain("upload.wav");
    const second = new File(["recording"], "recording.webm");
    await act(async () => root.render(<AudioPreview file={second} />));
    expect(revoke).toHaveBeenCalledWith("blob:first");
    expect(container.querySelector("audio")?.getAttribute("src")).toBe("blob:second");
    await act(async () => root.unmount());
    expect(revoke).toHaveBeenCalledWith("blob:second");
  } finally {
    vi.unstubAllGlobals();
  }
});
