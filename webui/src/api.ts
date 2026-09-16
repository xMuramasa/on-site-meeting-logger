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
  // "blocked" means another meeting holds the local models — a wait, not a failure.
  status: "running" | "complete" | "failed" | "cancelled" | "blocked";
  stage: string;
  error_code?: FailureCode;
  retryable?: boolean;
};
export type AudioAnalysis = {
  scanned_seconds: number;
  mean_db: number;
  max_db: number;
  classification: "silent" | "quiet" | "normal";

};
export type MeetingSummary = {
  date: string;
  source: string;
  stages: Record<string, string>;
  job: Job | null;
};
export type MeetingDetail = {
  date: string;
  job: Job | null;
  audio_analysis: AudioAnalysis | null;
  review: Review | null;
  artifacts: Artifact[];
};
export type ReadinessCheck = { name: string; ok: boolean; detail: string };
export type Readiness = { ok: boolean; checks: ReadinessCheck[] };
export type Bootstrap = {
  csrf_token: string;
  output_root: string;
  accepted_audio: string[];
  recording_supported: boolean;
  readiness: Readiness;
};

export type Artifact = {
  name: string;
  role: "minutes" | "transcript" | "digest" | "supporting";
  format: string;
  final: boolean;
};


let csrfToken = "";

async function parse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

async function request<T>(input: RequestInfo | URL, init?: RequestInit, retryCsrf = true): Promise<T> {
  const response = await fetch(input, init);
  const detail = await response.clone().json().catch(() => ({} as { detail?: string }));
  if (retryCsrf && response.status === 403 && detail.detail === "invalid CSRF token") {
    await bootstrap();
    const headers = new Headers(init?.headers);
    if (headers.has("X-CSRF-Token")) headers.set("X-CSRF-Token", csrfToken);
    return request(input, { ...init, headers }, false);
  }
  return parse<T>(response);
}

export async function bootstrap(): Promise<Bootstrap> {
  const data = await parse<Bootstrap>(await fetch("/api/bootstrap"));
  csrfToken = data.csrf_token;
  return data;
}

export async function meetings(): Promise<MeetingSummary[]> {
  return (await request<{ meetings: MeetingSummary[] }>("/api/meetings")).meetings;
}

export async function meeting(date: string): Promise<MeetingDetail> {
  return request(`/api/meetings/${date}`);
}

export async function uploadMeeting(date: string, audio: File, previous?: File): Promise<void> {
  const body = new FormData();
  body.append("meeting_date", date);
  body.append("audio", audio);
  if (previous) body.append("previous_acta", previous);
  await request("/api/meetings", {
    method: "POST",
    headers: { "X-CSRF-Token": csrfToken },
    body,
  });
}

export async function saveReview(date: string, review: Review): Promise<void> {
  await request(`/api/meetings/${date}/review`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
    body: JSON.stringify(review),
  });
}

export async function finalize(date: string): Promise<void> {
  await request(`/api/meetings/${date}/finalize`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrfToken },
  });
}

export async function cancel(date: string): Promise<void> {
  await request(`/api/meetings/${date}/cancel`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrfToken },
  });
}

export async function restart(date: string): Promise<void> {
  await request(`/api/meetings/${date}/restart`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrfToken },
  });
}
