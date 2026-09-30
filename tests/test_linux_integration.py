from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from video_workflow.pipeline import BuildRequest, build_video


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "test"


@pytest.mark.integration
def test_linux_pipeline_builds_short_video(tmp_path: Path) -> None:
    tools = {
        "LibreOffice": shutil.which("libreoffice") or shutil.which("soffice"),
        "pdftocairo": shutil.which("pdftocairo"),
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
    }
    missing = [name for name, executable in tools.items() if executable is None]
    if not sys.platform.startswith("linux") or missing:
        reason = ", ".join(missing) if missing else "non-Linux platform"
        pytest.skip(f"Linux integration dependencies are not installed: {reason}")

    source = tmp_path / "source.wav"
    outro = tmp_path / "outro.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=720x1280:d=1:r=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:duration=1",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(outro),
        ],
        check=True,
        capture_output=True,
    )
    timeline = tmp_path / "timeline.txt"
    timeline.write_text(
        "Slide 01: 00:00.000 --> 00:01.000\n"
        "Slide 02: 00:01.000 --> 00:02.000\n",
        encoding="utf-8",
    )
    request = BuildRequest(
        source_media=source,
        pptx=FIXTURES / "TOAN7_C4_B12_T36_2_fixed.pptx",
        timeline=timeline,
        logo=FIXTURES / "logo.png",
        outro=outro,
        output=tmp_path / "final.mp4",
    )

    report = build_video(request)

    assert request.output.stat().st_size > 0
    assert Path(f"{request.output}.report.json").is_file()
    assert report.slide_count == 2
