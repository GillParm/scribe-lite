# Sample conversations

**Synthetic data only. Demo only, not for clinical use.** These conversations were
written for this repository. Every name, date of birth, phone number and address in
them is invented, and the medical content is illustrative, not advice.

Each folder holds:

| File | What it is |
| --- | --- |
| `script.json` | The scripted turns, the two `say` voices, and the list of invented personal details (used to measure redaction recall). |
| `audio.m4a` | The script voiced with macOS `say`, 16 kHz mono AAC. |
| `transcript.json` | The scripted turns with exact timestamps from the stitching step. The reference transcriber serves this, so samples work with no API key. |
| `reference_facts.json` | Key facts per SOAP section, written from the script before any note was generated. The evaluation scores coverage against these. |
| `outputs/` | Precomputed pipeline outputs (redaction, note, claim check, audit rows) so the UI works with no keys. |

Rebuild the audio on a Mac with `make samples`.
