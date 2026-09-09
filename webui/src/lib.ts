export function formatDuration(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const remaining = safe % 60;
  return hours > 0
    ? [hours, minutes, remaining].map((value) => String(value).padStart(2, "0")).join(":")
    : [minutes, remaining].map((value) => String(value).padStart(2, "0")).join(":");
}

export function recordingExtension(mimeType: string): string {
  if (mimeType.includes("mp4")) return "m4a";
  if (mimeType.includes("ogg")) return "ogg";
  return "webm";
}

export function audioWarning(classification: "silent" | "quiet" | "normal"): string | null {
  if (classification === "silent") {
    return "No se detectó audio audible. Revisa el micrófono o graba el audio del sistema y vuelve a intentarlo.";
  }
  if (classification === "quiet") {
    return "El nivel de audio es muy bajo. Acerca el micrófono o confirma que el audio del sistema esté incluido.";
  }
  return null;
}
