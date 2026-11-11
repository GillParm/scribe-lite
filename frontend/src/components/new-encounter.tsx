"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { clock } from "@/lib/format";
import type { EncounterSummary } from "@/lib/types";
import { toWav16kMono } from "@/lib/wav";

type Props = { geminiConfigured: boolean; anthropicConfigured: boolean; maxUploadMb: number };

async function upload(
  file: Blob,
  filename: string,
  title: string,
  source: "upload" | "recording",
): Promise<EncounterSummary> {
  const form = new FormData();
  form.append("file", file, filename);
  form.append("source", source);
  if (title.trim()) form.append("title", title.trim());
  const response = await fetch("/api/encounters", { method: "POST", body: form });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    const detail = typeof body?.detail === "string" ? body.detail : `error ${response.status}`;
    throw new Error(`Upload failed: ${detail}`);
  }
  return (await response.json()) as EncounterSummary;
}

export function NewEncounter({ geminiConfigured, anthropicConfigured, maxUploadMb }: Props) {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const timer = useRef<number | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(
    () => () => {
      if (timer.current) window.clearInterval(timer.current);
      recorder.current?.stream.getTracks().forEach((t) => t.stop());
    },
    [],
  );

  const ready = geminiConfigured && anthropicConfigured;

  async function send(file: Blob, filename: string, source: "upload" | "recording") {
    setError(null);
    setBusy(source === "recording" ? "Uploading the recording" : "Uploading");
    try {
      const created = await upload(file, filename, title, source);
      router.push(`/encounters/${created.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(null);
    }
  }

  async function startRecording() {
    setError(null);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError("The browser did not allow microphone access. Check the site permissions.");
      return;
    }
    chunks.current = [];
    const mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (event) => chunks.current.push(event.data);
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      setBusy("Converting the recording to 16 kHz WAV");
      try {
        const raw = new Blob(chunks.current, { type: mediaRecorder.mimeType });
        const wav = await toWav16kMono(raw);
        await send(wav, "recording.wav", "recording");
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setBusy(null);
      }
    };
    recorder.current = mediaRecorder;
    mediaRecorder.start();
    setElapsed(0);
    setRecording(true);
    const started = Date.now();
    timer.current = window.setInterval(() => setElapsed((Date.now() - started) / 1000), 250);
  }

  function stopRecording() {
    if (timer.current) window.clearInterval(timer.current);
    setRecording(false);
    recorder.current?.stop();
  }

  return (
    <section aria-labelledby="new-encounter" className="border border-rule bg-paper p-6">
      <h2 id="new-encounter" className="text-lg font-bold">
        New encounter
      </h2>
      <p className="mt-1 text-sm text-ink-soft">
        Record or upload a synthetic doctor-patient conversation. The audio is sent to Google Gemini
        for transcription; only redacted text reaches the note model.
      </p>

      {!ready && (
        <p className="mt-4 border-l-4 border-partial bg-highlight-soft px-3 py-2 text-sm">
          {!geminiConfigured && "Transcribing new audio needs GEMINI_API_KEY on the server. "}
          {!anthropicConfigured && "Writing notes needs ANTHROPIC_API_KEY on the server. "}
          The three samples below work without either.
        </p>
      )}

      <label className="mt-5 block text-sm font-semibold" htmlFor="title">
        Title <span className="font-normal text-ink-soft">(optional)</span>
      </label>
      <input
        id="title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        maxLength={200}
        placeholder="Follow-up, knee pain"
        className="mt-1 w-full border border-rule px-3 py-2"
        disabled={busy !== null || recording}
      />

      <div className="mt-5 flex flex-wrap gap-3">
        {recording ? (
          <button
            type="button"
            onClick={stopRecording}
            className="inline-flex items-center gap-2 bg-unsupported px-4 py-2 font-semibold text-white"
          >
            <span
              aria-hidden
              className="size-2.5 rounded-full bg-white motion-safe:animate-pulse"
            />
            Stop and upload ({clock(elapsed)})
          </button>
        ) : (
          <button
            type="button"
            onClick={startRecording}
            disabled={busy !== null || !ready}
            className="bg-ink px-4 py-2 font-semibold text-white disabled:opacity-40"
          >
            Record in browser
          </button>
        )}
        <button
          type="button"
          onClick={() => fileInput.current?.click()}
          disabled={busy !== null || recording || !ready}
          className="border border-ink px-4 py-2 font-semibold disabled:opacity-40"
        >
          Upload audio file
        </button>
        <input
          ref={fileInput}
          type="file"
          accept="audio/*,.m4a,.wav,.mp3,.ogg,.flac,.webm"
          className="sr-only"
          tabIndex={-1}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void send(file, file.name, "upload");
            e.target.value = "";
          }}
        />
      </div>
      <p className="mt-3 text-xs text-ink-soft">
        WAV, MP3, M4A, OGG, FLAC or WebM, up to {maxUploadMb} MB.
      </p>

      <p aria-live="polite" className="mt-3 min-h-5 text-sm">
        {busy && <span>{busy}…</span>}
        {error && <span className="text-unsupported">{error}</span>}
      </p>
    </section>
  );
}
