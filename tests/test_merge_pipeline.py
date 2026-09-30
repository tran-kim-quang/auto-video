from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from video_workflow import merge_pipeline
from video_workflow.merge_pipeline import MergeRequest
from video_workflow.pipeline import WorkflowError
from video_workflow.probe import probe_media


def _make_video(
    path: Path,
    *,
    color: str,
    size: str,
    fps: int,
    duration: float,
    frequency: int | None,
) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={size}:r={fps}:d={duration}",
    ]
    if frequency is not None:
        command += [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={duration}:sample_rate=48000",
            "-shortest",
        ]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if frequency is not None:
        command += ["-c:a", "aac"]
    command += ["-y", str(path)]
    subprocess.run(command, check=True, capture_output=True)


def _frame(video: Path, seconds: float, target: Path) -> Image.Image:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(seconds),
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-y",
            str(target),
        ],
        check=True,
        capture_output=True,
    )
    return Image.open(target).convert("RGB")


def _close(actual: tuple[int, int, int], expected: tuple[int, int, int]) -> bool:
    return all(abs(left - right) <= 25 for left, right in zip(actual, expected))


@pytest.mark.integration
def test_merge_videos_normalizes_mismatched_inputs_and_keeps_order(
    tmp_path: Path,
) -> None:
    first = tmp_path / "portrait-with-audio.mp4"
    second = tmp_path / "landscape-silent.mp4"
    output = tmp_path / "merged.mp4"
    _make_video(
        first, color="red", size="360x640", fps=30, duration=0.75, frequency=440
    )
    _make_video(
        second, color="blue", size="640x360", fps=15, duration=0.5, frequency=None
    )
    stages: list[str] = []

    report = merge_pipeline.merge_videos(
        MergeRequest(first, second, output),
        on_stage=stages.append,
    )

    info = probe_media(output)
    assert (info.width, info.height, info.fps) == (1280, 720, 24)
    assert info.has_audio is True
    assert info.duration_ms == pytest.approx(1250, abs=100)
    assert _close(
        _frame(output, 0.25, tmp_path / "first.png").getpixel((640, 360)), (255, 0, 0)
    )
    assert _close(
        _frame(output, 1.0, tmp_path / "second.png").getpixel((640, 360)), (0, 0, 255)
    )
    assert stages == [
        "validating",
        "normalizing_first",
        "normalizing_second",
        "joining",
        "verifying",
    ]
    assert report.first_duration_ms == pytest.approx(750, abs=50)
    assert report.second_duration_ms == pytest.approx(500, abs=50)
    payload = json.loads(Path(f"{output}.report.json").read_text(encoding="utf-8"))
    assert payload["inputs"] == {
        "first_video": str(first.resolve()),
        "second_video": str(second.resolve()),
    }


def test_merge_videos_rejects_missing_input_without_creating_output(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.mp4"
    first.write_bytes(b"x")
    output = tmp_path / "merged.mp4"

    with pytest.raises(WorkflowError, match="second_video.*does not exist"):
        merge_pipeline.merge_videos(
            MergeRequest(first, tmp_path / "missing.mp4", output)
        )

    assert not output.exists()
