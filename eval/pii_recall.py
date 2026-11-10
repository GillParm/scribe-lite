"""Redaction recall on the bundled samples and the held-out set.

An occurrence counts as caught only if every letter and digit of it was redacted.
Writes eval/results/pii_recall.json and pii_recall.md.

Run from backend/:  uv run python ../eval/pii_recall.py
"""

from __future__ import annotations

import json
import sys
from typing import Any

from scribe.config import REPO_ROOT, get_settings
from scribe.models import Speaker
from scribe.redaction import Redactor
from scribe.redaction.recall import RecallReport, measure_recall
from scribe.samples import KnownPii, load_samples
from scribe.schemas import TranscriptSegment

OUT = REPO_ROOT / "eval" / "results"
TYPES = ["PERSON", "DATE_OF_BIRTH", "PHONE_NUMBER", "ADDRESS"]


def _row(name: str, report: RecallReport) -> dict[str, Any]:
    return {
        "set": name,
        "occurrences": sum(report.total.values()),
        "caught": sum(report.covered.values()),
        "recall": round(report.recall, 4),
        "by_type": {
            t: {"total": report.total.get(t, 0), "caught": report.covered.get(t, 0)} for t in TYPES
        },
        "missed": [f"{m.entity_type}: {m.value}" for m in report.missed],
        "over_redacted": report.extra,
    }


def main() -> int:
    settings = get_settings()
    redactor = Redactor(settings.spacy_model)
    rows = []

    samples_total = RecallReport()
    for sample in load_samples(settings.samples_dir):
        report = measure_recall(redactor, sample.transcript.segments, sample.script.pii)
        rows.append(_row(f"sample: {sample.slug}", report))
        samples_total = samples_total.merge(report)
    rows.append(_row("samples (all)", samples_total))

    holdout = json.loads((REPO_ROOT / "eval" / "pii_holdout.json").read_text())
    holdout_total = RecallReport()
    for case in holdout["cases"]:
        segments = [
            TranscriptSegment(id=f"s{i}", speaker=Speaker.PATIENT, start=0, end=0, text=t)
            for i, t in enumerate(case["turns"], start=1)
        ]
        known = [KnownPii.model_validate(p) for p in case["pii"]]
        holdout_total = holdout_total.merge(measure_recall(redactor, segments, known))
    rows.append(_row("held-out set", holdout_total))

    result = {"spacy_model": settings.spacy_model, "rows": rows}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "pii_recall.json").write_text(json.dumps(result, indent=2) + "\n")
    (OUT / "pii_recall.md").write_text(render(result))
    print(render(result))
    return 0


def render(result: dict[str, Any]) -> str:
    lines = [
        f"spaCy model: `{result['spacy_model']}`",
        "",
        "| Set | Caught / occurrences | Recall | Person | DOB | Phone | Address |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in result["rows"]:
        cells = [f"{row['by_type'][t]['caught']}/{row['by_type'][t]['total']}" for t in TYPES]
        lines.append(
            f"| {row['set']} | {row['caught']}/{row['occurrences']} | {row['recall']:.1%} | "
            + " | ".join(cells)
            + " |"
        )
    lines.append("")
    for row in result["rows"]:
        if row["missed"] or row["over_redacted"]:
            lines.append(
                f"- {row['set']}: missed {row['missed'] or 'none'}; "
                f"over-redacted {row['over_redacted'] or 'none'}"
            )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
