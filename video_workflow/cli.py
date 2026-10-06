from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .pipeline import BuildRequest, WorkflowError, build_video


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a 720p narrated slide video from PPTX and timeline metadata.")
    parser.add_argument(
        "--source-media", "--video", dest="source_media", required=True, type=Path,
        help="Source video or audio used for narration",
    )
    parser.add_argument("--pptx", required=True, type=Path, help="PowerPoint slide deck")
    parser.add_argument("--timeline", required=True, type=Path, help="Slide timeline in TXT or JSON format")
    parser.add_argument("--logo", type=Path, help="Optional logo image placed at lower right")
    parser.add_argument("--outro", type=Path, help="Optional video appended after the final slide")
    parser.add_argument("--output", required=True, type=Path, help="New MP4 output path")
    parser.add_argument("--fps", type=int, default=24, help="Output frame rate (default: 24)")
    parser.add_argument(
        "--logo-width-ratio",
        type=float,
        default=0.12,
        help="Logo width as a fraction of 1280 (default: 0.12)",
    )
    parser.add_argument("--margin-px", type=int, default=0, help="Logo margin from bottom/right edges (default: 0)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    request = BuildRequest(
        source_media=args.source_media,
        pptx=args.pptx,
        timeline=args.timeline,
        logo=args.logo,
        outro=args.outro,
        output=args.output,
        fps=args.fps,
        logo_width_ratio=args.logo_width_ratio,
        margin_px=args.margin_px,
    )
    try:
        report = build_video(request)
    except WorkflowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    output = getattr(report, "output", str(args.output))
    print(output)
    return 0
