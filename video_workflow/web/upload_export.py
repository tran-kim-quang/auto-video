from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ExportItem:
    relpath: str
    triple: str
    video: Path
    vtt: Path


def find_output_files(leaf: Path) -> tuple[Path, Path] | None:
    output = Path(leaf) / "output"
    if not output.is_dir():
        return None
    videos = sorted(path for path in output.iterdir() if path.suffix.lower() == ".mp4")
    vtt = output / "vi.vtt"
    if len(videos) != 1 or not vtt.is_file():
        return None
    return videos[0], vtt


def new_export_dir(
    exports_root: Path,
    batch_id: str,
    now: datetime | None = None,
) -> Path:
    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    export_dir = Path(exports_root) / batch_id / timestamp
    export_dir.mkdir(parents=True, exist_ok=False)
    return export_dir


def _link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def build_video_tree(export_dir: Path, items: Sequence[ExportItem]) -> Path:
    root = Path(export_dir) / "video"
    root.mkdir(parents=True, exist_ok=True)
    for item in items:
        _link_or_copy(item.video, root / f"{item.triple}.mp4")
    return root


def build_cc_tree(export_dir: Path, items: Sequence[ExportItem]) -> Path:
    root = Path(export_dir) / "cc"
    root.mkdir(parents=True, exist_ok=True)
    for item in items:
        _link_or_copy(item.vtt, root / item.triple / "vi.vtt")
    return root
