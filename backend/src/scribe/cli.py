"""Command-line entry point: `scribe <command>`."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from scribe.audit import AuditLog
from scribe.clock import utcnow
from scribe.config import get_settings
from scribe.db import Database
from scribe.encounters import create_encounter, export_encounter, seed_samples
from scribe.models import EncounterSource
from scribe.pipeline import Pipeline
from scribe.retention import purge_expired_audio
from scribe.samples import OUTPUT_FILE, load_sample_outputs, load_samples
from scribe.services import build_services
from scribe.storage import AudioStore


def _retention_run(args: argparse.Namespace) -> int:
    settings = get_settings()
    db = Database(settings.database_url)
    try:
        report = purge_expired_audio(
            db,
            AudioStore(settings.audio_dir),
            retention_days=args.days or settings.retention_days,
            now=utcnow(),
            dry_run=args.dry_run,
        )
    finally:
        db.dispose()
    verb = "would delete" if report.dry_run else "deleted"
    print(
        f"retention: {verb} {len(report.deleted)} audio file(s), "
        f"{report.bytes_freed} bytes, older than {report.cutoff:%Y-%m-%d %H:%M} UTC"
    )
    for item in report.deleted:
        print(f"  {item.encounter_id}  {item.audio_path}  {item.age_days:.1f} days")
    return 0


def _samples_seed(args: argparse.Namespace) -> int:
    settings = get_settings()
    db = Database(settings.database_url)
    try:
        samples = load_samples(settings.samples_dir)
        created = seed_samples(
            db,
            AudioStore(settings.audio_dir),
            samples,
            max_bytes=settings.max_upload_mb * 1024 * 1024,
            outputs=load_sample_outputs(samples),
        )
    finally:
        db.dispose()
    print(f"seeded {len(created)} sample encounter(s)")
    return 0


def _samples_rebuild(args: argparse.Namespace) -> int:
    """Run the real pipeline on each sample and write samples/<slug>/outputs/."""
    settings = get_settings()
    if not settings.has_anthropic_key():
        print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 1
    samples = [
        s for s in load_samples(settings.samples_dir) if not args.slugs or s.slug in args.slugs
    ]
    failures = 0
    for sample in samples:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(f"sqlite:///{Path(tmp) / 'rebuild.db'}")
            db.create_all()
            services = build_services(settings, db)
            services.store = AudioStore(Path(tmp) / "audio")
            services.transcriber_mode = "reference"
            with sample.audio_path.open("rb") as audio:
                encounter_id = create_encounter(
                    db,
                    services.store,
                    title=sample.title,
                    source=EncounterSource.SAMPLE,
                    audio=audio,
                    mime_type="audio/mp4",
                    max_bytes=settings.max_upload_mb * 1024 * 1024,
                    sample_slug=sample.slug,
                )
            result = Pipeline(services).process(encounter_id)
            if not result.ok:
                print(f"{sample.slug}: failed: {result.error}", file=sys.stderr)
                failures += 1
                db.dispose()
                continue
            data = export_encounter(db, encounter_id)
            totals = AuditLog(db).totals()
            db.dispose()
        sample.outputs_dir.mkdir(exist_ok=True)
        (sample.outputs_dir / OUTPUT_FILE).write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        stats = result.stats
        assert stats is not None
        print(
            f"{sample.slug}: {stats.statements} statements, {stats.unsupported} unsupported, "
            f"{stats.partially_supported} partial, {len(data['rejected_statements'])} rejected; "
            f"{totals.calls} calls, {totals.input_tokens} in / {totals.output_tokens} out, "
            f"${totals.cost_usd:.4f}"
        )
    return 1 if failures else 0


def _process(args: argparse.Namespace) -> int:
    settings = get_settings()
    db = Database(settings.database_url)
    try:
        result = Pipeline(build_services(settings, db)).process(args.encounter_id)
    finally:
        db.dispose()
    print("ok" if result.ok else f"failed: {result.error}")
    return 0 if result.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scribe", description="scribe-lite (demo only)")
    sub = parser.add_subparsers(dest="command", required=True)

    retention = sub.add_parser("retention", help="audio retention")
    retention_sub = retention.add_subparsers(dest="action", required=True)
    run = retention_sub.add_parser("run", help="delete audio older than RETENTION_DAYS")
    run.add_argument("--dry-run", action="store_true", help="list what would be deleted")
    run.add_argument("--days", type=int, help="override RETENTION_DAYS for this run")
    run.set_defaults(func=_retention_run)

    samples = sub.add_parser("samples", help="bundled synthetic samples")
    samples_sub = samples.add_subparsers(dest="action", required=True)
    seed = samples_sub.add_parser("seed", help="import samples with precomputed outputs")
    seed.set_defaults(func=_samples_seed)
    rebuild = samples_sub.add_parser(
        "rebuild", help="re-run the real pipeline on the samples (needs ANTHROPIC_API_KEY)"
    )
    rebuild.add_argument("slugs", nargs="*", help="only these samples")
    rebuild.set_defaults(func=_samples_rebuild)

    process = sub.add_parser("process", help="run the pipeline for one encounter")
    process.add_argument("encounter_id")
    process.set_defaults(func=_process)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
