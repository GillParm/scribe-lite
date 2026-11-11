import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { NewEncounter } from "@/components/new-encounter";
import { StatusText } from "@/components/status";
import { clock, dateTime, SOURCE_LABELS } from "@/lib/format";
import { api } from "@/lib/server-api";

export default function Home() {
  return (
    <main className="mx-auto max-w-[1400px] px-6 py-8">
      <h1 className="max-w-3xl font-serif text-3xl leading-tight font-semibold">
        A recorded visit becomes a SOAP note, and every sentence shows where in the conversation it
        came from.
      </h1>
      <p className="mt-3 max-w-3xl text-ink-soft">
        Open a sample to see the note beside its transcript. Select a sentence to mark its source
        and jump the audio there. Sentences the transcript does not back up are flagged.
      </p>
      <Suspense fallback={<p className="mt-10 text-ink-soft">Loading encounters…</p>}>
        <EncountersAndForm />
      </Suspense>
    </main>
  );
}

async function EncountersAndForm() {
  await connection();
  let data;
  try {
    data = await Promise.all([api.encounters(), api.config()]);
  } catch {
    return (
      <p className="mt-10 border-l-4 border-unsupported bg-paper px-4 py-3">
        The API is not reachable. Start it with <code>make api</code> (port 8400) or set{" "}
        <code>API_URL</code>.
      </p>
    );
  }
  const [encounters, config] = data;
  return (
    <div className="mt-8 grid grid-cols-[minmax(0,1fr)] gap-8 lg:grid-cols-[minmax(0,1fr)_360px]">
      <section aria-labelledby="encounters">
        <h2 id="encounters" className="sr-only">
          Encounters
        </h2>
        {encounters.length === 0 ? (
          <p className="border border-rule bg-paper p-6">
            No encounters yet. Record or upload a conversation, or run{" "}
            <code>scribe samples seed</code> to load the three samples.
          </p>
        ) : (
          <div className="overflow-x-auto border border-rule bg-paper">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-rule text-ink-soft">
                <tr>
                  <th className="px-4 py-3 font-semibold">Encounter</th>
                  <th className="px-4 py-3 font-semibold">Source</th>
                  <th className="px-4 py-3 text-right font-semibold">Length</th>
                  <th className="px-4 py-3 font-semibold">Status</th>
                  <th className="px-4 py-3 font-semibold">Note</th>
                  <th className="px-4 py-3 font-semibold">Added (UTC)</th>
                </tr>
              </thead>
              <tbody>
                {encounters.map((e) => (
                  <tr key={e.id} className="border-b border-rule-soft last:border-0">
                    <td className="px-4 py-3">
                      <Link
                        href={`/encounters/${e.id}`}
                        className="font-semibold underline decoration-rule underline-offset-4 hover:decoration-ink"
                      >
                        {e.title}
                      </Link>
                    </td>
                    <td className="px-4 py-3">{SOURCE_LABELS[e.source]}</td>
                    <td className="px-4 py-3 text-right">{clock(e.duration_seconds)}</td>
                    <td className="px-4 py-3">
                      <StatusText status={e.status} />
                    </td>
                    <td className="px-4 py-3">
                      {e.statement_count > 0 && (
                        <>
                          {e.statement_count} statements
                          {e.flagged_count > 0 && (
                            <span className="text-partial">, {e.flagged_count} flagged</span>
                          )}
                        </>
                      )}
                      {!e.audio_available && (
                        <span className="block text-xs text-ink-soft">Audio deleted</span>
                      )}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-ink-soft">
                      {dateTime(e.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-3 text-sm text-ink-soft">
          Audio is deleted {config.retention_days} days after upload. Transcripts and notes stay.
        </p>
      </section>
      <NewEncounter
        geminiConfigured={config.gemini_configured}
        anthropicConfigured={config.anthropic_configured}
        maxUploadMb={config.max_upload_mb}
      />
    </div>
  );
}
