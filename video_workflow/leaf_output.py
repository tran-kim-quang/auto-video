from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from .pipeline import _publish_artifact_tree


VTT_NAME = "vi.vtt"


def publish_leaf_output(
    leaf: Path,
    video: Path,
    stem: str,
    vtt_text: str,
) -> Path:
    leaf = Path(leaf)
    video = Path(video)
    staging = leaf / f".output.staging-{uuid4().hex}"
    destination = leaf / "output"
    output_name = f"{stem}.mp4"
    try:
        staging.mkdir()
        shutil.copy2(video, staging / output_name)
        with (staging / VTT_NAME).open(
            "w",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            handle.write(vtt_text)
        _publish_artifact_tree(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination / output_name
