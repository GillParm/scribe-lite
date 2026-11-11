"""Evaluate the generated notes for the three samples.

1. Coverage of the reference facts, judged per fact by Claude, and the unsupported-claim
   rate from the claim check, for two conditions:
   - reference: notes generated from the scripted transcripts (samples/*/outputs);
   - gemini: notes generated end to end from the recorded gemini-3.8-flash transcripts
     (eval/results/transcription/), through the same redaction, drafting and check.
2. Planted errors: deliberate mistakes edited into the reference notes
   (eval/planted_errors.json), to see whether the claim check flags them.

Every model response is saved under eval/results/ and reused on the next run unless
--fresh is given, so the tables can be rebuilt without new API calls. The summary is
written to eval/results/summary.md and between the eval markers in README.md.

Run from backend/:  uv run python ../eval/run_eval.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select

from scribe.audit import AuditLog
from scribe.config import REPO_ROOT, Settings, get_settings
from scribe.db import Database
from scribe.encounters import create_encounter, export_encounter
from scribe.evaluation import CoverageJudge, NoteStatementText, unsupported_rate
from scribe.llm import ClaudeClient
from scribe.models import EncounterSource, ModelCall, Section
from scribe.notes.checking import ClaimChecker
from scribe.notes.drafting import PromptSegment
from scribe.notes.schemas import NoteSection, NoteStatement, SoapNote
from scribe.pipeline import Pipeline
from scribe.redaction import Redactor
from scribe.samples import Sample, load_sample_outputs, load_samples
from scribe.schemas import TranscriptSegment
from scribe.services import build_services
from scribe.storage import AudioStore
from scribe.transcription.fake import FakeTranscriber

RESULTS = REPO_ROOT / "eval" / "results"
GEMINI_MODEL = "gemini-3.8-flash"
FLAGGED = {"unsupported", "partially_supported"}
README = REPO_ROOT / "README.md"
START, END = "<!-- eval-results:start -->", "<!-- eval-results:end -->"


class Recorder:
    """Collects the audit rows of every model call the evaluation makes."""

    def __init__(self) -> None:
        self.db = Database("sqlite://")
        self.db.create_all()
        self.audit = AuditLog(self.db)

    def calls(self) -> list[dict[str, Any]]:
        with self.db.session() as session:
            return [
                {
                    "created_at": c.created_at.isoformat(),
                    "model": c.model,
                    "purpose": c.purpose,
                    "input_tokens": c.input_tokens,
                    "output_tokens": c.output_tokens,
                    "cost_usd": str(c.cost_usd),
                    "latency_ms": c.latency_ms,
                    "status": c.status,
                    "prompt_sha256": c.prompt_sha256,
                }
                for c in session.scalars(select(ModelCall).order_by(ModelCall.id))
            ]


def save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def statement_texts(note: dict[str, Any]) -> list[NoteStatementText]:
    return [NoteStatementText(s["id"], s["text"]) for s in note["statements"]]


# -- coverage ------------------------------------------------------------------------


def coverage_for(
    path: Path, sample: Sample, note: dict[str, Any], judge: CoverageJudge | None, fresh: bool
) -> dict[str, Any]:
    if path.exists() and not fresh:
        return dict(load(path))
    if judge is None:
        raise SystemExit(f"{path} is missing and ANTHROPIC_API_KEY is not set")
    score = judge.judge(sample.facts.facts, statement_texts(note))
    labels = [s["support"] for s in note["statements"]]
    unsupported, statements, rate = unsupported_rate(labels)
    result = {
        "sample": sample.slug,
        "facts": score.facts,
        "present": score.present,
        "partial": score.partial,
        "absent": score.absent,
        "coverage": round(score.coverage, 4),
        "missing_verdicts": score.missing_verdicts,
        "statements": statements,
        "unsupported": unsupported,
        "partially_supported": sum(1 for x in labels if x == "partially_supported"),
        "unsupported_rate": round(rate, 4),
        "rejected": len(note["rejected_statements"]),
        "verdicts": [v.model_dump() for v in score.verdicts],
    }
    save(path, result)
    return result


# -- gemini condition: full pipeline on the recorded Gemini transcripts ----------------


def gemini_note(
    sample: Sample, settings: Settings, redactor: Redactor, fresh: bool
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The note for this sample from its recorded Gemini transcript, and any new calls."""
    path = RESULTS / "notes" / "gemini" / f"{sample.slug}.json"
    if path.exists() and not fresh:
        return dict(load(path)), []
    if not settings.has_anthropic_key():
        raise SystemExit(f"{path} is missing and ANTHROPIC_API_KEY is not set")
    raw = load(RESULTS / "transcription" / GEMINI_MODEL / f"{sample.slug}.json")
    segments = [TranscriptSegment.model_validate(s) for s in raw["segments"]]
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(f"sqlite:///{Path(tmp) / 'eval.db'}")
        db.create_all()
        services = build_services(settings, db)
        services.redactor_factory = lambda: redactor
        services.store = AudioStore(Path(tmp) / "audio")
        services.fake = FakeTranscriber(segments)
        services.transcriber_mode = "fake"
        with sample.audio_path.open("rb") as audio:
            encounter_id = create_encounter(
                db,
                services.store,
                title=sample.title,
                source=EncounterSource.SAMPLE,
                audio=audio,
                mime_type="audio/mp4",
                max_bytes=50 * 1024 * 1024,
            )
        result = Pipeline(services).process(encounter_id)
        if not result.ok:
            raise SystemExit(f"{sample.slug}: pipeline failed: {result.error}")
        data = export_encounter(db, encounter_id)
        db.dispose()
    data["transcriber"] = f"recorded {GEMINI_MODEL} transcript ({raw['model']})"
    save(path, data)
    return data, list(data["model_calls"])


def leaked_pii(sample: Sample, note: dict[str, Any]) -> list[str]:
    """Known PII values that appear in any text that was sent to the note model."""
    sent = " ".join(s["redacted_text"] for s in note["segments"])
    return [p.value for p in sample.script.pii if p.value in sent]


# -- planted errors ----------------------------------------------------------------


def planted_note(
    original: dict[str, Any], edits: list[dict[str, Any]]
) -> tuple[SoapNote, dict[str, str]]:
    """The original note with the planted edits applied. Returns {statement_id: kind}."""
    by_section: dict[Section, list[NoteStatement]] = {s: [] for s in Section}
    for s in original["statements"]:
        by_section[Section(s["section"])].append(
            NoteStatement(
                id=s["id"],
                section=Section(s["section"]),
                text=s["text"],
                source_segment_ids=s["source_segment_ids"],
            )
        )
    planted: dict[str, str] = {}
    for edit in edits:
        if "replace" in edit:
            for statements in by_section.values():
                for statement in statements:
                    if statement.id == edit["replace"]:
                        statement.text = edit["text"]
                        planted[statement.id] = edit["kind"]
            if edit["replace"] not in planted:
                raise SystemExit(f"no statement {edit['replace']} to replace")
        else:
            section = Section(edit["insert"])
            new_id = f"{section.value}-{len(by_section[section]) + 1}"
            by_section[section].append(
                NoteStatement(
                    id=new_id, section=section, text=edit["text"], source_segment_ids=edit["cites"]
                )
            )
            planted[new_id] = edit["kind"]
    note = SoapNote(
        sections=[
            NoteSection(name=s, statements=by_section[s]) for s in Section.__members__.values()
        ]
    )
    return note, planted


def planted_check(
    sample: Sample,
    original: dict[str, Any],
    edits: list[dict[str, Any]],
    checker: ClaimChecker | None,
    fresh: bool,
) -> dict[str, Any]:
    path = RESULTS / "planted" / f"{sample.slug}.json"
    if path.exists() and not fresh:
        return dict(load(path))
    if checker is None:
        raise SystemExit(f"{path} is missing and ANTHROPIC_API_KEY is not set")
    note, planted = planted_note(original, edits)
    segments = [
        PromptSegment(s["id"], s["speaker"], s["redacted_text"]) for s in original["segments"]
    ]
    checked, _ = checker.check(note, segments)
    before = {s["id"]: s["support"] for s in original["statements"]}
    rows = [
        {
            "id": s.id,
            "text": s.text,
            "planted": planted.get(s.id),
            "label": s.support.value if s.support else None,
            "label_before": before.get(s.id),
            "reason": s.support_reason,
        }
        for s in checked.statements()
    ]
    planted_rows = [r for r in rows if r["planted"]]
    untouched = [r for r in rows if not r["planted"]]
    result = {
        "sample": sample.slug,
        "planted": len(planted_rows),
        "caught_unsupported": sum(1 for r in planted_rows if r["label"] == "unsupported"),
        "caught_partial": sum(1 for r in planted_rows if r["label"] == "partially_supported"),
        "missed": sum(1 for r in planted_rows if r["label"] not in FLAGGED),
        "untouched": len(untouched),
        "untouched_flagged": sum(1 for r in untouched if r["label"] in FLAGGED),
        "rows": rows,
    }
    save(path, result)
    return result


# -- report --------------------------------------------------------------------------


def pct(value: float) -> str:
    return f"{value:.1%}"


def render(summary: dict[str, Any]) -> str:
    lines = [
        f"Generated {summary['generated_at']} by `eval/run_eval.py`. Note and check: "
        f"`{summary['note_model']}` (effort `{summary['note_effort']}` / "
        f"`{summary['check_effort']}`); coverage judge: `{summary['judge_model']}`. "
        "Raw outputs are in `eval/results/`.",
        "",
        "| Transcript | Sample | Facts | Coverage | Present / partial / absent "
        "| Statements | Unsupported | Partly supported | Rejected |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    names = {"reference": "Scripted", "gemini": f"Gemini ({GEMINI_MODEL})"}
    for condition, rows in summary["conditions"].items():
        for r in rows:
            lines.append(
                f"| {names[condition]} | {r['sample']} | {r['facts']} | {pct(r['coverage'])} "
                f"| {r['present']} / {r['partial']} / {r['absent']} | {r['statements']} "
                f"| {r['unsupported']} ({pct(r['unsupported_rate'])}) "
                f"| {r['partially_supported']} | {r['rejected']} |"
            )
        t = summary["totals"][condition]
        lines.append(
            f"| **{names[condition]}** | **all** | **{t['facts']}** | **{pct(t['coverage'])}** "
            f"| {t['present']} / {t['partial']} / {t['absent']} | {t['statements']} "
            f"| {t['unsupported']} ({pct(t['unsupported_rate'])}) "
            f"| {t['partially_supported']} | {t['rejected']} |"
        )
    lines += [
        "",
        "Coverage = (present + 0.5 x partial) / facts. Unsupported rate = statements the claim "
        "check labelled unsupported / all statements.",
        "",
        "Planted errors (claim check run on the scripted-transcript notes after editing "
        "them; see `eval/planted_errors.json`):",
        "",
        "| Sample | Planted | Flagged unsupported | Flagged partly supported | Missed "
        "| Untouched statements flagged |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in summary["planted"]:
        lines.append(
            f"| {r['sample']} | {r['planted']} | {r['caught_unsupported']} | "
            f"{r['caught_partial']} | {r['missed']} "
            f"| {r['untouched_flagged']} of {r['untouched']} |"
        )
    p = summary["planted_totals"]
    lines.append(
        f"| **all** | **{p['planted']}** | **{p['caught_unsupported']}** | "
        f"**{p['caught_partial']}** | **{p['missed']}** | "
        f"**{p['untouched_flagged']} of {p['untouched']}** |"
    )
    leaks = summary["gemini_pii_leaks"]
    lines += [
        "",
        "Known PII values found in the redacted Gemini transcripts sent to the note model: "
        + (", ".join(f"{k}: {v}" for k, v in leaks.items() if v) or "none")
        + ".",
        "",
        f"Model calls made by this evaluation run: {summary['eval_calls']}, "
        f"${summary['eval_cost_usd']} at list prices.",
    ]
    return "\n".join(lines) + "\n"


def totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys = ["facts", "present", "partial", "absent", "statements", "unsupported"]
    keys += ["partially_supported", "rejected"]
    total = {k: sum(r[k] for r in rows) for k in keys}
    total["coverage"] = (total["present"] + 0.5 * total["partial"]) / total["facts"]
    total["unsupported_rate"] = total["unsupported"] / total["statements"]
    return total


def update_readme(table: str) -> bool:
    if not README.exists():
        return False
    text = README.read_text(encoding="utf-8")
    if START not in text or END not in text:
        return False
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    README.write_text(f"{head}{START}\n{table}{END}{tail}", encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true", help="ignore saved model outputs")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    settings = get_settings()
    samples = load_samples(settings.samples_dir)
    outputs = load_sample_outputs(samples)
    recorder = Recorder()
    judge: CoverageJudge | None = None
    checker: ClaimChecker | None = None
    if settings.anthropic_api_key and settings.has_anthropic_key():
        llm = ClaudeClient(
            audit=recorder.audit,
            api_key=settings.anthropic_api_key.get_secret_value(),
            fallbacks=settings.llm_fallbacks,
        )
        judge = CoverageJudge(llm, model=settings.soap_model, effort="medium")
        checker = ClaimChecker(llm, model=settings.check_model, effort=settings.check_effort)
    redactor = Redactor(settings.spacy_model)

    conditions: dict[str, list[dict[str, Any]]] = {"reference": [], "gemini": []}
    pipeline_calls: list[dict[str, Any]] = []
    leaks: dict[str, int] = {}
    for sample in samples:
        reference = outputs[sample.slug]
        conditions["reference"].append(
            coverage_for(
                RESULTS / "coverage" / "reference" / f"{sample.slug}.json",
                sample,
                reference,
                judge,
                args.fresh,
            )
        )
        note, new_calls = gemini_note(sample, settings, redactor, args.fresh)
        pipeline_calls += new_calls
        leaks[sample.slug] = len(leaked_pii(sample, note))
        conditions["gemini"].append(
            coverage_for(
                RESULTS / "coverage" / "gemini" / f"{sample.slug}.json",
                sample,
                note,
                judge,
                args.fresh,
            )
        )

    edits = load(REPO_ROOT / "eval" / "planted_errors.json")["samples"]
    planted = [
        planted_check(sample, outputs[sample.slug], edits[sample.slug], checker, args.fresh)
        for sample in samples
    ]
    planted_keys = ["planted", "caught_unsupported", "caught_partial", "missed"]
    planted_keys += ["untouched", "untouched_flagged"]

    calls = pipeline_calls + recorder.calls()
    previous = RESULTS / "summary.json"
    history = load(previous).get("eval_calls_log", []) if previous.exists() else []
    summary: dict[str, Any] = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "note_model": settings.soap_model,
        "note_effort": settings.soap_effort,
        "check_effort": settings.check_effort,
        "judge_model": settings.soap_model,
        "conditions": {
            name: [{k: v for k, v in r.items() if k != "verdicts"} for r in rows]
            for name, rows in conditions.items()
        },
        "totals": {name: totals(rows) for name, rows in conditions.items()},
        "planted": [{k: v for k, v in r.items() if k != "rows"} for r in planted],
        "planted_totals": {k: sum(r[k] for r in planted) for k in planted_keys},
        "gemini_pii_leaks": leaks,
        "eval_calls_log": history + calls,
    }
    summary["eval_calls"] = len(summary["eval_calls_log"])
    summary["eval_cost_usd"] = str(
        sum((Decimal(c["cost_usd"]) for c in summary["eval_calls_log"]), Decimal(0))
    )
    save(previous, summary)
    table = render(summary)
    (RESULTS / "summary.md").write_text(table, encoding="utf-8")
    print(table)
    print("README updated" if update_readme(table) else "README markers not found; skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
