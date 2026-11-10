from __future__ import annotations

import pytest

from scribe.models import Speaker
from scribe.schemas import TranscriptSegment
from scribe.transcript_metrics import normalise_words, score_transcript, word_error_rate


def seg(i: int, speaker: str, start: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(
        id=f"s{i}", speaker=Speaker(speaker), start=start, end=start + 1, text=text
    )


REFERENCE = [
    seg(1, "doctor", 0.0, "Have you had a fever?"),
    seg(2, "patient", 2.0, "Yes, for three days."),
    seg(3, "doctor", 5.0, "Any cough?"),
]


def test_normalise_folds_case_punctuation_and_numbers() -> None:
    assert normalise_words("Three days, 416-555-0182!") == ["3", "days", "416", "555", "0182"]


def test_word_error_rate() -> None:
    assert word_error_rate(["a", "b", "c"], ["a", "b", "c"]) == 0
    assert word_error_rate(["a", "b", "c"], ["a", "x", "c", "d"]) == pytest.approx(2 / 3)


def test_perfect_transcript() -> None:
    score = score_transcript(REFERENCE, REFERENCE)
    assert score.speaker_accuracy == 1.0
    assert score.word_error_rate == 0.0
    assert score.mixed_segments == 0
    assert score.median_start_error_s == 0.0


def test_swapped_label_lowers_speaker_accuracy_only() -> None:
    hypothesis = [REFERENCE[0], seg(2, "doctor", 2.0, "Yes, for 3 days."), REFERENCE[2]]
    score = score_transcript(REFERENCE, hypothesis)
    assert score.word_error_rate == 0.0
    assert score.speaker_accuracy == pytest.approx(7 / 11)  # 4 of 11 words relabelled


def test_merged_turns_count_as_mixed_segments() -> None:
    hypothesis = [
        seg(1, "doctor", 0.3, "Have you had a fever? Yes, for three days."),
        seg(2, "doctor", 5.5, "Any cough?"),
    ]
    score = score_transcript(REFERENCE, hypothesis)
    assert score.mixed_segments == 1
    assert score.median_start_error_s == pytest.approx(0.4)
