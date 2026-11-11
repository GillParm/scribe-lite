"""Compare Gemini models on the bundled samples: word error rate and speaker labels.

Transcribes each sample's audio with each model, scores the result against the scripted
reference transcript and writes raw outputs plus a summary to
eval/results/transcription/. Saved outputs are reused unless --fresh is given, so the
scores can be recomputed without new API calls.

Run from backend/:  uv run python ../eval/compare_transcribers.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select

from scribe.audit import AuditLog
from scribe.config import REPO_ROOT, get_settings
from scribe.db import Database
from scribe.models import ModelCall
from scribe.samples import load_samples
from scribe.schemas import TranscriptSegment
from scribe.transcript_metrics import score_transcript
from scribe.transcription import AudioInput
from scribe.transcription.gemini import GeminiTranscriber

MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
OUT = REPO_ROOT / "eval" / "results" / "transcription"


def transcribe(model: str, audio: Path, sha256: str, api_key: str) -> dict[str, object]:
    db = Database("sqlite://")
    db.create_all()
    transcriber = GeminiTranscriber(api_key=api_key, model=model, audit=AuditLog(db))
    started = time.perf_counter()
    result = transcriber.transcribe(AudioInput(path=audio, mime_type="audio/mp4", sha256=sha256))
    elapsed = time.perf_counter() - started
    with db.session() as session:
        calls = [
            {
                "status": c.status,
                "input_tokens": c.input_tokens,
                "output_tokens": c.output_tokens,
                "cost_usd_at_paid_rates": str(c.cost_usd),
                "latency_ms": c.latency_ms,
                "prompt_sha256": c.prompt_sha256,
            }
            for c in session.scalars(select(ModelCall).order_by(ModelCall.id))
        ]
    return {
        "model": model,
        "wall_seconds": round(elapsed, 1),
        "calls": calls,
        "segments": [s.model_dump(mode="json") for s in result.segments],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true", help="ignore saved outputs")
    parser.add_argument("--models", nargs="*", default=MODELS)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    settings = get_settings()
    samples = load_samples(settings.samples_dir)
    rows: list[dict[str, Any]] = []
    for model in args.models:
        for sample in samples:
            raw_path = OUT / model / f"{sample.slug}.json"
            if raw_path.exists() and not args.fresh:
                raw = json.loads(raw_path.read_text())
            else:
                if not settings.has_gemini_key():
                    print("GEMINI_API_KEY is not set", file=sys.stderr)
                    return 1
                assert settings.gemini_api_key is not None
                raw = transcribe(
                    model,
                    sample.audio_path,
                    sample.transcript.audio_sha256,
                    settings.gemini_api_key.get_secret_value(),
                )
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_text(json.dumps(raw, indent=2) + "\n")
            hypothesis = [TranscriptSegment.model_validate(s) for s in raw["segments"]]
            score = score_transcript(sample.transcript.segments, hypothesis)
            ok_calls = [c for c in raw["calls"] if c["status"] == "ok"]
            rows.append(
                {
                    "model": model,
                    "sample": sample.slug,
                    **score.as_dict(),
                    "input_tokens": sum(c["input_tokens"] for c in ok_calls),
                    "output_tokens": sum(c["output_tokens"] for c in ok_calls),
                    "latency_s": round(sum(c["latency_ms"] for c in ok_calls) / 1000, 1),
                    "attempts": len(raw["calls"]),
                }
            )
            print(json.dumps(rows[-1]))

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(rows, indent=2) + "\n")
    (OUT / "summary.md").write_text(render(rows))
    print(render(rows))
    return 0


def render(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Model | Sample | Speaker accuracy | WER | Segments (ref / hyp) "
        "| Mixed segments | Median start error (s) | Latency (s) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r['model']} | {r['sample']} | {float(r['speaker_accuracy']):.1%} "
            f"| {float(r['word_error_rate']):.1%} "
            f"| {r['reference_segments']} / {r['hypothesis_segments']} "
            f"| {r['mixed_segments']} | {r['median_start_error_s']} | {r['latency_s']} |"
        )
    by_model: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_model.setdefault(str(r["model"]), []).append(r)
    lines += ["", "| Model | Mean speaker accuracy | Mean WER | Mixed segments (total) |"]
    lines.append("| --- | --- | --- | --- |")
    for model, model_rows in by_model.items():
        acc = sum(float(r["speaker_accuracy"]) for r in model_rows) / len(model_rows)
        wer = sum(float(r["word_error_rate"]) for r in model_rows) / len(model_rows)
        mixed = sum(int(r["mixed_segments"]) for r in model_rows)
        lines.append(f"| {model} | {acc:.1%} | {wer:.1%} | {mixed} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
