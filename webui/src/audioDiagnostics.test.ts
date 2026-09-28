import { describe, expect, it } from "vitest";
import { LiveAudioDiagnostics } from "./audioDiagnostics";

describe("LiveAudioDiagnostics", () => {
  it("keeps normal synthetic audio free of warnings", () => {
    const diagnostics = new LiveAudioDiagnostics();

    expect([0, 500, 1_000, 5_000].map((at) => diagnostics.observe(0.35, at))).toEqual([null, null, null, null]);
  });

  it("emits one silence warning after five seconds of quiet input", () => {
    const diagnostics = new LiveAudioDiagnostics();

    expect(diagnostics.observe(0.005, 0)).toBeNull();
    expect(diagnostics.observe(0.005, 4_999)).toBeNull();
    expect(diagnostics.observe(0.005, 5_000)).toBe("silence");
    expect(diagnostics.observe(0.005, 8_000)).toBeNull();
  });

  it("emits one clipping warning after a short sustained overload", () => {
    const diagnostics = new LiveAudioDiagnostics();

    expect(diagnostics.observe(0.99, 0)).toBeNull();
    expect(diagnostics.observe(0.99, 249)).toBeNull();
    expect(diagnostics.observe(0.99, 250)).toBe("clipping");
    expect(diagnostics.observe(0.99, 500)).toBeNull();
  });

  it("clears an active warning on recovery and can warn again after a new condition", () => {
    const diagnostics = new LiveAudioDiagnostics();

    diagnostics.observe(0, 0);
    expect(diagnostics.observe(0, 5_000)).toBe("silence");
    expect(diagnostics.observe(0.35, 5_100)).toBe("normal");
    expect(diagnostics.observe(0.99, 5_200)).toBeNull();
    expect(diagnostics.observe(0.99, 5_450)).toBe("clipping");
  });

  it("replaces a silence warning with clipping rather than repeating silence", () => {
    const diagnostics = new LiveAudioDiagnostics();

    diagnostics.observe(0, 0);
    expect(diagnostics.observe(0, 5_000)).toBe("silence");
    expect(diagnostics.observe(0.99, 5_100)).toBeNull();
    expect(diagnostics.observe(0.99, 5_350)).toBe("clipping");
    expect(diagnostics.observe(0.99, 5_600)).toBeNull();
  });
});
