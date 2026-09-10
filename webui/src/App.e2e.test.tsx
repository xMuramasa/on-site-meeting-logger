/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Bootstrap, MeetingDetail, MeetingSummary, Review } from "./api";

const mocks = vi.hoisted(() => ({
  bootstrap: vi.fn(),
  meetings: vi.fn(),
  meeting: vi.fn(),
  uploadMeeting: vi.fn(),
  saveReview: vi.fn(),
  finalize: vi.fn(),
  cancel: vi.fn(),
  restart: vi.fn(),
  useRecorder: vi.fn(),
}));

vi.mock("./api", () => mocks);
vi.mock("./useRecorder", () => ({ useRecorder: mocks.useRecorder }));

import App from "./App";

const bootstrap: Bootstrap = {
  csrf_token: "csrf",
  output_root: "/tmp/meetings",
  accepted_audio: ["webm"],
  recording_supported: true,
  readiness: { ok: true, checks: [] },
};

const review: Review = {
  participants: [{ name: "Ana", email: "ana@example.com", attended: null }],
  proper_nouns: {},
  owners: { "A-1": "unresolved" },
  relative_date_actions: [{
    action_id: "A-2",
    action_text: "Enviar informe",
    due_expression: "mañana",
    resolved_date: null,
  }],
  quality_warnings: ["Verifica la fecha"],
  approve_for_final_render: false,
};

const summary = (overrides: Partial<MeetingSummary> = {}): MeetingSummary => ({
  date: "2026-09-03",
  source: "meeting.webm",
  stages: { inspect: "complete", transcribe: "complete", validate: "pending" },
  job: null,
  ...overrides,
});

const detail = (overrides: Partial<MeetingDetail> = {}): MeetingDetail => ({
  date: "2026-09-03",
  job: null,
  audio_analysis: null,
  review,
  artifacts: [],
  ...overrides,
});

let container: HTMLDivElement;
let root: Root;

async function renderApp() {
  await act(async () => {
    root.render(<App />);
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function click(element: Element) {
  await act(async () => {
    (element as HTMLButtonElement).click();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function setInput(element: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string) {
  const prototype = element instanceof HTMLSelectElement ? HTMLSelectElement.prototype :
    element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  setter?.call(element, value);
  await act(async () => {
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

async function waitFor(check: () => boolean) {
  for (let attempt = 0; attempt < 10; attempt += 1) {
    if (check()) return;
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  }
  throw new Error("Condition was not met");
}

function button(label: string) {
  const found = [...container.querySelectorAll("button")].find((item) => item.textContent?.includes(label));
  if (!found) throw new Error(`Button not found: ${label}`);
  return found;
}

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:preview") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  vi.clearAllMocks();
  mocks.bootstrap.mockResolvedValue(bootstrap);
  mocks.meetings.mockResolvedValue([]);
  mocks.meeting.mockResolvedValue(detail());
  mocks.uploadMeeting.mockResolvedValue(undefined);
  mocks.saveReview.mockResolvedValue(undefined);
  mocks.finalize.mockResolvedValue(undefined);
  mocks.cancel.mockResolvedValue(undefined);
  mocks.restart.mockResolvedValue(undefined);
  mocks.useRecorder.mockReturnValue({
    recording: false,
    elapsed: 0,
    level: 0,
    file: null,
    error: "",
    start: vi.fn(),
    stop: vi.fn(),
    discard: vi.fn(),
  });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("Meeting Studio browser workflows", () => {
  it("uploads selected audio and begins a draft", async () => {
    await renderApp();
    const audio = new File(["audio"], "meeting.webm", { type: "audio/webm" });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    Object.defineProperty(input, "files", { value: [audio] });
    await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));

    await click(button("Crear borrador de acta"));

    expect(mocks.uploadMeeting).toHaveBeenCalledWith(expect.any(String), audio, undefined);
    expect(container.textContent).toContain("Procesamiento iniciado");
  });

  it("renders durable job progress and lets the user cancel processing", async () => {
    mocks.meetings.mockResolvedValue([summary({ job: { status: "running", stage: "transcribe" } })]);
    mocks.meeting.mockResolvedValue(detail({ job: { status: "running", stage: "transcribe" }, review: null }));
    await renderApp();

    await click(button("3 sept"));
    await click(button("Cancelar"));

    expect(container.textContent).toContain("Audio inspeccionado");
    expect(container.textContent).toContain("Transcripción");
    expect(mocks.cancel).toHaveBeenCalledWith("2026-09-03");
  });

  it("edits evidence review fields and saves the reviewed evidence", async () => {
    mocks.meetings.mockResolvedValue([summary()]);
    await renderApp();
    await click(button("3 sept"));

    const attendance = container.querySelector('select[aria-label="Asistencia de Ana"]') as HTMLSelectElement;
    await setInput(attendance, "yes");
    const due = container.querySelector('input[aria-label="Fecha resuelta para A-2"]') as HTMLInputElement;
    await setInput(due, "2026-09-04");
    await click(button("Guardar"));

    expect(mocks.saveReview).toHaveBeenCalledWith("2026-09-03", expect.objectContaining({
      participants: [expect.objectContaining({ attended: true })],
      relative_date_actions: [expect.objectContaining({ resolved_date: "2026-09-04" })],
    }));
  });

  it("shows microphone access errors in the capture form", async () => {
    mocks.useRecorder.mockReturnValue({
      recording: false, elapsed: 0, level: 0, file: null,
      error: "Permission denied", start: vi.fn(), stop: vi.fn(), discard: vi.fn(),
    });
    await renderApp();

    expect(container.textContent).toContain("Permission denied");
  });

  it("finalizes an approved review and allows re-finalization after an edit", async () => {
    mocks.meetings.mockResolvedValue([summary()]);
    mocks.meeting.mockResolvedValue(detail({ review: { ...review, approve_for_final_render: true } }));
    await renderApp();
    await click(button("3 sept"));

    await click(button("Finalizar acta"));
    await waitFor(() => !button("Finalizar acta").hasAttribute("disabled"));
    const corrections = container.querySelector('textarea[aria-label="Correcciones de nombres propios"]') as HTMLTextAreaElement;
    await setInput(corrections, "Obvio → Obvio Health");
    await click(button("Finalizar acta"));

    expect(mocks.saveReview).toHaveBeenCalledTimes(2);
    expect(mocks.finalize).toHaveBeenCalledTimes(2);
    expect(mocks.finalize).toHaveBeenLastCalledWith("2026-09-03");
  });

  it("reports duplicate upload requests without hiding the server reason", async () => {
    mocks.uploadMeeting.mockRejectedValue(new Error("A meeting already exists for this date"));
    await renderApp();
    const audio = new File(["audio"], "meeting.webm", { type: "audio/webm" });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    Object.defineProperty(input, "files", { value: [audio] });
    await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));

    await click(button("Crear borrador de acta"));

    expect(container.textContent).toContain("A meeting already exists for this date");
  });

  it("offers restart recovery for failed durable jobs", async () => {
    mocks.meetings.mockResolvedValue([summary({ job: { status: "failed", stage: "transcribe", error_code: "PROCESSING_FAILED", retryable: true } })]);
    mocks.meeting.mockResolvedValue(detail({ job: { status: "failed", stage: "transcribe", error_code: "PROCESSING_FAILED", retryable: true }, review: null }));
    await renderApp();

    await click(button("3 sept"));
    await click(button("Reanudar"));

    expect(container.textContent).toContain("PROCESSING_FAILED");
    expect(mocks.restart).toHaveBeenCalledWith("2026-09-03");
  });

  it("exposes final document downloads with meeting-scoped links", async () => {
    mocks.meetings.mockResolvedValue([summary({ stages: { validate: "complete" } })]);
    mocks.meeting.mockResolvedValue(detail({ artifacts: [
      { name: "acta-final.pdf", role: "minutes", format: "PDF", final: true },
      { name: "acta-final.html", role: "minutes", format: "HTML", final: true },
      { name: "review.yaml", role: "supporting", format: "YAML", final: false },
    ] }));
    await renderApp();
    await click(button("3 sept"));

    const links = [...container.querySelectorAll(".output-grid a")];
    expect(links).toHaveLength(3);
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/api/meetings/2026-09-03/files/acta-final.pdf",
      "/api/meetings/2026-09-03/files/acta-final.html",
      "/api/meetings/2026-09-03/files/review.yaml",
    ]);
  });
});
