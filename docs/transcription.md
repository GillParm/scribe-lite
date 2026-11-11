# Transcription and speaker labels

Demo only. Not for clinical use. The recordings here are synthetic.

## How it works

`GeminiTranscriber` sends the whole recording to Gemini with a JSON response schema and
asks for one segment per speaker turn: `{id, speaker: doctor|patient, start, end, text}`.
The app then keeps the model's order, makes the times monotonic and renumbers the
segments `s1..sN` (`normalize_segments`). Segment IDs are assigned by the app, never
taken from the model, because the note cites them.

The bundled samples do not need Gemini: `ReferenceTranscriber` recognises them by slug
or audio hash and returns the scripted turns with the exact timestamps recorded when the
audio was stitched together.

## Which model

Both candidate models were run once on each sample (`eval/compare_transcribers.py`; raw
output in `eval/results/transcription/`). Speaker accuracy counts only words that both
the reference and the model's transcript contain, so it measures labelling separately
from recognition. A mixed segment is one model segment holding words from both speakers'
turns, which means a missed speaker change.

| Model | Mean speaker accuracy | Mean WER | Mixed segments (3 samples) | Median turn-start error | Time per 3.3-min recording |
| --- | --- | --- | --- | --- | --- |
| gemini-3.8-flash | 100.0% | 1.4% | 0 | 0.19 to 0.56 s | 181 to 235 s |
| gemini-3.5-flash-lite | 93.7% | 1.3% | 7 | 0.16 to 39.8 s | 8 to 9 s |

`gemini-3.8-flash` is the default because it got every speaker label right and kept turn
boundaries. `gemini-3.5-flash-lite` is about 20 times faster and recognised words just as
well, but merged turns on two of the three samples and drifted by about 40 seconds on
their timestamps, which would break click-to-seek. Set `GEMINI_TRANSCRIBE_MODEL` to
switch. The latencies were measured on the free tier on 2026-11-10 and will vary.

An earlier run sorted segments by start time; flash-lite's overlapping timestamps on one
sample scrambled the turn order under that rule, which is why the app now keeps the
model's order. Both models were re-run after the fix and the table above is from that
run.

## Limits of LLM-based diarization

- **Roles are inferred from content, not from voices.** The model decides who is the
  doctor by what is said. It works when the doctor asks questions and gives the plan; it
  can fail for a nurse, a family member or an interpreter, and there are only two labels.
- **The samples are easy.** Two clearly different synthetic voices, no crosstalk, no
  background noise and clean pauses between turns. Real clinic audio has overlapping
  speech, short back-channel turns ("mm-hm"), room noise and accents. These numbers say
  nothing about that.
- **Timestamps are generated, not measured.** The model writes times as text. They can
  drift, overlap or go backwards, as flash-lite showed. A dedicated ASR with word-level
  alignment gives reliable times; this demo only uses times to seek the audio.
- **Names can be misheard.** Both models wrote "Tyler Nguyen" as something else on one
  sample ("Guyon", "Gion"). A misheard name is still a name for redaction, but recall was
  only measured on the scripted transcripts, where the spellings are known.
- **Three samples is a smoke test.** Each model ran once per sample. Treat the table as
  a sanity check, not a benchmark.

## Privacy

The audio goes to Google unredacted. Redaction only protects text sent to the
note-writing model. A real deployment would use on-premises ASR or a speech service
covered by a business associate agreement. See the privacy section of the README.
