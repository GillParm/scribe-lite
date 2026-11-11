// Mirrors the API's response models (backend/src/scribe/api/schemas.py).

export type EncounterStatus =
  "uploaded" | "transcribing" | "redacting" | "drafting" | "checking" | "ready" | "failed";
export type EncounterSource = "upload" | "recording" | "sample";
export type Speaker = "doctor" | "patient";
export type SectionName = "subjective" | "objective" | "assessment" | "plan";
export type Support = "supported" | "partially_supported" | "unsupported" | "unchecked";
export type View = "display" | "redacted";

export interface Config {
  notice: string;
  transcriber_mode: string;
  gemini_configured: boolean;
  anthropic_configured: boolean;
  gemini_model: string;
  soap_model: string;
  check_model: string;
  retention_days: number;
  max_upload_mb: number;
}

export interface EncounterSummary {
  id: string;
  title: string;
  source: EncounterSource;
  sample_slug: string | null;
  status: EncounterStatus;
  error: string | null;
  created_at: string;
  duration_seconds: number | null;
  audio_available: boolean;
  audio_deleted_at: string | null;
  statement_count: number;
  flagged_count: number;
}

export interface RedactionSpan {
  start: number;
  end: number;
  placeholder: string;
  entity_type: string;
}

export interface Segment {
  id: string;
  speaker: Speaker;
  start: number;
  end: number;
  text: string;
  redactions: RedactionSpan[];
}

export interface Statement {
  id: string;
  text: string;
  source_segment_ids: string[];
  citations_repaired: boolean;
  support: Support | null;
  support_reason: string | null;
}

export interface NoteSection {
  name: SectionName;
  statements: Statement[];
}

export interface RejectedStatement {
  section: SectionName;
  text: string;
  cited_ids: string[];
  reason: string;
}

export interface PiiItem {
  placeholder: string;
  entity_type: string;
  occurrences: number;
  value: string | null;
}

export interface ModelCall {
  id: number;
  created_at: string;
  provider: string;
  model: string;
  purpose: string;
  encounter_id: string | null;
  prompt_sha256: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: string | null;
  latency_ms: number;
  status: string;
  error: string | null;
  origin: "live" | "sample_import";
}

export interface EncounterDetail extends EncounterSummary {
  view: View;
  transcriber: string | null;
  note_model: string | null;
  check_model: string | null;
  audio_mime: string | null;
  segments: Segment[];
  sections: NoteSection[];
  rejected_statements: RejectedStatement[];
  pii: PiiItem[];
  check_summary: Record<Support, number>;
  model_calls: ModelCall[];
}

export interface AuditPage {
  totals: { calls: number; input_tokens: number; output_tokens: number; cost_usd: number };
  total_items: number;
  items: ModelCall[];
}

export const IN_PROGRESS: EncounterStatus[] = [
  "uploaded",
  "transcribing",
  "redacting",
  "drafting",
  "checking",
];
