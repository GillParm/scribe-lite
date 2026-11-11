import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { dateTime, PURPOSE_LABELS, usd } from "@/lib/format";
import { api } from "@/lib/server-api";

export const metadata: Metadata = { title: "Audit log" };

export default function AuditPage() {
  return (
    <main className="mx-auto max-w-[1400px] px-6 py-8">
      <h1 className="font-serif text-3xl font-semibold">Audit log</h1>
      <p className="mt-2 max-w-3xl text-ink-soft">
        One row for every call to a hosted model, including failed and refused calls. The prompt
        hash is a SHA-256 of the model, settings, system prompt, redacted messages and output
        schema, so a prompt can be matched later without storing its text here.
      </p>
      <Suspense fallback={<p className="mt-8 text-ink-soft">Loading the audit log…</p>}>
        <AuditTable />
      </Suspense>
    </main>
  );
}

async function AuditTable() {
  await connection();
  let page;
  try {
    page = await api.audit(200);
  } catch {
    return (
      <p className="mt-8 border-l-4 border-unsupported bg-paper px-4 py-3">
        The API is not reachable.
      </p>
    );
  }
  const { totals, items, total_items } = page;
  return (
    <>
      <dl className="mt-6 flex flex-wrap gap-x-10 gap-y-3">
        <Total label="Calls" value={totals.calls.toLocaleString()} />
        <Total label="Input tokens" value={totals.input_tokens.toLocaleString()} />
        <Total label="Output tokens" value={totals.output_tokens.toLocaleString()} />
        <Total label="Cost at list prices" value={usd(totals.cost_usd)} />
      </dl>
      {items.length === 0 ? (
        <p className="mt-6 border border-rule bg-paper p-6">
          No model calls yet. Processing an encounter adds rows here.
        </p>
      ) : (
        <div className="mt-6 overflow-x-auto border border-rule bg-paper">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-rule text-ink-soft">
              <tr>
                <th className="px-3 py-2.5 font-semibold">Time (UTC)</th>
                <th className="px-3 py-2.5 font-semibold">Purpose</th>
                <th className="px-3 py-2.5 font-semibold">Provider and model</th>
                <th className="px-3 py-2.5 text-right font-semibold">In</th>
                <th className="px-3 py-2.5 text-right font-semibold">Out</th>
                <th className="px-3 py-2.5 text-right font-semibold">Cost</th>
                <th className="px-3 py-2.5 text-right font-semibold">Latency</th>
                <th className="px-3 py-2.5 font-semibold">Status</th>
                <th className="px-3 py-2.5 font-semibold">Prompt SHA-256</th>
                <th className="px-3 py-2.5 font-semibold">Encounter</th>
              </tr>
            </thead>
            <tbody>
              {items.map((call) => (
                <tr key={call.id} className="border-b border-rule-soft align-top last:border-0">
                  <td className="px-3 py-2 whitespace-nowrap">
                    {dateTime(call.created_at)}
                    {call.origin === "sample_import" && (
                      <span className="block text-xs text-ink-soft">imported with sample</span>
                    )}
                  </td>
                  <td className="px-3 py-2">{PURPOSE_LABELS[call.purpose] ?? call.purpose}</td>
                  <td className="px-3 py-2">
                    {call.provider}
                    <span className="block text-ink-soft">{call.model}</span>
                  </td>
                  <td className="px-3 py-2 text-right">{call.input_tokens.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right">{call.output_tokens.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right">{usd(call.cost_usd)}</td>
                  <td className="px-3 py-2 text-right whitespace-nowrap">
                    {(call.latency_ms / 1000).toFixed(1)} s
                  </td>
                  <td
                    className={`px-3 py-2 ${call.status === "ok" ? "" : "font-semibold text-unsupported"}`}
                  >
                    {call.status}
                    {call.error && (
                      <span className="block max-w-xs text-xs font-normal break-words text-ink-soft">
                        {call.error}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs" title={call.prompt_sha256}>
                    {call.prompt_sha256.slice(0, 12)}
                  </td>
                  <td className="px-3 py-2">
                    {call.encounter_id ? (
                      <Link
                        href={`/encounters/${call.encounter_id}`}
                        className="underline underline-offset-4"
                      >
                        Open
                      </Link>
                    ) : (
                      <span className="text-ink-soft">deleted</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {total_items > items.length && (
        <p className="mt-3 text-sm text-ink-soft">
          Showing the latest {items.length} of {total_items} calls.
        </p>
      )}
      <p className="mt-3 text-sm text-ink-soft">
        Gemini calls are priced at paid-tier list rates; on the free tier they cost nothing.
      </p>
    </>
  );
}

function Total({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-sm text-ink-soft">{label}</dt>
      <dd className="text-2xl font-bold">{value}</dd>
    </div>
  );
}
