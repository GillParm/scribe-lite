"""Custom Presidio recognizers for the personal details a clinic conversation carries.

Presidio's spaCy recognizer finds most names and its phone recognizer finds most
numbers. These add what it misses in conversation:

- dates of birth, told apart from ordinary dates by a nearby cue ("date of birth",
  "born"), which may sit in the previous speaker's turn;
- street addresses with unit, city and postal code;
- names that follow a title ("Doctor Raman"), which spaCy often leaves out;
- North American phone numbers, written or read out digit by digit.
"""

from __future__ import annotations

import re
from typing import Any

from presidio_analyzer import EntityRecognizer, Pattern, PatternRecognizer, RecognizerResult

_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
)
_DAY = r"\d{1,2}(?:st|nd|rd|th)?"
_YEAR = r"(?:19|20)\d{2}"
_DATE = re.compile(
    rf"\b(?:{_MONTH}\s+{_DAY},?\s+{_YEAR}"  # March 4th, 1988
    rf"|{_DAY}\s+(?:of\s+)?{_MONTH},?\s+{_YEAR}"  # 4th of March, 1988
    r"|\d{1,2}[/.-]\d{1,2}[/.-](?:\d{4}|\d{2})"  # 03/04/1988
    rf"|{_YEAR}-\d{{2}}-\d{{2}})\b"  # 1988-03-04
)
_DOB_CUE = re.compile(r"\b(?:date of birth|birth ?date|birthday|born|d\.?o\.?b\.?)\b", re.I)


class DateOfBirthRecognizer(EntityRecognizer):
    """A date counts as a date of birth only when a cue appears shortly before it."""

    ENTITY = "DATE_OF_BIRTH"

    def __init__(self, lookback_chars: int = 160) -> None:
        self.lookback_chars = lookback_chars
        super().__init__(supported_entities=[self.ENTITY], name="DateOfBirthRecognizer")

    def load(self) -> None:
        pass

    def analyze(self, text: str, entities: list[str], nlp_artifacts: Any = None) -> list[Any]:
        results = []
        for match in _DATE.finditer(text):
            window = text[max(0, match.start() - self.lookback_chars) : match.start()]
            if _DOB_CUE.search(window):
                results.append(RecognizerResult(self.ENTITY, match.start(), match.end(), 0.9))
        return results


_STREET_TYPES = (
    r"(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Court|Ct|Crescent|"
    r"Cres|Place|Pl|Way|Terrace|Circle|Cir|Trail|Parkway|Pkwy|Highway|Hwy|Square|Sq|Row|"
    r"Gate|Grove|Close|Mews|Heights)"
)
_PROVINCES = r"(?:ON|QC|BC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU|[A-Z]{2}|Ontario|Quebec|Alberta)"
_ADDRESS = re.compile(
    r"\b\d{1,6}\s+(?:[A-Z][a-zA-Z'-]+\s+){1,4}"
    + _STREET_TYPES
    + r"\b\.?(?:\s+(?:East|West|North|South|E|W|N|S)\b)?"
    r"(?:,?\s+(?i:apartment|apt|unit|suite|ste|#)\.?\s*[A-Za-z0-9-]+)?"
    r"(?:,\s*(?!(?i:and|but|or|is|in)\b)[A-Z][a-zA-Z'.-]+(?:\s+[A-Z][a-zA-Z'.-]+){0,2})?"
    r"(?:,?\s*" + _PROVINCES + r"\b)?"
    r"(?:,?\s*[A-Z]\d[A-Z]\s?\d[A-Z]\d\b|,?\s+\d{5}(?:-\d{4})?\b)?"
)


class StreetAddressRecognizer(PatternRecognizer):
    def __init__(self) -> None:
        super().__init__(
            supported_entity="ADDRESS",
            name="StreetAddressRecognizer",
            patterns=[Pattern("street address", _ADDRESS.pattern, 0.85)],
        )


_TITLE = re.compile(
    r"\b(?:Dr|Doctor|Mr|Mrs|Ms|Miss|Mx|Nurse|Coach)\.?\s+"
    r"(?P<name>[A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+)?)"
)


class TitledNameRecognizer(EntityRecognizer):
    """The name after a title, without the title, so 'Doctor' stays readable."""

    def __init__(self) -> None:
        super().__init__(supported_entities=["PERSON"], name="TitledNameRecognizer")

    def load(self) -> None:
        pass

    def analyze(self, text: str, entities: list[str], nlp_artifacts: Any = None) -> list[Any]:
        return [
            RecognizerResult("PERSON", m.start("name"), m.end("name"), 0.85)
            for m in _TITLE.finditer(text)
        ]


_DIGIT_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"


class NorthAmericanPhoneRecognizer(PatternRecognizer):
    """10-digit numbers in written form, and numbers read out as words."""

    def __init__(self) -> None:
        super().__init__(
            supported_entity="PHONE_NUMBER",
            name="NorthAmericanPhoneRecognizer",
            patterns=[
                Pattern(
                    "nanp digits",
                    r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}(?!\d)",
                    0.7,
                ),
                Pattern(
                    "spoken digits",
                    rf"(?i)\b(?:{_DIGIT_WORD}[\s,-]+){{6,}}{_DIGIT_WORD}\b",
                    0.6,
                ),
            ],
        )


_GREETING = re.compile(
    r"\b(?:Hi|Hello|Hey|Thanks|Thank you|Bye|Goodbye|Morning|"
    r"Good (?:morning|afternoon|evening)),?\s+"
    r"(?P<name>[A-Z][a-z'-]+)(?=\s*[,.!?])"
)
_NOT_GREETED_NAMES = {"There", "Everyone", "Again", "Doctor", "Nurse", "So", "Okay"}


class GreetingNameRecognizer(EntityRecognizer):
    """A capitalised word right after a greeting: 'Hi Priya,' or 'Thanks, Maria.'"""

    def __init__(self) -> None:
        super().__init__(supported_entities=["PERSON"], name="GreetingNameRecognizer")

    def load(self) -> None:
        pass

    def analyze(self, text: str, entities: list[str], nlp_artifacts: Any = None) -> list[Any]:
        return [
            RecognizerResult("PERSON", m.start("name"), m.end("name"), 0.7)
            for m in _GREETING.finditer(text)
            if m.group("name") not in _NOT_GREETED_NAMES
        ]
