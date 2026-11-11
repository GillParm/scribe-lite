import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";

import { EncounterView } from "@/components/encounter-view";
import { api, ApiError } from "@/lib/server-api";
import type { EncounterDetail } from "@/lib/types";

export const metadata: Metadata = { title: "Encounter" };

export default function EncounterPage({ params }: PageProps<"/encounters/[id]">) {
  return (
    <main className="mx-auto max-w-[1400px] px-6 py-6">
      <Suspense fallback={<p className="text-ink-soft">Loading the encounter…</p>}>
        <Loader params={params} />
      </Suspense>
    </main>
  );
}

async function fetchEncounter(id: string): Promise<EncounterDetail> {
  try {
    return await api.encounter(id);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }
}

async function Loader({ params }: { params: Promise<{ id: string }> }) {
  await connection();
  const { id } = await params;
  const detail = await fetchEncounter(id);
  return <EncounterView initial={detail} />;
}
