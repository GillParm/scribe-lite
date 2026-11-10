"""Replace personal details with stable placeholders before text reaches a model.

The whole transcript is analysed as one document (turns joined by newlines), so a date
of birth given in the patient's turn is linked to the doctor's question in the turn
before it. Each distinct value gets one placeholder for the whole encounter:

    "Hi Maria, is 416-555-0182 still your number?"
    -> "Hi [PERSON_1], is [PHONE_NUMBER_1] still your number?"

A shorter form of a name ("Maria", "Ms. Alvarez") shares the placeholder of the full
name, so the model can tell it is the same person. The mapping (the `Vault`) stays in
the local database; `Vault.reveal` restores real values in model output for display.
Each segment also records exact spans, so its original text is always recoverable.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import (
    EmailRecognizer,
    PhoneRecognizer,
    SpacyRecognizer,
)

from scribe.models import Speaker
from scribe.redaction.recognizers import (
    DateOfBirthRecognizer,
    NorthAmericanPhoneRecognizer,
    StreetAddressRecognizer,
    TitledNameRecognizer,
)
from scribe.schemas import TranscriptSegment

ENTITY_TYPES = ["PERSON", "DATE_OF_BIRTH", "PHONE_NUMBER", "ADDRESS", "EMAIL_ADDRESS"]
# When spans overlap, the longer one wins; on a tie, the more specific type.
_PRIORITY = {"DATE_OF_BIRTH": 0, "ADDRESS": 1, "PHONE_NUMBER": 2, "EMAIL_ADDRESS": 3, "PERSON": 4}
MIN_SCORE = 0.5
_SPACY_LABELS_UNUSED = [
    "CARDINAL",
    "DATE",
    "EVENT",
    "FAC",
    "GPE",
    "LANGUAGE",
    "LAW",
    "LOC",
    "MONEY",
    "NORP",
    "ORDINAL",
    "ORG",
    "PERCENT",
    "PRODUCT",
    "QUANTITY",
    "TIME",
    "WORK_OF_ART",
]
PLACEHOLDER = re.compile(r"\[(?P<type>[A-Z_]+)_(?P<n>\d+)\]")
_LEADING_TITLE = re.compile(r"(?:Dr|Doctor|Mr|Mrs|Ms|Miss|Mx|Nurse|Coach)\.?\s+")

# Capitalised words spaCy sometimes tags as names in clinical talk. Never propagated or
# redacted on their own.
_NOT_NAMES = {
    "Doctor",
    "Dr",
    "Mr",
    "Mrs",
    "Ms",
    "Miss",
    "Nurse",
    "Coach",
    "Okay",
    "Yes",
    "No",
    "Hi",
    "Thanks",
}


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    entity_type: str
    text: str
    score: float


@dataclass(frozen=True)
class RedactionSpan:
    """Where a placeholder sits in the redacted text, and what it replaced."""

    start: int
    end: int
    placeholder: str
    entity_type: str
    original: str

    def public(self) -> dict[str, int | str]:
        """The span without the original value (safe to send to a browser view)."""
        return {
            "start": self.start,
            "end": self.end,
            "placeholder": self.placeholder,
            "entity_type": self.entity_type,
        }


@dataclass
class VaultEntry:
    placeholder: str
    entity_type: str
    value: str
    variants: list[str] = field(default_factory=list)
    occurrences: int = 0


@dataclass
class Vault:
    entries: dict[str, VaultEntry] = field(default_factory=dict)

    def reveal(self, text: str) -> str:
        """Put real values back in place of placeholders. Unknown placeholders stay."""

        def swap(match: re.Match[str]) -> str:
            entry = self.entries.get(match.group(0))
            return entry.value if entry else match.group(0)

        return PLACEHOLDER.sub(swap, text)


@dataclass(frozen=True)
class RedactedSegment:
    segment: TranscriptSegment
    redacted_text: str
    spans: list[RedactionSpan]

    def restore(self) -> str:
        """Rebuild the original text from the redacted text and its spans."""
        text = self.redacted_text
        for span in sorted(self.spans, key=lambda s: s.start, reverse=True):
            text = text[: span.start] + span.original + text[span.end :]
        return text


@dataclass(frozen=True)
class RedactedTranscript:
    segments: list[RedactedSegment]
    vault: Vault


def _normalise(entity_type: str, value: str) -> str:
    if entity_type == "PHONE_NUMBER":
        return re.sub(r"\D", "", value)[-10:] or value.lower()
    return re.sub(r"[^a-z0-9]", "", value.lower())


class Redactor:
    def __init__(self, spacy_model: str = "en_core_web_md") -> None:
        nlp_engine = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": spacy_model}],
                # Only PERSON is used from spaCy; drop the other labels quietly.
                "ner_model_configuration": {"labels_to_ignore": _SPACY_LABELS_UNUSED},
            }
        ).create_engine()
        registry = RecognizerRegistry(supported_languages=["en"])
        for recognizer in (
            SpacyRecognizer(supported_entities=["PERSON"]),
            PhoneRecognizer(),
            EmailRecognizer(),
            DateOfBirthRecognizer(),
            StreetAddressRecognizer(),
            TitledNameRecognizer(),
            NorthAmericanPhoneRecognizer(),
        ):
            registry.add_recognizer(recognizer)
        self.analyzer = AnalyzerEngine(
            nlp_engine=nlp_engine, registry=registry, supported_languages=["en"]
        )

    # -- detection -----------------------------------------------------------------

    def find(self, text: str) -> list[Span]:
        """Personal details in `text`, non-overlapping, in order."""
        results = self.analyzer.analyze(
            text=text, language="en", entities=ENTITY_TYPES, score_threshold=MIN_SCORE
        )
        spans = [
            Span(r.start, r.end, r.entity_type, text[r.start : r.end], r.score) for r in results
        ]
        spans = [s for s in (_trim(s) for s in spans) if s is not None]
        spans = _resolve_overlaps(spans)
        return _resolve_overlaps(spans + _propagate_names(text, spans))

    # -- redaction -----------------------------------------------------------------

    def redact_segments(self, segments: list[TranscriptSegment]) -> RedactedTranscript:
        text, offsets = _join(segments)
        spans = self.find(text)
        placeholders = _assign_placeholders(spans)
        vault = Vault()
        for span in spans:
            placeholder = placeholders[(span.entity_type, span.text)]
            entry = vault.entries.setdefault(
                placeholder, VaultEntry(placeholder, span.entity_type, span.text)
            )
            entry.occurrences += 1
            if span.text not in entry.variants:
                entry.variants.append(span.text)
            if len(span.text) > len(entry.value):
                entry.value = span.text

        redacted = []
        for segment, (seg_start, seg_end) in zip(segments, offsets, strict=True):
            inside = [s for s in spans if s.start >= seg_start and s.end <= seg_end]
            redacted.append(_redact_one(segment, seg_start, inside, placeholders))
        return RedactedTranscript(segments=redacted, vault=vault)

    def redact_text(self, text: str) -> tuple[str, Vault]:
        """Redact a single string (convenience for tests and ad-hoc use)."""
        segment = TranscriptSegment(id="s1", speaker=Speaker.PATIENT, start=0, end=0, text=text)
        result = self.redact_segments([segment])
        return result.segments[0].redacted_text, result.vault


def _join(segments: list[TranscriptSegment]) -> tuple[str, list[tuple[int, int]]]:
    parts, offsets, cursor = [], [], 0
    for segment in segments:
        parts.append(segment.text)
        offsets.append((cursor, cursor + len(segment.text)))
        cursor += len(segment.text) + 1
    return "\n".join(parts), offsets


def _trim(span: Span) -> Span | None:
    """Drop surrounding punctuation and possessive endings; discard non-names."""
    text = span.text
    start, end = span.start, span.end
    keep_leading = "(+" if span.entity_type == "PHONE_NUMBER" else ""
    while text and not text[0].isalnum() and text[0] not in keep_leading:
        text, start = text[1:], start + 1
    while text and not text[-1].isalnum():
        text, end = text[:-1], end - 1
    if span.entity_type == "PERSON":
        # Keep titles readable: "Doctor [PERSON_2]", not "[PERSON_2]".
        title = _LEADING_TITLE.match(text)
        if title:
            text, start = text[title.end() :], start + title.end()
        for suffix in ("'s", "\u2019s"):
            if text.endswith(suffix):
                text, end = text[: -len(suffix)], end - len(suffix)
        if not text or text in _NOT_NAMES or not text[0].isupper():
            return None
    if not text:
        return None
    return Span(start, end, span.entity_type, text, span.score)


def _resolve_overlaps(spans: Iterable[Span]) -> list[Span]:
    ranked = sorted(
        spans, key=lambda s: (-(s.end - s.start), _PRIORITY.get(s.entity_type, 9), -s.score)
    )
    kept: list[Span] = []
    for span in ranked:
        if all(span.end <= k.start or span.start >= k.end for k in kept):
            kept.append(span)
    return sorted(kept, key=lambda s: s.start)


def _propagate_names(text: str, spans: list[Span]) -> list[Span]:
    """Once a name is found anywhere, redact every other mention of it (and its parts)."""
    tokens = {
        token
        for span in spans
        if span.entity_type == "PERSON"
        for token in re.findall(r"[A-Z][a-zA-Z'-]+", span.text)
        if len(token) > 2 and token not in _NOT_NAMES
    }
    found = []
    for token in tokens:
        for match in re.finditer(rf"\b{re.escape(token)}\b", text):
            found.append(Span(match.start(), match.end(), "PERSON", token, 0.8))
    return found


def _assign_placeholders(spans: list[Span]) -> dict[tuple[str, str], str]:
    """Map each (type, surface text) to a placeholder, numbered by first appearance.

    Names: a shorter name whose words all appear in a longer name joins that name's
    placeholder ("Maria" -> same as "Maria Alvarez").
    """
    keys: dict[tuple[str, str], str] = {}  # (type, surface) -> group key
    person_groups: list[tuple[str, set[str]]] = []  # (group key, words)
    names = sorted({s.text for s in spans if s.entity_type == "PERSON"}, key=lambda n: (-len(n), n))
    for name in names:
        words = set(re.findall(r"[A-Za-z'-]+", name.lower()))
        group = next((key for key, group_words in person_groups if words <= group_words), None)
        if group is None:
            group = _normalise("PERSON", name)
            person_groups.append((group, words))
        keys[("PERSON", name)] = group
    for span in spans:
        if span.entity_type != "PERSON":
            keys[(span.entity_type, span.text)] = _normalise(span.entity_type, span.text)

    numbers: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}
    placeholders: dict[tuple[str, str], str] = {}
    for span in spans:  # in text order, so numbering follows first appearance
        group_key = (span.entity_type, keys[(span.entity_type, span.text)])
        if group_key not in numbers:
            counters[span.entity_type] = counters.get(span.entity_type, 0) + 1
            numbers[group_key] = f"[{span.entity_type}_{counters[span.entity_type]}]"
        placeholders[(span.entity_type, span.text)] = numbers[group_key]
    return placeholders


def _redact_one(
    segment: TranscriptSegment,
    seg_start: int,
    spans: list[Span],
    placeholders: dict[tuple[str, str], str],
) -> RedactedSegment:
    out: list[str] = []
    redaction_spans: list[RedactionSpan] = []
    cursor = 0  # in the original segment text
    length = 0  # of the redacted text so far
    for span in spans:
        start, end = span.start - seg_start, span.end - seg_start
        before = segment.text[cursor:start]
        out.append(before)
        length += len(before)
        placeholder = placeholders[(span.entity_type, span.text)]
        redaction_spans.append(
            RedactionSpan(
                length, length + len(placeholder), placeholder, span.entity_type, span.text
            )
        )
        out.append(placeholder)
        length += len(placeholder)
        cursor = end
    out.append(segment.text[cursor:])
    return RedactedSegment(segment=segment, redacted_text="".join(out), spans=redaction_spans)
