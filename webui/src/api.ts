export type ReviewParticipant = { name: string; email: string | null; attended: boolean | null };
export type RelativeDateReview = {
  action_id: string;
  action_text: string;
  due_expression: string;
  resolved_date: string | null;
};
export type Review = {
  participants: ReviewParticipant[];
  proper_nouns: Record<string, string>;
  owners: Record<string, string>;
  relative_date_actions: RelativeDateReview[];
  quality_warnings: string[];
  approve_for_final_render: boolean;
};
export type FailureCode = "NO_SPEECH" | "MODEL_UNAVAILABLE" | "AUDIO_DECODE_FAILED" | "STORAGE_UNAVAILABLE" | "JOB_INTERRUPTED" | "PROCESSING_FAILED";
export type Job = {
  status: "running" | "complete" | "failed" | "cancelled";
  stage: string;
  error_code?: FailureCode;
  retryable?: boolean;
};
export type MeetingSummary = {
  date: string;
  source: string;
  stages: Record<string, string>;
  job: Job | null;
};
export type MeetingDetail = { date: string; job: Job | null; review: Review | null; files: string[] };
export type Bootstrap = { csrf_token: string; output_root: string; accepted_audio: string[]; recording_supported: boolean };

let csrfToken = "";

async function parse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function bootstrap(): Promise<Bootstrap> {
  const data = await parse<Bootstrap>(await fetch("/api/bootstrap"));
  csrfToken = data.csrf_token;
  return data;
}

export async function meetings(): Promise<MeetingSummary[]> {
  return (await parse<{ meetings: MeetingSummary[] }>(await fetch("/api/meetings"))).meetings;
}

export async function meeting(date: string): Promise<MeetingDetail> {
  return parse(await fetch(`/api/meetings/${date}`));
}

export async function uploadMeeting(date: string, audio: File, previous?: File): Promise<void> {
  const body = new FormData();
  body.append("meeting_date", date);
  body.append("audio", audio);
  if (previous) body.append("previous_acta", previous);
  await parse(await fetch("/api/meetings", {
    method: "POST",
    headers: { "X-CSRF-Token": csrfToken },
    body,
  }));
}

export async function saveReview(date: string, review: Review): Promise<void> {
  await parse(await fetch(`/api/meetings/${date}/review`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
    body: JSON.stringify(review),
  }));
}

export async function finalize(date: string): Promise<void> {
  await parse(await fetch(`/api/meetings/${date}/finalize`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrfToken },
  }));
}

export async function cancel(date: string): Promise<void> {
  await parse(await fetch(`/api/meetings/${date}/cancel`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrfToken },
  }));
}

export async function restart(date: string): Promise<void> {
  await parse(await fetch(`/api/meetings/${date}/restart`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrfToken },
  }));
}
