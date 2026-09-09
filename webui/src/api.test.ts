import { afterEach, describe, expect, it, vi } from "vitest";
import { bootstrap, uploadMeeting } from "./api";

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("browser API session recovery", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("refreshes an expired CSRF token once and retries the original upload", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ csrf_token: "before-restart", output_root: "/meetings", accepted_audio: [".m4a"], recording_supported: true, readiness: { ok: true, checks: [] } }))
      .mockResolvedValueOnce(response({ detail: "invalid CSRF token" }, 403))
      .mockResolvedValueOnce(response({ csrf_token: "after-restart", output_root: "/meetings", accepted_audio: [".m4a"], recording_supported: true, readiness: { ok: true, checks: [] } }))
      .mockResolvedValueOnce(response({ date: "2026-09-03", status: "accepted" }, 202));
    vi.stubGlobal("fetch", fetchMock);

    await bootstrap();
    await uploadMeeting("2026-09-03", new File(["audio"], "meeting.m4a", { type: "audio/mp4" }));

    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get("X-CSRF-Token")).toBe("before-restart");
    expect(new Headers(fetchMock.mock.calls[3][1]?.headers).get("X-CSRF-Token")).toBe("after-restart");
    expect(fetchMock.mock.calls[1][1]?.body).toBe(fetchMock.mock.calls[3][1]?.body);
  });

  it("does not retry a mutation when the server rejected it for another reason", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ csrf_token: "token", output_root: "/meetings", accepted_audio: [".m4a"], recording_supported: true, readiness: { ok: true, checks: [] } }))
      .mockResolvedValueOnce(response({ detail: "pipeline is not ready" }, 503));
    vi.stubGlobal("fetch", fetchMock);

    await bootstrap();
    await expect(uploadMeeting("2026-09-03", new File(["audio"], "meeting.m4a", { type: "audio/mp4" }))).rejects.toThrow("pipeline is not ready");

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
