"""Score a hypothesis transcript against the scripted reference.

- Word error rate over normalised words (case, punctuation and number words folded).
- Speaker accuracy: of the words both transcripts share (aligned with difflib), the
  share whose speaker label matches the reference. This isolates labelling from
  recognition errors.
- Mixed segments: hypothesis segments that contain words from both speakers' turns,
  i.e. a missed speaker change.
- Turn start error: for reference turns that open a hypothesis segment, the absolute
  difference between the two start times.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from difflib import SequenceMatcher

from scribe.schemas import TranscriptSegment

_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "fifteen": "15",
    "twenty": "20",
}
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def normalise_words(text: str) -> list[str]:
    words = _WORD.findall(text.lower().replace("-", " "))
    return [_NUMBER_WORDS.get(w, w) for w in words]


@dataclass(frozen=True)
class _Word:
    text: str
    speaker: str
    segment: int


def _words(segments: list[TranscriptSegment]) -> list[_Word]:
    return [
        _Word(word, segment.speaker.value, index)
        for index, segment in enumerate(segments)
        for word in normalise_words(segment.text)
    ]


def word_error_rate(reference: list[str], hypothesis: list[str]) -> float:
    """Levenshtein distance over words divided by the reference length."""
    if not reference:
        return 0.0 if not hypothesis else 1.0
    previous = list(range(len(hypothesis) + 1))
    for i, ref_word in enumerate(reference, start=1):
        current = [i] + [0] * len(hypothesis)
        for j, hyp_word in enumerate(hypothesis, start=1):
            current[j] = min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (ref_word != hyp_word),
            )
        previous = current
    return previous[-1] / len(reference)


@dataclass(frozen=True)
class TranscriptScore:
    reference_segments: int
    hypothesis_segments: int
    word_error_rate: float
    aligned_words: int
    speaker_accuracy: float
    mixed_segments: int
    median_start_error_s: float | None

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "reference_segments": self.reference_segments,
            "hypothesis_segments": self.hypothesis_segments,
            "word_error_rate": round(self.word_error_rate, 4),
            "aligned_words": self.aligned_words,
            "speaker_accuracy": round(self.speaker_accuracy, 4),
            "mixed_segments": self.mixed_segments,
            "median_start_error_s": (
                None if self.median_start_error_s is None else round(self.median_start_error_s, 2)
            ),
        }


def score_transcript(
    reference: list[TranscriptSegment], hypothesis: list[TranscriptSegment]
) -> TranscriptScore:
    ref_words = _words(reference)
    hyp_words = _words(hypothesis)
    matcher = SequenceMatcher(
        a=[w.text for w in ref_words], b=[w.text for w in hyp_words], autojunk=False
    )

    aligned = 0
    same_speaker = 0
    # hypothesis segment -> reference speakers its aligned words came from
    speakers_in_segment: dict[int, set[str]] = {}
    # reference segment -> first aligned hypothesis word index
    first_hyp_word: dict[int, int] = {}
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            ref_word = ref_words[block.a + offset]
            hyp_index = block.b + offset
            hyp_word = hyp_words[hyp_index]
            aligned += 1
            same_speaker += ref_word.speaker == hyp_word.speaker
            speakers_in_segment.setdefault(hyp_word.segment, set()).add(ref_word.speaker)
            first_hyp_word.setdefault(ref_word.segment, hyp_index)

    segment_first_word: dict[int, int] = {}
    for index, word in enumerate(hyp_words):
        segment_first_word.setdefault(word.segment, index)
    start_errors = []
    for ref_index, hyp_index in first_hyp_word.items():
        hyp_segment = hyp_words[hyp_index].segment
        # Only turns that open a hypothesis segment have a comparable start time.
        if segment_first_word.get(hyp_segment) == hyp_index:
            start_errors.append(abs(hypothesis[hyp_segment].start - reference[ref_index].start))

    return TranscriptScore(
        reference_segments=len(reference),
        hypothesis_segments=len(hypothesis),
        word_error_rate=word_error_rate([w.text for w in ref_words], [w.text for w in hyp_words]),
        aligned_words=aligned,
        speaker_accuracy=same_speaker / aligned if aligned else 0.0,
        mixed_segments=sum(1 for s in speakers_in_segment.values() if len(s) > 1),
        median_start_error_s=statistics.median(start_errors) if start_errors else None,
    )
