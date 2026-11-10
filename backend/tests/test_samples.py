"""The bundled samples are complete and internally consistent."""

from __future__ import annotations

import hashlib
import itertools

import pytest

from scribe.config import REPO_ROOT
from scribe.samples import Sample, load_samples

SAMPLES = load_samples(REPO_ROOT / "samples")


def test_three_samples_are_bundled() -> None:
    assert [s.slug for s in SAMPLES] == ["ankle-sprain", "diabetes-follow-up", "sore-throat"]


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s.slug)
def test_sample_is_marked_synthetic(sample: Sample) -> None:
    assert sample.script.synthetic is True
    assert "Synthetic" in sample.script.notice


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s.slug)
def test_duration_is_two_to_four_minutes(sample: Sample) -> None:
    assert 120 <= sample.transcript.duration_seconds <= 240


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s.slug)
def test_transcript_matches_script(sample: Sample) -> None:
    segments = sample.transcript.segments
    assert [(s.speaker, s.text) for s in segments] == [
        (t.speaker, t.text) for t in sample.script.turns
    ]
    assert [s.id for s in segments] == [f"s{i}" for i in range(1, len(segments) + 1)]
    for previous, current in itertools.pairwise(segments):
        assert previous.start < previous.end <= current.start


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s.slug)
def test_audio_hash_matches_transcript(sample: Sample) -> None:
    digest = hashlib.sha256(sample.audio_path.read_bytes()).hexdigest()
    assert digest == sample.transcript.audio_sha256


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s.slug)
def test_known_pii_appears_in_the_script(sample: Sample) -> None:
    text = " ".join(t.text for t in sample.script.turns)
    for item in sample.script.pii:
        assert item.value in text, item.value
    assert {p.type for p in sample.script.pii} == {
        "PERSON",
        "DATE_OF_BIRTH",
        "PHONE_NUMBER",
        "ADDRESS",
    }


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s.slug)
def test_reference_facts_cover_every_section(sample: Sample) -> None:
    assert sample.facts.slug == sample.slug
    assert {f.section for f in sample.facts.facts} == {
        "subjective",
        "objective",
        "assessment",
        "plan",
    }
    assert len({f.id for f in sample.facts.facts}) == len(sample.facts.facts)
