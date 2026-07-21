/**
 * The shapes the backend returns.
 *
 * Mirrors the envelope every endpoint uses, so there is one parser for success
 * and one for failure — including the failure that matters most here, a source
 * that could not answer.
 */

/** Every backend response, success or failure. */
export type Envelope<T> =
  | { ok: true; data: T; message: string }
  | { ok: false; error: { code: string; detail: string }; message: string };

/**
 * What a section gets after a fetch. `unavailable` is a first-class outcome,
 * not an error to swallow: a metric with no data must say so rather than
 * render a zero the clinic would read as "Sofía no trabajó".
 */
export type Result<T> =
  | { status: "ok"; data: T }
  | { status: "unavailable"; message: string; detail?: string };

/** Which upstream sources answered for a given payload. */
export type Sources = Record<string, string>;

export type Metrics = {
  range: { start: string; end: string; timezone: string };
  total_calls: number;
  appointments_booked: number;
  appointments_label: string;
  success_rate: number | null;
  avg_duration_seconds: number | null;
  avg_duration_sample_size: number;
  avg_duration_is_sample: boolean;
  sources: Sources;
};

export type CallRow = {
  call_id: string;
  started_at: string | null;
  duration_seconds: number | null;
  origin: string;
  call_type: string | null;
  phone: string | null;
  contact_name: string | null;
  contact_id: string | null;
  booked: boolean;
  resumen: string | null;
  nivel_urgencia: string | null;
  interes_score: string | number | null;
  call_status: string | null;
};

export type CallList = {
  calls: CallRow[];
  count: number;
  has_more: boolean;
  pagination_key: string | null;
  range: { start: string; end: string };
  sources: Sources;
};

export type ToolCall = {
  name: string | null;
  arguments: Record<string, unknown>;
  result: unknown;
  succeeded: boolean | null;
};

export type CallDetail = {
  call_id: string;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  origin: string;
  call_type: string | null;
  call_status: string | null;
  disconnection_reason: string | null;
  phone: string | null;
  contact_name: string | null;
  contact_id: string | null;
  booked: boolean;
  transcript: string;
  tool_calls: ToolCall[];
  analysis: {
    resumen: string | null;
    interes_score: string | number | null;
    nivel_urgencia: string | null;
    probabilidad_asistir: string | number | null;
    motivo: string | null;
  };
  recording_url: string | null;
  sources: Sources;
};

export type FunnelStage = {
  key: string;
  label: string;
  stage_id: string;
  count: number;
};

export type Funnel = {
  stages: FunnelStage[];
  total: number;
  unmapped: number;
  pipeline_id: string;
  sources: Sources;
};

export type Temperature = {
  counts: Record<string, number>;
  total: number;
  sources: Sources;
};

export type PromptPayload = {
  editable: string;
  guardrails: string;
  guardrails_present_live: boolean;
  guardrails_match_repo: boolean;
  protection: {
    marker: string;
    section: number;
    title: string;
    guardrails: string;
    available: boolean;
    detail: string | null;
    why: string;
  };
  previous: {
    available: boolean;
    prompt: string | null;
    saved_at: string | null;
    durable: boolean;
  };
};

export type ServiceStatus = {
  services: Array<{ service: string; ok: boolean; detail?: string } & Record<string, unknown>>;
  all_ok: boolean;
  degraded: string[];
};
