export type LiveAudioCondition = "silence" | "clipping" | "normal";

const SILENCE_LEVEL = 0.015;
const SILENCE_DURATION_MS = 5_000;
const CLIPPING_LEVEL = 0.98;
const CLIPPING_DURATION_MS = 250;

export function liveAudioWarning(condition: Exclude<LiveAudioCondition, "normal">): string {
  if (condition === "silence") {
    return "No se detecta audio desde hace varios segundos. Revisa que el micrófono correcto esté activo y que no esté silenciado.";
  }
  return "El audio está saturando. Baja la ganancia o aléjate del micrófono para evitar distorsión.";
}

export class LiveAudioDiagnostics {
  private active: LiveAudioCondition = "normal";
  private silenceSince: number | null = null;
  private clippingSince: number | null = null;

  observe(level: number, at: number): LiveAudioCondition | null {
    const silent = level <= SILENCE_LEVEL;
    const clipping = level >= CLIPPING_LEVEL;

    const silenceSince = silent ? (this.silenceSince ?? at) : null;
    const clippingSince = clipping ? (this.clippingSince ?? at) : null;
    this.silenceSince = silenceSince;
    this.clippingSince = clippingSince;

    if (silent && silenceSince !== null && at - silenceSince >= SILENCE_DURATION_MS) return this.transition("silence");
    if (clipping && clippingSince !== null && at - clippingSince >= CLIPPING_DURATION_MS) return this.transition("clipping");
    if (!silent && !clipping) return this.transition("normal");
    return null;
  }

  private transition(next: LiveAudioCondition): LiveAudioCondition | null {
    if (next === this.active) return null;
    this.active = next;
    return next;
  }
}
