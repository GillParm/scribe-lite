import { STATUS_LABELS } from "@/lib/format";
import type { EncounterStatus } from "@/lib/types";
import { IN_PROGRESS } from "@/lib/types";

const TONE: Record<"ready" | "failed" | "working", string> = {
  ready: "text-supported",
  failed: "text-unsupported",
  working: "text-partial",
};

export function StatusText({ status }: { status: EncounterStatus }) {
  const tone = status === "ready" ? "ready" : status === "failed" ? "failed" : "working";
  return (
    <span className={`inline-flex items-center gap-1.5 font-semibold ${TONE[tone]}`}>
      {IN_PROGRESS.includes(status) && (
        <span aria-hidden className="size-2 rounded-full bg-partial motion-safe:animate-pulse" />
      )}
      {STATUS_LABELS[status]}
    </span>
  );
}
