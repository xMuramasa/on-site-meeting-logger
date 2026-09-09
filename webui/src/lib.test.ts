import { describe, expect, it } from "vitest";
import { audioWarning, formatDuration, recordingExtension } from "./lib";

describe("recording helpers", () => {
  it("formats long recordings without wrapping minutes", () => {
    expect(formatDuration(65)).toBe("01:05");
    expect(formatDuration(3661)).toBe("01:01:01");
  });

  it("maps browser recording MIME types to accepted extensions", () => {
    expect(recordingExtension("audio/mp4")).toBe("m4a");
    expect(recordingExtension("audio/webm;codecs=opus")).toBe("webm");
  });

  it("gives actionable Spanish guidance for silent and quiet recordings", () => {
    expect(audioWarning("silent")).toContain("No se detectó audio audible");
    expect(audioWarning("quiet")).toContain("nivel de audio es muy bajo");
    expect(audioWarning("normal")).toBeNull();
  });
});
