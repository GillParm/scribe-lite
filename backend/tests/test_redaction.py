from __future__ import annotations

import pytest

from scribe.config import REPO_ROOT
from scribe.models import Speaker
from scribe.redaction import Redactor, Vault, VaultEntry
from scribe.redaction.recall import measure_recall
from scribe.samples import load_samples
from scribe.schemas import TranscriptSegment

SAMPLES = load_samples(REPO_ROOT / "samples")


@pytest.fixture(scope="module")
def redactor() -> Redactor:
    return Redactor("en_core_web_md")


def turns(*texts: str) -> list[TranscriptSegment]:
    speakers = [Speaker.DOCTOR, Speaker.PATIENT]
    return [
        TranscriptSegment(id=f"s{i}", speaker=speakers[i % 2], start=i, end=i + 1, text=t)
        for i, t in enumerate(texts)
    ]


def test_names_get_stable_placeholders(redactor: Redactor) -> None:
    result = redactor.redact_segments(
        turns(
            "Can you confirm your name?",
            "It's Maria Alvarez.",
            "Thanks, Maria. Is Jenna your roommate?",
        )
    )
    texts = [s.redacted_text for s in result.segments]
    assert texts[1] == "It's [PERSON_1]."
    assert texts[2] == "Thanks, [PERSON_1]. Is [PERSON_2] your roommate?"
    assert result.vault.entries["[PERSON_1]"].value == "Maria Alvarez"
    assert result.vault.entries["[PERSON_1]"].variants == ["Maria Alvarez", "Maria"]
    assert result.vault.entries["[PERSON_1]"].occurrences == 2


def test_title_stays_readable(redactor: Redactor) -> None:
    text, vault = redactor.redact_text("Good morning, I'm Doctor Raman.")
    assert text == "Good morning, I'm Doctor [PERSON_1]."
    assert vault.entries["[PERSON_1]"].value == "Raman"


def test_possessive_is_kept_outside_the_placeholder(redactor: Redactor) -> None:
    text, _ = redactor.redact_text("I landed on my teammate Marcus's foot.")
    assert text == "I landed on my teammate [PERSON_1]'s foot."


def test_date_of_birth_needs_a_cue(redactor: Redactor) -> None:
    text, _ = redactor.redact_text("My date of birth is March 4th, 1988.")
    assert text == "My date of birth is [DATE_OF_BIRTH_1]."
    clinical, _ = redactor.redact_text("The scan on March 4th, 2026 was normal.")
    assert clinical == "The scan on March 4th, 2026 was normal."


def test_date_of_birth_cue_can_be_in_the_previous_turn(redactor: Redactor) -> None:
    result = redactor.redact_segments(
        turns("Can you give me your date of birth?", "November 22nd, 2001.")
    )
    assert result.segments[1].redacted_text == "[DATE_OF_BIRTH_1]."


@pytest.mark.parametrize(
    "number", ["416-555-0182", "(905) 555-0147", "647.555.0119", "416 555 0198"]
)
def test_phone_numbers(redactor: Redactor, number: str) -> None:
    text, vault = redactor.redact_text(f"Call me at {number} after five.")
    assert text == "Call me at [PHONE_NUMBER_1] after five."
    assert vault.entries["[PHONE_NUMBER_1]"].value == number


def test_same_number_in_two_formats_shares_a_placeholder(redactor: Redactor) -> None:
    text, _ = redactor.redact_text("It's 416-555-0182, or (416) 555-0182 on my old forms.")
    assert text.count("[PHONE_NUMBER_1]") == 2


def test_spoken_phone_number(redactor: Redactor) -> None:
    text, _ = redactor.redact_text("It's four one six, five five five, zero one eight two.")
    assert text == "It's [PHONE_NUMBER_1]."


@pytest.mark.parametrize(
    "address",
    [
        "42 Maple Crescent, Apartment 3, Toronto",
        "1187 Birchwood Drive, Mississauga, L5G 2K9",
        "305 Harbourview Lane, unit 12, Toronto",
    ],
)
def test_street_addresses(redactor: Redactor, address: str) -> None:
    text, vault = redactor.redact_text(f"I'm still at {address}.")
    assert text == "I'm still at [ADDRESS_1]."
    assert vault.entries["[ADDRESS_1]"].value == address


def test_clinical_content_is_left_alone(redactor: Redactor) -> None:
    sentence = (
        "Temperature 38.3, heart rate 96, blood pressure 118 over 76. Azithromycin "
        "500 milligrams once a day for five days. Repeat the A1c in three months."
    )
    text, vault = redactor.redact_text(sentence)
    assert text == sentence
    assert vault.entries == {}


def test_segments_restore_exactly(redactor: Redactor) -> None:
    for sample in SAMPLES:
        result = redactor.redact_segments(sample.transcript.segments)
        for redacted in result.segments:
            assert redacted.restore() == redacted.segment.text
            for span in redacted.spans:
                assert redacted.redacted_text[span.start : span.end] == span.placeholder


def test_reveal_restores_values_in_model_output() -> None:
    vault = Vault(
        {
            "[PERSON_1]": VaultEntry("[PERSON_1]", "PERSON", "Maria Alvarez", ["Maria"]),
            "[PHONE_NUMBER_1]": VaultEntry("[PHONE_NUMBER_1]", "PHONE_NUMBER", "416-555-0182"),
        }
    )
    note = "[PERSON_1] can be reached at [PHONE_NUMBER_1]; see [PERSON_9]."
    assert vault.reveal(note) == ("Maria Alvarez can be reached at 416-555-0182; see [PERSON_9].")


def test_nothing_identifying_survives_in_sample_redactions(redactor: Redactor) -> None:
    for sample in SAMPLES:
        redacted = " ".join(
            s.redacted_text for s in redactor.redact_segments(sample.transcript.segments).segments
        )
        for item in sample.script.pii:
            assert item.value not in redacted, (sample.slug, item.value)


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s.slug)
def test_recall_on_samples(redactor: Redactor, sample: object) -> None:
    from scribe.samples import Sample

    assert isinstance(sample, Sample)
    report = measure_recall(redactor, sample.transcript.segments, sample.script.pii)
    assert report.recall == 1.0, report.missed
    assert report.extra == []
