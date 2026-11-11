import "server-only";

import type { AuditPage, Config, EncounterDetail, EncounterSummary, View } from "./types";

// Where the Python API lives, read at request time so one image works everywhere.
export function apiUrl(): string {
  return (process.env.API_URL ?? "http://localhost:8400").replace(/\/$/, "");
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${apiUrl()}${path}`, { cache: "no-store" });
  if (!response.ok) {
    throw new ApiError(response.status, `${path} returned ${response.status}`);
  }
  return (await response.json()) as T;
}

export const api = {
  config: () => get<Config>("/api/config"),
  encounters: () => get<EncounterSummary[]>("/api/encounters"),
  encounter: (id: string, view: View = "display") =>
    get<EncounterDetail>(`/api/encounters/${encodeURIComponent(id)}?view=${view}`),
  audit: (limit = 200) => get<AuditPage>(`/api/audit?limit=${limit}`),
};
