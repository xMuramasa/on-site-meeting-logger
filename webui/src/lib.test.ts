import { describe, expect, it } from "vitest";
import { formatDuration, recordingExtension } from "./lib";

describe("recording helpers", () => {
  it("formats long recordings without wrapping minutes", () => {
    expect(formatDuration(65)).toBe("01:05");
    expect(formatDuration(3661)).toBe("01:01:01");
  });

  it("maps browser recording MIME types to accepted extensions", () => {
    expect(recordingExtension("audio/mp4")).toBe("m4a");
    expect(recordingExtension("audio/webm;codecs=opus")).toBe("webm");
  });
});
