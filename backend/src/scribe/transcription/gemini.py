"""Transcription and speaker labelling with Gemini.

Gemini hears the audio and returns JSON segments that follow a response schema. Speaker
labels come from the model's reading of the conversation, not from a dedicated
diarization model, so they can drift on short or ambiguous turns. See
docs/transcription.md for measured accuracy and the limits.

Privacy: the audio itself goes to Google unredacted. Redaction only protects the text
sent to the note-writing model afterwards.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import Any, Literal

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from scribe.audit import AuditLog
from scribe.models import Speaker
from scribe.pricing import Usage
from scribe.schemas import TranscriptSegment
from scribe.transcription.base import (
    AudioInput,
    Transcription,
    TranscriptionError,
    normalize_segments,
)

log = logging.getLogger(__name__)

PROMPT = """\
Transcribe this recording of a conversation between a doctor and a patient.

Split the transcript into segments. Start a new segment every time the speaker changes.
For each segment return:
- id: "s1", "s2", ... in order.
- speaker: "doctor" or "patient". Decide from what is said: the doctor asks the clinical
  questions, examines, explains findings and gives the plan; the patient describes their
  symptoms and history. The same voice keeps the same label for the whole recording.
- start and end: seconds from the start of the recording, as decimal numbers.
- text: the words exactly as spoken. Write numbers as digits (38.5, 400 milligrams,
  416-555-0182). Do not summarise, correct or add anything. Write [inaudible] for
  anything you cannot make out.
"""

# Gemini accepts these audio types; anything else is sent as-is and may be rejected.
GEMINI_MIME = {
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/x-m4a": "audio/mp4",
    "audio/m4a": "audio/mp4",
    "audio/mp3": "audio/mpeg",
    "audio/x-flac": "audio/flac",
}

RETRYABLE = {429, 500, 503}


class GeminiSegment(BaseModel):
    id: str
    speaker: Literal["doctor", "patient"]
    start: float
    end: float
    text: str


class GeminiTranscript(BaseModel):
    segments: list[GeminiSegment]


def _retry_delay(error: errors.APIError, attempt: int) -> float:
    """Use the server's RetryInfo delay when it sends one, else exponential backoff."""
    details: Any = getattr(error, "details", None)
    text = str(details) if details else str(error)
    match = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", text)
    if match:
        return min(float(match.group(1)) + 1.0, 120.0)
    return float(min(5 * 2**attempt, 60))


def _usage(response: types.GenerateContentResponse) -> Usage:
    meta = response.usage_metadata
    if meta is None:
        return Usage()
    return Usage(
        input_tokens=meta.prompt_token_count or 0,
        # Thinking tokens are billed as output.
        output_tokens=(meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0),
        cache_read_tokens=meta.cached_content_token_count or 0,
    )


class GeminiTranscriber:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        audit: AuditLog,
        client: genai.Client | None = None,
        max_retries: int = 5,
        timeout_s: float = 900.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model
        self.audit = audit
        # gemini-3.8-flash took up to 12 minutes per request on the free tier; without a
        # timeout a stalled request would hold a worker forever.
        self.client = client or genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000))
        )
        self.max_retries = max_retries
        self.sleep = sleep

    def transcribe(self, audio: AudioInput, *, encounter_id: str | None = None) -> Transcription:
        data = audio.path.read_bytes()
        mime = GEMINI_MIME.get(audio.mime_type, audio.mime_type)
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=GeminiTranscript,
        )
        contents: list[Any] = [types.Part.from_bytes(data=data, mime_type=mime), PROMPT]
        prompt_record = {
            "model": self.model,
            "prompt": PROMPT,
            "audio_sha256": audio.sha256,
            "audio_mime": mime,
            "schema": GeminiTranscript.model_json_schema(),
        }

        response: types.GenerateContentResponse | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with self.audit.track(
                    provider="gemini",
                    model=self.model,
                    purpose="transcription",
                    prompt=prompt_record,
                    encounter_id=encounter_id,
                ) as call:
                    try:
                        response = self.client.models.generate_content(
                            model=self.model, contents=contents, config=config
                        )
                    except errors.APIError as exc:
                        if exc.code == 429:
                            call.status = "rate_limited"
                        raise
                    call.usage = _usage(response)
                break
            except errors.APIError as exc:
                if exc.code not in RETRYABLE or attempt == self.max_retries:
                    raise TranscriptionError(f"Gemini request failed ({exc.code})") from exc
                delay = _retry_delay(exc, attempt)
                log.warning("Gemini returned %s; retrying in %.0f s", exc.code, delay)
                self.sleep(delay)

        assert response is not None
        return Transcription(
            segments=parse_transcript(response), provider=self.name, model=self.model
        )


def parse_transcript(response: types.GenerateContentResponse) -> list[TranscriptSegment]:
    parsed = response.parsed
    try:
        if isinstance(parsed, GeminiTranscript):
            transcript = parsed
        else:
            transcript = GeminiTranscript.model_validate_json(response.text or "")
    except ValidationError as exc:
        raise TranscriptionError("Gemini output does not match the transcript schema") from exc
    if not transcript.segments:
        raise TranscriptionError("Gemini returned an empty transcript")
    return normalize_segments(
        TranscriptSegment(
            id=s.id,
            speaker=Speaker(s.speaker),
            start=max(0.0, s.start),
            end=max(0.0, s.end),
            text=s.text,
        )
        for s in transcript.segments
    )
