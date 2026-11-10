"""Measure redaction recall against a known list of personal details."""

from __future__ import annotations

from dataclasses import dataclass, field

from scribe.redaction.redactor import Redactor
from scribe.samples import KnownPii
from scribe.schemas import TranscriptSegment


@dataclass(frozen=True)
class Occurrence:
    entity_type: str
    value: str
    start: int
    end: int


@dataclass
class RecallReport:
    total: dict[str, int] = field(default_factory=dict)
    covered: dict[str, int] = field(default_factory=dict)
    missed: list[Occurrence] = field(default_factory=list)
    # Redacted text that is not on the known list: over-redaction, not a privacy failure.
    extra: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        total = sum(self.total.values())
        return sum(self.covered.values()) / total if total else 1.0

    def recall_for(self, entity_type: str) -> float:
        total = self.total.get(entity_type, 0)
        return self.covered.get(entity_type, 0) / total if total else 1.0

    def merge(self, other: RecallReport) -> RecallReport:
        merged = RecallReport(
            total=dict(self.total),
            covered=dict(self.covered),
            missed=self.missed + other.missed,
            extra=self.extra + other.extra,
        )
        for key, value in other.total.items():
            merged.total[key] = merged.total.get(key, 0) + value
        for key, value in other.covered.items():
            merged.covered[key] = merged.covered.get(key, 0) + value
        return merged


def known_occurrences(text: str, known: list[KnownPii]) -> list[Occurrence]:
    """Every place a known value appears, longest values first, without overlaps."""
    claimed: list[Occurrence] = []
    for item in sorted(known, key=lambda k: -len(k.value)):
        start = text.find(item.value)
        while start != -1:
            end = start + len(item.value)
            if all(end <= o.start or start >= o.end for o in claimed):
                claimed.append(Occurrence(item.type, item.value, start, end))
            start = text.find(item.value, start + 1)
    return sorted(claimed, key=lambda o: o.start)


def measure_recall(
    redactor: Redactor, segments: list[TranscriptSegment], known: list[KnownPii]
) -> RecallReport:
    text = "\n".join(s.text for s in segments)
    spans = redactor.find(text)
    redacted = {i for span in spans for i in range(span.start, span.end)}
    occurrences = known_occurrences(text, known)

    report = RecallReport()
    for occurrence in occurrences:
        report.total[occurrence.entity_type] = report.total.get(occurrence.entity_type, 0) + 1
        needed = {i for i in range(occurrence.start, occurrence.end) if text[i].isalnum()}
        if needed <= redacted:
            report.covered[occurrence.entity_type] = (
                report.covered.get(occurrence.entity_type, 0) + 1
            )
        else:
            report.missed.append(occurrence)
    for span in spans:
        if all(span.end <= o.start or span.start >= o.end for o in occurrences):
            report.extra.append(f"{span.entity_type}: {span.text}")
    return report
