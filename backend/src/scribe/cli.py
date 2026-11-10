"""Command-line entry point: `scribe <command>`."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from scribe.clock import utcnow
from scribe.config import get_settings
from scribe.db import Database
from scribe.retention import purge_expired_audio
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scribe", description="scribe-lite (demo only)")
    sub = parser.add_subparsers(dest="command", required=True)

    retention = sub.add_parser("retention", help="audio retention")
    retention_sub = retention.add_subparsers(dest="action", required=True)
    run = retention_sub.add_parser("run", help="delete audio older than RETENTION_DAYS")
    run.add_argument("--dry-run", action="store_true", help="list what would be deleted")
    run.add_argument("--days", type=int, help="override RETENTION_DAYS for this run")
    run.set_defaults(func=_retention_run)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
