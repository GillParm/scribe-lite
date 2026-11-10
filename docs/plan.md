# scribe-lite: build plan

Demo only. Not for clinical use. Every conversation in this repository is synthetic.

This is the plan written before any code. The README records where the build ended up
differing from it.

## Goal

Turn a recorded doctor-patient conversation into a SOAP note where every statement
points back to the transcript segments it came from, then check each statement
against the transcript and flag the ones the transcript does not support. Personal
details are replaced with placeholders before any text reaches a language model, and
every model call is written to an audit log.

## Pipeline

```
audio ─▶ transcribe ─▶ redact PII ─▶ draft SOAP note ─▶ validate segment IDs ─▶ check claims ─▶ store
          (provider)    (Presidio)     (Claude, structured)   (repair/reject)     (Claude)
```

1. **Ingest.** Save the upload (or browser recording) under `DATA_DIR/audio/`, keep the
   path, MIME type and SHA-256 in Postgres.
2. **Transcribe.** A `Transcriber` protocol with three implementations:
   - `GeminiTranscriber`: sends the audio to Gemini with a JSON response schema and asks
     for `{id, speaker: doctor|patient, start, end, text}` segments. Both
     `gemini-3.8-flash` and `gemini-3.5-flash-lite` are measured on the samples; the one
     with better speaker labels becomes the default.
   - `ReferenceTranscriber`: recognises a bundled sample (by slug or audio hash) and
     returns the scripted ground truth with exact timestamps, so the demo runs with no
     keys.
   - `FakeTranscriber`: canned segments for tests.
3. **Redact.** Presidio analyzer with a spaCy model plus custom recognizers for
   date-of-birth phrases, phone numbers (digits and spoken digits) and street addresses.
   The whole transcript is analysed as one document so a date of birth answered in the
   patient's turn is linked to the doctor's question in the previous turn. Each distinct
   value gets a stable placeholder (`[PERSON_1]`, `[DATE_OF_BIRTH_1]`, ...). The mapping
   lives only in the local database and is used to restore real values for display.
4. **Draft the note.** `claude-opus-5-5` with structured output parsed into Pydantic
   models: four sections, each a list of statements with `source_segment_ids`.
5. **Validate citations.** IDs are normalised (`S3`, `3`, `seg-3` → `s3`); unknown IDs are
   dropped; a statement left with no valid source is rejected and recorded, not shown.
6. **Check claims.** A second Claude pass sees the redacted transcript and every
   statement with its cited segments, and labels each one `supported`,
   `partially_supported` or `unsupported` with a short reason. Missing verdicts become
   `unchecked`.
7. **Store and display.** The API returns the note with placeholders restored. The UI
   can also show the redacted text the model actually saw.

## Data model (Postgres, SQLAlchemy 2, Alembic)

| Table | Purpose |
| --- | --- |
| `encounters` | title, source (upload, recording, sample), status, audio path/MIME/hash, audio deleted time, transcriber used, error |
| `segments` | per-encounter segment ID (`s1`...), speaker, start/end seconds, original text, redacted text, redaction spans |
| `pii_entities` | placeholder, entity type, canonical value, surface variants (local only) |
| `statements` | section, position, text (redacted), cited segment IDs, support label, reason |
| `rejected_statements` | statements dropped during citation validation, with the reason |
| `model_calls` | audit log: timestamp, provider, model, purpose, prompt SHA-256, input/output tokens, cost USD, latency, status |
| `retention_deletions` | what the retention job deleted and when |

## Audit log and retention

- One `model_calls` row per call, written whether the call succeeds, fails or is refused.
  The prompt hash is SHA-256 over the canonical JSON of model, system prompt, messages
  and output schema. Cost comes from a price table in code.
- `scribe retention run [--dry-run]` deletes audio older than `RETENTION_DAYS`, clears
  the path, stamps `audio_deleted_at` and writes a `retention_deletions` row. The API
  runs the same function on an interval (`RETENTION_INTERVAL_MINUTES`). A clock is
  injected so tests can move time.

## Samples

Three scripted conversations of 2-4 minutes: sore throat, type 2 diabetes follow-up,
ankle sprain. Each script carries fake names, dates of birth, phone numbers and
addresses, and a list of those values so redaction recall can be measured. A tool voices
each turn with macOS `say` (two voices per sample), stitches the turns with short pauses
into one 16 kHz mono file, records exact turn timestamps, and converts to `.m4a` with
`afconvert`. Scripts, audio, reference facts and precomputed pipeline outputs are all
committed so the UI works with no keys.

Reference notes are written as lists of key facts from the scripts, before any model
output is generated.

## Evaluation

`eval/run_eval.py` scores each generated note against its reference facts:

- **Coverage**: Claude judges each fact as present, partial or absent with a rubric;
  coverage = (present + 0.5 x partial) / facts.
- **Unsupported-claim rate**: unsupported statements / all statements, from the checker.

It writes raw outputs to `eval/results/` and a table into the README between markers.
Separate scripts measure PII recall and the Gemini speaker-label comparison.

## API (FastAPI)

- `GET /api/health`, `GET /api/config` (which providers are configured, as booleans)
- `GET/POST /api/encounters`, `GET/DELETE /api/encounters/{id}`,
  `POST /api/encounters/{id}/process`, `GET /api/encounters/{id}/audio` (range requests)
- `GET /api/audit` with totals, `GET /api/retention`, `POST /api/retention/run`

Processing runs as a background task after the upload returns.

## UI (Next.js 16, TypeScript, Tailwind)

- Encounter list with upload and in-browser recording (MediaRecorder; the recording is
  decoded and re-encoded as 16 kHz mono WAV in the browser before upload).
- Encounter page: speaker-labelled transcript beside the SOAP note; clicking a statement
  highlights its source segments and seeks the audio; unsupported statements are flagged
  with the checker's reason; a toggle shows the redacted text the model saw.
- Audit log page. A demo-only banner on every page.
- The browser only talks to the Next.js server, which proxies `/api/*` to the API at
  runtime (`API_URL`), so the same image works locally and on Cloud Run.

## Testing and CI

pytest runs offline: redaction and reversal, PII recall on the samples, schemas and
segment-ID validation, checker parsing, audit log, retention with a fake clock, and the
API with fake providers. Live tests are marked `live` and skipped without keys. Tests use
`TEST_DATABASE_URL` (Postgres in CI) and fall back to SQLite locally.

CI: ruff, ruff format, strict mypy, pytest against a Postgres 17 service, ESLint,
Prettier, tsc, both Docker builds, gitleaks.

## Deployment

Cloud Run-ready Dockerfiles that listen on `$PORT`. Documented path: Cloud SQL for
Postgres, a GCS bucket for audio, Secret Manager for keys, and a Cloud Run job plus Cloud
Scheduler for retention.

## Budget

Anthropic spend for building and evaluating this project is capped at $10. Opus 5.5 is
$4 per million input tokens and $20 per million output tokens; each note plus check is
expected to cost well under $0.50. Gemini transcription uses the free tier.

## Milestones

1. Plan and scaffold
2. Database models, audit log, retention
3. Samples: scripts, voiced audio, reference facts
4. Transcription providers and the Gemini model comparison
5. PII redaction and recall measurement
6. SOAP generation and source linking
7. Hallucination check and the full pipeline
8. API
9. UI
10. Evaluation and results
11. Docker, CI, README, deployment notes
