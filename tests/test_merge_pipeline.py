from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest
from PIL import Image

from video_workflow import merge_pipeline
from video_workflow.merge_pipeline import MergeRequest
from video_workflow.pipeline import WorkflowCancelled, WorkflowError
from video_workflow.probe import MediaInfo, probe_media


def _media(duration_ms: int) -> MediaInfo:
    return MediaInfo(
        duration_ms=duration_ms,
        width=1280,
        height=720,
        fps=24,
        audio_duration_ms=duration_ms,
        has_audio=True,
        video_codec="h264",
        audio_codec="aac",
    )


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


def test_merge_output_created_during_render_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    output = tmp_path / "merged.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    monkeypatch.setattr(
        "video_workflow.merge_pipeline.probe_media",
        lambda path: _media(1000 if Path(path) in {first, second} else 2000),
    )
    monkeypatch.setattr(
        "video_workflow.merge_pipeline.normalize_video",
        lambda _source, target, **_kwargs: Path(target).write_bytes(b"normalized"),
    )

    def join_with_late_output(_first, _second, target, **_kwargs) -> None:
        Path(target).write_bytes(b"merged")
        output.write_bytes(b"late-output")

    monkeypatch.setattr("video_workflow.merge_pipeline.join_parts", join_with_late_output)

    with pytest.raises(WorkflowError, match="already exists"):
        merge_pipeline.merge_videos(MergeRequest(first, second, output))

    assert output.read_bytes() == b"late-output"


def _batch_request(tmp_path: Path) -> MergeRequest:
    first = tmp_path / "lesson_1.mp4"
    second = tmp_path / "lesson_2.mp4"
    first.write_bytes(b"part-1")
    second.write_bytes(b"part-2")
    return MergeRequest(
        first,
        second,
        tmp_path / "lesson.mp4",
        write_report=False,
        overwrite_output=True,
    )


def _stub_merge_tools(
    monkeypatch: pytest.MonkeyPatch,
    request: MergeRequest,
    *,
    cancel_event: threading.Event | None = None,
) -> None:
    def fake_probe(path: Path) -> MediaInfo:
        if Path(path) not in {request.first_video, request.second_video}:
            if cancel_event is not None:
                cancel_event.set()
            return _media(2000)
        return _media(1000)

    monkeypatch.setattr("video_workflow.merge_pipeline.probe_media", fake_probe)
    monkeypatch.setattr(
        "video_workflow.merge_pipeline.normalize_video",
        lambda _source, target, **_kwargs: Path(target).write_bytes(b"normalized"),
    )
    monkeypatch.setattr(
        "video_workflow.merge_pipeline.join_parts",
        lambda _first, _second, target, **_kwargs: Path(target).write_bytes(
            b"new-video"
        ),
    )


def test_batch_merge_replaces_verified_output_and_removes_stale_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _batch_request(tmp_path)
    request.output.write_bytes(b"old-video")
    report_path = Path(f"{request.output}.report.json")
    report_path.write_text("stale", encoding="utf-8")
    _stub_merge_tools(monkeypatch, request)

    merge_pipeline.merge_videos(request)

    assert request.output.read_bytes() == b"new-video"
    assert not report_path.exists()


@pytest.mark.parametrize("failure_stage", ["normalize", "join", "verify"])
def test_batch_merge_failure_preserves_previous_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    request = _batch_request(tmp_path)
    request.output.write_bytes(b"old-video")
    _stub_merge_tools(monkeypatch, request)
    if failure_stage == "normalize":
        monkeypatch.setattr(
            "video_workflow.merge_pipeline.normalize_video",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("normalize failed")
            ),
        )
    elif failure_stage == "join":
        monkeypatch.setattr(
            "video_workflow.merge_pipeline.join_parts",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("join failed")),
        )
    else:
        monkeypatch.setattr(
            "video_workflow.merge_pipeline.probe_media",
            lambda path: (
                _media(1000)
                if Path(path) in {request.first_video, request.second_video}
                else MediaInfo(
                    duration_ms=2000,
                    width=640,
                    height=360,
                    fps=24,
                    audio_duration_ms=2000,
                    has_audio=True,
                    video_codec="h264",
                    audio_codec="aac",
                )
            ),
        )

    with pytest.raises(WorkflowError):
        merge_pipeline.merge_videos(request)

    assert request.output.read_bytes() == b"old-video"


def test_batch_merge_cancelled_during_final_probe_preserves_previous_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _batch_request(tmp_path)
    request.output.write_bytes(b"old-video")
    report_path = Path(f"{request.output}.report.json")
    report_path.write_text("stale", encoding="utf-8")
    cancelled = threading.Event()
    _stub_merge_tools(monkeypatch, request, cancel_event=cancelled)

    with pytest.raises(WorkflowCancelled, match="cancelled"):
        merge_pipeline.merge_videos(request, cancel_event=cancelled)

    assert request.output.read_bytes() == b"old-video"
    assert report_path.read_text(encoding="utf-8") == "stale"
