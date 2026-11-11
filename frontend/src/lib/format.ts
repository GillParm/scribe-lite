import type { EncounterSource, EncounterStatus, SectionName, Support } from "./types";

export function clock(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "";
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  return `${minutes}:${String(total % 60).padStart(2, "0")}`;
}

export function dateTime(iso: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(new Date(iso));
}

export function usd(value: number | string | null): string {
  if (value == null) return "not priced";
  const amount = typeof value === "string" ? Number(value) : value;
  return amount > 0 && amount < 0.01 ? `$${amount.toFixed(4)}` : `$${amount.toFixed(3)}`;
}

export function plural(count: number, [one, many]: [string, string]): string {
  return `${count} ${count === 1 ? one : many}`;
}

export const SECTION_TITLES: Record<SectionName, string> = {
  subjective: "Subjective",
  objective: "Objective",
  assessment: "Assessment",
  plan: "Plan",
};

export const SOURCE_LABELS: Record<EncounterSource, string> = {
  sample: "Sample",
  upload: "Upload",
  recording: "Recording",
};

export const STATUS_LABELS: Record<EncounterStatus, string> = {
  uploaded: "Queued",
  transcribing: "Transcribing",
  redacting: "Redacting",
  drafting: "Writing note",
  checking: "Checking claims",
  ready: "Ready",
  failed: "Failed",
};

export const SUPPORT_LABELS: Record<Support, string> = {
  supported: "Supported",
  partially_supported: "Partly supported",
  unsupported: "Not supported",
  unchecked: "Not checked",
};

export const ENTITY_LABELS: Record<string, [string, string]> = {
  PERSON: ["name", "names"],
  DATE_OF_BIRTH: ["date of birth", "dates of birth"],
  PHONE_NUMBER: ["phone number", "phone numbers"],
  ADDRESS: ["address", "addresses"],
  EMAIL_ADDRESS: ["email address", "email addresses"],
};

export const PURPOSE_LABELS: Record<string, string> = {
  transcription: "Transcription",
  soap_note: "SOAP note",
  claim_check: "Claim check",
  eval_coverage: "Eval: coverage",
};
