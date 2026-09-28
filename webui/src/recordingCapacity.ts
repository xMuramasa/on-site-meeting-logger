/** Browser storage estimate for the PCM16 mono recovery blocks written by recovery.worklet.js. */

export const DEFAULT_RECOVERY_SAMPLE_RATE = 48_000;
const BYTES_PER_SAMPLE = 2;

export type StorageEstimateState =
  | { status: "available"; availableBytes: number; recordingSeconds: number }
  | { status: "unknown" };

export function recoveryBytesPerSecond(sampleRate = DEFAULT_RECOVERY_SAMPLE_RATE) {
  return sampleRate * BYTES_PER_SAMPLE;
}

export function storageEstimateState(
  estimate?: { quota?: number; usage?: number },
  sampleRate = DEFAULT_RECOVERY_SAMPLE_RATE,
): StorageEstimateState {
  const quota = estimate?.quota;
  const usage = estimate?.usage;
  if (typeof quota !== "number" || typeof usage !== "number" || !Number.isFinite(quota) || !Number.isFinite(usage)) {
    return { status: "unknown" };
  }
  const availableBytes = Math.max(0, quota - usage);
  return { status: "available", availableBytes, recordingSeconds: Math.floor(availableBytes / recoveryBytesPerSecond(sampleRate)) };
}
