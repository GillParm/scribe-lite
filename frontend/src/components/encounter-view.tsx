"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { StatusText } from "@/components/status";
import {
  clock,
  ENTITY_LABELS,
  plural,
  PURPOSE_LABELS,
  SECTION_TITLES,
  SOURCE_LABELS,
  SUPPORT_LABELS,
  usd,
} from "@/lib/format";
import type { EncounterDetail, Segment, Statement, Support, View } from "@/lib/types";
import { IN_PROGRESS } from "@/lib/types";

const STEPS = ["transcribing", "redacting", "drafting", "checking"] as const;
const STEP_LABELS = ["Transcribe", "Redact", "Write note", "Check claims"];

function prefersReducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// Scroll a pane so `target` sits in its middle, without moving the page around it. On
// narrow screens the pane does not scroll, so fall back to scrolling the page.
function scrollWithin(pane: HTMLElement | null, target: HTMLElement | undefined) {
  if (!target) return;
  const behavior = prefersReducedMotion() ? "auto" : "smooth";
  if (pane && pane.scrollHeight > pane.clientHeight) {
    // The pane is position: relative, so offsetTop is already measured from its top.
    const offset = target.offsetTop;
    pane.scrollTo({ top: offset - pane.clientHeight / 2 + target.clientHeight / 2, behavior });
  } else {
    target.scrollIntoView({ block: "center", behavior });
  }
}

export function EncounterView({ initial }: { initial: EncounterDetail }) {
  const [detail, setDetail] = useState(initial);
  const [view, setView] = useState<View>(initial.view);
  const [selected, setSelected] = useState<Statement | null>(null);
  const [selectedSegment, setSelectedSegment] = useState<string | null>(null);
  const [now, setNow] = useState(0);
  const [stopAt, setStopAt] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const segmentRefs = useRef(new Map<string, HTMLElement>());
  const transcriptPane = useRef<HTMLElement>(null);

  const load = useCallback(
    async (nextView: View) => {
      const response = await fetch(`/api/encounters/${detail.id}?view=${nextView}`, {
        cache: "no-store",
      });
      if (response.ok) setDetail((await response.json()) as EncounterDetail);
    },
    [detail.id],
  );

  function changeView(next: View) {
    setView(next);
    void load(next);
  }

  const processing = IN_PROGRESS.includes(detail.status);
  useEffect(() => {
    if (!processing) return;
    const timer = window.setInterval(() => void load(view), 2500);
    return () => window.clearInterval(timer);
  }, [processing, load, view]);

  const segmentsById = useMemo(
    () => new Map(detail.segments.map((s) => [s.id, s])),
    [detail.segments],
  );
  const cited = useMemo(() => new Set(selected?.source_segment_ids ?? []), [selected]);
  const citingSelectedSegment = useMemo(() => {
    if (!selectedSegment) return new Set<string>();
    return new Set(
      detail.sections
        .flatMap((s) => s.statements)
        .filter((st) => st.source_segment_ids.includes(selectedSegment))
        .map((st) => st.id),
    );
  }, [detail.sections, selectedSegment]);
  const playing = detail.segments.find((s) => now >= s.start && now < s.end)?.id ?? null;

  function seek(seconds: number) {
    if (audio.current) audio.current.currentTime = seconds;
  }

  function selectStatement(statement: Statement) {
    if (selected?.id === statement.id) {
      setSelected(null);
      return;
    }
    setSelected(statement);
    setSelectedSegment(null);
    const sources = statement.source_segment_ids
      .map((id) => segmentsById.get(id))
      .filter((s): s is Segment => s !== undefined);
    if (sources.length === 0) return;
    seek(Math.min(...sources.map((s) => s.start)));
    scrollWithin(transcriptPane.current, segmentRefs.current.get(sources[0].id));
  }

  function playSource(statement: Statement) {
    const sources = statement.source_segment_ids
      .map((id) => segmentsById.get(id))
      .filter((s): s is Segment => s !== undefined);
    if (!audio.current || sources.length === 0) return;
    seek(Math.min(...sources.map((s) => s.start)));
    setStopAt(Math.max(...sources.map((s) => s.end)));
    void audio.current.play();
  }

  function selectSegment(segment: Segment) {
    setSelected(null);
    setSelectedSegment(selectedSegment === segment.id ? null : segment.id);
    seek(segment.start);
  }

  function onTimeUpdate() {
    const element = audio.current;
    if (!element) return;
    setNow(element.currentTime);
    if (stopAt !== null && element.currentTime >= stopAt) {
      element.pause();
      setStopAt(null);
    }
  }

  async function reprocess() {
    setActionError(null);
    const response = await fetch(`/api/encounters/${detail.id}/process`, { method: "POST" });
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as { detail?: string } | null;
      setActionError(body?.detail ?? `Could not start processing (error ${response.status}).`);
      return;
    }
    await load(view);
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setSelected(null);
        setSelectedSegment(null);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const statements = detail.sections.flatMap((s) => s.statements);
  const redactedView = view === "redacted";

  return (
    <div>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-ink-soft">
            <Link href="/" className="underline underline-offset-4">
              Encounters
            </Link>
          </p>
          <h1 className="mt-1 font-serif text-3xl font-semibold">{detail.title}</h1>
          <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-sm">
            <Meta label="Source" value={SOURCE_LABELS[detail.source]} />
            {detail.duration_seconds != null && (
              <Meta label="Length" value={clock(detail.duration_seconds)} />
            )}
            <div className="flex gap-1.5">
              <dt className="text-ink-soft">Status</dt>
              <dd>
                <StatusText status={detail.status} />
              </dd>
            </div>
            {detail.transcriber && <Meta label="Transcript" value={detail.transcriber} />}
            {detail.note_model && <Meta label="Note and check" value={detail.note_model} />}
          </dl>
        </div>
        <ViewToggle view={view} onChange={changeView} />
      </header>

      {detail.status === "failed" && (
        <div className="mt-5 border-l-4 border-unsupported bg-paper px-4 py-3">
          <p className="font-semibold">Processing stopped.</p>
          <p className="mt-1 text-sm break-words">{detail.error}</p>
          {detail.audio_available && (
            <button
              type="button"
              onClick={reprocess}
              className="mt-3 bg-ink px-3 py-1.5 text-sm font-semibold text-white"
            >
              Process again
            </button>
          )}
          {actionError && <p className="mt-2 text-sm text-unsupported">{actionError}</p>}
        </div>
      )}
      {processing && <Progress status={detail.status} />}

      <div className="sticky top-9 z-20 mt-5 border border-rule bg-paper px-4 py-3">
        {detail.audio_available ? (
          <>
            <audio
              ref={audio}
              controls
              preload="metadata"
              src={`/api/encounters/${detail.id}/audio`}
              onTimeUpdate={onTimeUpdate}
              onPause={() => setStopAt(null)}
              className="h-9 w-full"
            >
              Your browser cannot play this audio.
            </audio>
            <Timeline
              segments={detail.segments}
              duration={detail.duration_seconds ?? 0}
              cited={cited}
              playing={playing}
              onSeek={(segment) => selectSegment(segment)}
            />
          </>
        ) : (
          <p className="text-sm text-ink-soft">
            The audio was deleted by the retention job
            {detail.audio_deleted_at ? ` on ${detail.audio_deleted_at.slice(0, 10)}` : ""}. The
            transcript and note are kept.
          </p>
        )}
      </div>

      <div className="mt-5 grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <section
          ref={transcriptPane}
          aria-labelledby="transcript-heading"
          className="relative border border-rule bg-paper lg:h-[calc(100vh-22rem)] lg:min-h-[28rem] lg:overflow-y-auto"
        >
          <div className="sticky top-0 z-10 border-b border-rule bg-paper px-5 py-3">
            <h2 id="transcript-heading" className="font-bold">
              Transcript
            </h2>
            <PiiSummary detail={detail} redactedView={redactedView} />
          </div>
          {detail.segments.length === 0 ? (
            <p className="px-5 py-4 text-ink-soft">No transcript yet.</p>
          ) : (
            <ol className="divide-y divide-rule-soft">
              {detail.segments.map((segment) => (
                <li
                  key={segment.id}
                  ref={(element) => {
                    if (element) segmentRefs.current.set(segment.id, element);
                    else segmentRefs.current.delete(segment.id);
                  }}
                  className={`border-l-4 ${playing === segment.id ? "border-l-ink" : "border-l-transparent"}`}
                >
                  <button
                    type="button"
                    onClick={() => selectSegment(segment)}
                    aria-pressed={selectedSegment === segment.id}
                    className={`grid w-full grid-cols-[3.5rem_minmax(0,1fr)] gap-x-3 px-4 py-2.5 text-left hover:bg-desk/40 ${
                      selectedSegment === segment.id ? "bg-desk/60" : ""
                    }`}
                  >
                    <span className="pt-0.5 text-xs text-ink-soft">
                      {clock(segment.start)}
                      <span className="block">{segment.id}</span>
                    </span>
                    <span>
                      <span
                        className={`text-sm font-bold ${
                          segment.speaker === "doctor" ? "text-doctor" : "text-patient"
                        }`}
                      >
                        {segment.speaker === "doctor" ? "Doctor" : "Patient"}
                      </span>
                      <span className="mt-0.5 block leading-relaxed">
                        <span className={cited.has(segment.id) ? "marker" : undefined}>
                          {redactedView ? <Redacted segment={segment} /> : segment.text}
                        </span>
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </section>

        <section
          aria-labelledby="note-heading"
          className="border border-rule bg-paper lg:h-[calc(100vh-22rem)] lg:min-h-[28rem] lg:overflow-y-auto"
        >
          <div className="sticky top-0 z-10 border-b border-rule bg-paper px-6 py-3">
            <h2 id="note-heading" className="font-bold">
              SOAP note
            </h2>
            <CheckSummaryLine detail={detail} />
          </div>
          {statements.length === 0 ? (
            <p className="px-6 py-4 text-ink-soft">
              {processing
                ? "The note appears here when it is ready."
                : "No note for this encounter."}
            </p>
          ) : (
            <div className="px-6 pt-2 pb-6">
              {detail.sections.map((section) => (
                <section
                  key={section.name}
                  className="mt-5"
                  aria-label={SECTION_TITLES[section.name]}
                >
                  <h3 className="border-b border-rule-soft pb-1 font-serif text-xl font-semibold">
                    {SECTION_TITLES[section.name]}
                  </h3>
                  {section.statements.length === 0 ? (
                    <p className="mt-2 text-sm text-ink-soft">Nothing recorded.</p>
                  ) : (
                    <ul className="mt-1">
                      {section.statements.map((statement) => (
                        <StatementItem
                          key={statement.id}
                          statement={statement}
                          selected={selected?.id === statement.id}
                          linked={citingSelectedSegment.has(statement.id)}
                          canPlay={detail.audio_available}
                          onSelect={() => selectStatement(statement)}
                          onPlay={() => playSource(statement)}
                        />
                      ))}
                    </ul>
                  )}
                </section>
              ))}
              {detail.rejected_statements.length > 0 && (
                <details className="mt-6 text-sm">
                  <summary className="cursor-pointer text-ink-soft">
                    {plural(detail.rejected_statements.length, [
                      "statement was",
                      "statements were",
                    ])}{" "}
                    removed because none of their citations pointed at a real segment
                  </summary>
                  <ul className="mt-2 space-y-1">
                    {detail.rejected_statements.map((r, index) => (
                      <li key={index} className="text-ink-soft line-through">
                        {r.text} (cited {r.cited_ids.join(", ") || "nothing"})
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}
          <ModelCalls detail={detail} />
        </section>
      </div>
    </div>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-1.5">
      <dt className="text-ink-soft">{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function ViewToggle({ view, onChange }: { view: View; onChange: (view: View) => void }) {
  const options: [View, string][] = [
    ["display", "Real values"],
    ["redacted", "What the model saw"],
  ];
  return (
    <div role="radiogroup" aria-label="Text shown" className="flex border border-ink">
      {options.map(([value, label]) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={view === value}
          onClick={() => onChange(value)}
          className={`px-3 py-1.5 text-sm font-semibold ${
            view === value ? "bg-ink text-white" : "bg-paper text-ink"
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function Progress({ status }: { status: EncounterDetail["status"] }) {
  const current = STEPS.indexOf(status as (typeof STEPS)[number]);
  return (
    <ol className="mt-5 flex flex-wrap gap-x-6 gap-y-2 border border-rule bg-paper px-4 py-3 text-sm">
      {STEP_LABELS.map((label, index) => (
        <li
          key={label}
          className={
            index < current
              ? "text-supported"
              : index === current
                ? "font-semibold text-ink"
                : "text-ink-soft"
          }
        >
          {index + 1}. {label}
          {index === current && "…"}
        </li>
      ))}
    </ol>
  );
}

function Timeline({
  segments,
  duration,
  cited,
  playing,
  onSeek,
}: {
  segments: Segment[];
  duration: number;
  cited: Set<string>;
  playing: string | null;
  onSeek: (segment: Segment) => void;
}) {
  if (!duration || segments.length === 0) return null;
  return (
    <div className="relative mt-2 h-4" aria-hidden>
      {segments.map((segment) => (
        <div
          key={segment.id}
          onClick={() => onSeek(segment)}
          title={`${segment.id} ${segment.speaker} ${clock(segment.start)}`}
          className={`absolute top-1 h-2 cursor-pointer ${
            segment.speaker === "doctor" ? "bg-doctor/45" : "bg-patient/45"
          } ${cited.has(segment.id) ? "!top-0 !h-4 !bg-highlight outline outline-ink/40" : ""} ${
            playing === segment.id ? "!bg-ink" : ""
          }`}
          style={{
            left: `${(segment.start / duration) * 100}%`,
            width: `max(2px, ${((segment.end - segment.start) / duration) * 100}%)`,
          }}
        />
      ))}
    </div>
  );
}

function Redacted({ segment }: { segment: Segment }) {
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  for (const span of [...segment.redactions].sort((a, b) => a.start - b.start)) {
    parts.push(segment.text.slice(cursor, span.start));
    parts.push(
      <span key={span.start} className="tape" title={span.entity_type}>
        {span.placeholder}
      </span>,
    );
    cursor = span.end;
  }
  parts.push(segment.text.slice(cursor));
  return <>{parts}</>;
}

function PiiSummary({ detail, redactedView }: { detail: EncounterDetail; redactedView: boolean }) {
  if (detail.pii.length === 0) return null;
  const counts = new Map<string, number>();
  for (const item of detail.pii)
    counts.set(item.entity_type, (counts.get(item.entity_type) ?? 0) + 1);
  const parts = [...counts].map(([type, count]) =>
    plural(count, ENTITY_LABELS[type] ?? [type, type]),
  );
  return (
    <details className="mt-1 text-sm text-ink-soft">
      <summary className="cursor-pointer">
        Redacted before the note model saw it: {parts.join(", ")}
      </summary>
      <table className="mt-2 w-full text-left text-xs">
        <tbody>
          {detail.pii.map((item) => (
            <tr key={item.placeholder}>
              <td className="py-0.5 pr-3">
                <span className="tape">{item.placeholder}</span>
              </td>
              <td className="py-0.5 pr-3 text-ink">
                {redactedView ? "hidden in this view" : item.value}
              </td>
              <td className="py-0.5">{plural(item.occurrences, ["mention", "mentions"])}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs">This mapping is stored only in this server&apos;s database.</p>
    </details>
  );
}

function CheckSummaryLine({ detail }: { detail: EncounterDetail }) {
  const total = Object.values(detail.check_summary).reduce((a, b) => a + b, 0);
  if (total === 0) return null;
  const order: Support[] = ["supported", "partially_supported", "unsupported", "unchecked"];
  const parts = order
    .filter((key) => detail.check_summary[key] > 0)
    .map((key) => `${detail.check_summary[key]} ${SUPPORT_LABELS[key].toLowerCase()}`);
  return (
    <p className="mt-0.5 text-sm text-ink-soft">
      {plural(total, ["statement", "statements"])} checked against the transcript:{" "}
      {parts.join(", ")}. Select one to mark its source.
    </p>
  );
}

const SUPPORT_CLASS: Record<Support, string> = {
  supported: "",
  partially_supported: "mark-partial",
  unsupported: "mark-unsupported",
  unchecked: "mark-unchecked",
};
const SUPPORT_TEXT: Record<Support, string> = {
  supported: "text-supported",
  partially_supported: "text-partial",
  unsupported: "text-unsupported",
  unchecked: "text-ink-soft",
};

function StatementItem({
  statement,
  selected,
  linked,
  canPlay,
  onSelect,
  onPlay,
}: {
  statement: Statement;
  selected: boolean;
  linked: boolean;
  canPlay: boolean;
  onSelect: () => void;
  onPlay: () => void;
}) {
  const support = statement.support ?? "unchecked";
  const flagged = support !== "supported";
  return (
    <li className={`-mx-3 px-3 py-1.5 ${linked ? "bg-highlight-soft" : ""}`}>
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        aria-describedby={flagged ? `${statement.id}-flag` : undefined}
        className="w-full text-left font-serif text-[1.075rem] leading-relaxed"
      >
        <span className={`${selected ? "marker" : ""} ${SUPPORT_CLASS[support]}`}>
          {statement.text}
        </span>{" "}
        <span className="font-sans text-xs whitespace-nowrap text-ink-soft">
          {statement.source_segment_ids.join(" ")}
          {support === "supported" && (
            <svg
              aria-label="Supported"
              role="img"
              viewBox="0 0 12 12"
              className="ml-1 inline size-3 align-baseline text-supported"
            >
              <path d="M2 6.5 4.8 9 10 3" fill="none" stroke="currentColor" strokeWidth="1.8" />
            </svg>
          )}
        </span>
      </button>
      {(flagged || selected) && statement.support_reason && (
        <p id={`${statement.id}-flag`} className={`mt-0.5 text-sm ${SUPPORT_TEXT[support]}`}>
          <span className="font-semibold">{SUPPORT_LABELS[support]}.</span>{" "}
          {statement.support_reason}
          {statement.citations_repaired && " Citations were repaired before the check."}
        </p>
      )}
      {selected && canPlay && (
        <button
          type="button"
          onClick={onPlay}
          className="mt-1 border border-ink px-2 py-0.5 text-sm font-semibold"
        >
          Play the source
        </button>
      )}
    </li>
  );
}

function ModelCalls({ detail }: { detail: EncounterDetail }) {
  if (detail.model_calls.length === 0) return null;
  return (
    <details className="border-t border-rule px-6 py-3 text-sm">
      <summary className="cursor-pointer text-ink-soft">
        {plural(detail.model_calls.length, ["model call", "model calls"])} for this encounter
        {detail.model_calls.every((c) => c.origin === "sample_import") &&
          " (recorded when the sample outputs were generated)"}
      </summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-ink-soft">
            <tr>
              <th className="py-1 pr-3 font-semibold">Purpose</th>
              <th className="py-1 pr-3 font-semibold">Model</th>
              <th className="py-1 pr-3 text-right font-semibold">Tokens in / out</th>
              <th className="py-1 pr-3 text-right font-semibold">Cost</th>
              <th className="py-1 pr-3 text-right font-semibold">Latency</th>
              <th className="py-1 font-semibold">Status</th>
            </tr>
          </thead>
          <tbody>
            {detail.model_calls.map((call) => (
              <tr key={call.id} className="border-t border-rule-soft">
                <td className="py-1 pr-3">{PURPOSE_LABELS[call.purpose] ?? call.purpose}</td>
                <td className="py-1 pr-3">{call.model}</td>
                <td className="py-1 pr-3 text-right">
                  {call.input_tokens.toLocaleString()} / {call.output_tokens.toLocaleString()}
                </td>
                <td className="py-1 pr-3 text-right">{usd(call.cost_usd)}</td>
                <td className="py-1 pr-3 text-right">{(call.latency_ms / 1000).toFixed(1)} s</td>
                <td className="py-1">{call.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2">
        <Link href="/audit" className="underline underline-offset-4">
          Full audit log
        </Link>
      </p>
    </details>
  );
}
