import { describe, expect, it } from "vitest";
import { recoveryBytesPerSecond, storageEstimateState } from "./recordingCapacity";

describe("storageEstimateState", () => {
  it("sizes capacity for PCM16 mono recovery blocks", () => {
    expect(recoveryBytesPerSecond(48_000)).toBe(96_000);
    expect(storageEstimateState({ quota: 1_200_000, usage: 240_000 })).toEqual({
      status: "available",
      availableBytes: 960_000,
      recordingSeconds: 10,
    });
  });

  it("never reports negative space", () => {
    expect(storageEstimateState({ quota: 100, usage: 500 })).toEqual({ status: "available", availableBytes: 0, recordingSeconds: 0 });
  });

  it("is unknown when the browser gives no usable estimate", () => {
    expect(storageEstimateState(undefined)).toEqual({ status: "unknown" });
    expect(storageEstimateState({ quota: 10 })).toEqual({ status: "unknown" });
    expect(storageEstimateState({ quota: Number.NaN, usage: 0 })).toEqual({ status: "unknown" });
  });
});
