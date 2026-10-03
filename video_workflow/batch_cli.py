from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from .batch_workflow import BatchRequest, run_batch


DEFAULT_SOURCE_ROOT = Path("/home/meconlonton/Documents/gen_video")
DEFAULT_ASSETS_DIR = Path("/home/meconlonton/work/auto-video/test/logo_and_outro")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build all two-part slide videos in a lesson tree.")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--assets-dir", type=Path, default=DEFAULT_ASSETS_DIR)
    parser.add_argument("--fps", type=int, default=24)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_batch(BatchRequest(args.source_root, args.assets_dir, args.fps))
    for result in report.results:
        detail = f" - {result.error}" if result.error else ""
        print(f"{result.lesson}: {result.status}{detail}")
    completed = sum(item.status == "completed" for item in report.results)
    skipped = sum(item.status == "skipped" for item in report.results)
    failed = sum(item.status == "failed" for item in report.results)
    print(f"completed={completed} skipped={skipped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

