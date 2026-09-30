from __future__ import annotations

import subprocess
import struct
import threading
from pathlib import Path

import pytest
from PIL import Image

from video_workflow.compose import CompositionCancelled, _run_ffmpeg, join_parts, normalize_outro, render_lecture
from video_workflow.probe import probe_media
from video_workflow.timeline import FrameSpan


def _run(*args: str) -> None:
    subprocess.run(list(args), check=True, capture_output=True, shell=False)


def _make_audio(path: Path, duration: float = 2.0) -> None:
    _run(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duration}:sample_rate=48000",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s=1280x720:r=24:d={duration}",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-y",
        str(path),
    )


def _make_audio_only(path: Path, duration: float = 1.0) -> None:
    _run(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        f"sine=frequency=440:duration={duration}:sample_rate=48000", "-y", str(path),
    )


def _make_video_from_image(path: Path, image: Path, duration: float, frequency: int | None) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-loop",
        "1",
        "-framerate",
        "24",
        "-i",
        str(image),
    ]
    if frequency is not None:
        command += [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={duration}:sample_rate=48000",
        ]
    command += [
        "-t",
        str(duration),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
    ]
    if frequency is not None:
        command += ["-c:a", "aac"]
    command += ["-y", str(path)]
    _run(*command)


def _frame(video: Path, seconds: float, target: Path) -> Image.Image:
    _run(
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
    )
    return Image.open(target).convert("RGB")


def _close(actual: tuple[int, int, int], expected: tuple[int, int, int], tolerance: int = 25) -> bool:
    return all(abs(left - right) <= tolerance for left, right in zip(actual, expected))


def _audio_frequency(video: Path, start: float, duration: float = 0.2) -> float:
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(start),
            "-i",
            str(video),
            "-t",
            str(duration),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "8000",
            "-f",
            "s16le",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
        shell=False,
    )
    samples = struct.unpack(f"<{len(completed.stdout) // 2}h", completed.stdout)
    crossings = sum((left < 0 <= right) or (left >= 0 > right) for left, right in zip(samples, samples[1:]))
    return crossings / (2 * duration)


@pytest.mark.parametrize("logo_mode", ["RGB", "RGBA"])
def test_render_lecture_changes_slides_on_frames_and_overlays_logo(
    tmp_path: Path, logo_mode: str
) -> None:
    slides: dict[int, Path] = {}
    for slide_id, color in ((1, (240, 20, 20)), (2, (20, 220, 20)), (3, (20, 20, 240))):
        path = tmp_path / f"slide_{slide_id}.png"
        Image.new("RGB", (1280, 720), color).save(path)
        slides[slide_id] = path
    logo = tmp_path / f"logo_{logo_mode}.png"
    if logo_mode == "RGBA":
        Image.new("RGBA", (200, 100), (250, 250, 0, 255)).save(logo)
    else:
        Image.new("RGB", (200, 100), (250, 250, 0)).save(logo)
    source = tmp_path / "source.mp4"
    _make_audio(source)
    output_dir = tmp_path / "đầu ra có dấu"
    output_dir.mkdir()
    output = output_dir / "lecture.mp4"
    spans = (
        FrameSpan(1, 0, 12),
        FrameSpan(2, 12, 24),
        FrameSpan(3, 24, 36),
    )

    render_lecture(slides, spans, source, logo, output, fps=24, logo_width_ratio=0.1, margin_px=10)

    info = probe_media(output)
    assert (info.width, info.height, info.fps) == (1280, 720, 24)
    assert info.has_audio is True
    assert info.duration_ms == pytest.approx(1500, abs=45)
    samples = [
        _frame(output, 0.25, tmp_path / "frame1.png"),
        _frame(output, 0.75, tmp_path / "frame2.png"),
        _frame(output, 1.25, tmp_path / "frame3.png"),
    ]
    assert _close(samples[0].getpixel((640, 360)), (240, 20, 20))
    assert _close(samples[1].getpixel((640, 360)), (20, 220, 20))
    assert _close(samples[2].getpixel((640, 360)), (20, 20, 240))
    assert _close(samples[2].getpixel((1200, 680)), (250, 250, 0))
    assert _close(samples[2].getpixel((1200, 620)), (20, 20, 240))


def test_render_lecture_without_logo_keeps_slide_and_audio(tmp_path: Path) -> None:
    slide = tmp_path / "slide.png"
    Image.new("RGB", (1280, 720), (20, 80, 120)).save(slide)
    source = tmp_path / "source.mp4"
    _make_audio(source, duration=1.0)
    output = tmp_path / "lecture.mp4"

    render_lecture(
        {1: slide},
        (FrameSpan(1, 0, 24),),
        source,
        None,
        output,
        fps=24,
        logo_width_ratio=0.1,
        margin_px=10,
    )

    info = probe_media(output)
    assert info.has_audio is True
    sample = _frame(output, 0.5, tmp_path / "frame-without-logo.png")
    assert _close(sample.getpixel((1200, 680)), (20, 80, 120))


def test_render_lecture_fits_four_by_three_slide_without_cropping(tmp_path: Path) -> None:
    slide = tmp_path / "slide.png"
    Image.new("RGB", (640, 480), (230, 30, 30)).save(slide)
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (10, 10), (255, 255, 255, 0)).save(logo)
    source = tmp_path / "source.mp4"
    _make_audio(source, duration=1.0)
    output = tmp_path / "lecture.mp4"

    render_lecture(
        {1: slide},
        (FrameSpan(1, 0, 24),),
        source,
        logo,
        output,
        fps=24,
        logo_width_ratio=0.1,
        margin_px=10,
    )

    sample = _frame(output, 0.5, tmp_path / "frame.png")
    assert _close(sample.getpixel((20, 360)), (0, 0, 0))
    assert _close(sample.getpixel((170, 360)), (230, 30, 30))


@pytest.mark.parametrize("suffix", [".wav", ".m4a"])
def test_render_lecture_accepts_audio_only_source(tmp_path: Path, suffix: str) -> None:
    slide = tmp_path / "slide.png"
    Image.new("RGB", (1280, 720), (20, 80, 120)).save(slide)
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (2, 2), (0, 0, 0, 0)).save(logo)
    source = tmp_path / f"source{suffix}"
    _make_audio_only(source)
    output = tmp_path / "lecture.mp4"
    render_lecture({1: slide}, (FrameSpan(1, 0, 24),), source, logo, output, fps=24, logo_width_ratio=.01, margin_px=0)
    info = probe_media(output)
    assert info.has_audio and info.audio_codec == "aac"
    assert info.duration_ms == pytest.approx(1000, abs=45)


def test_ffmpeg_runner_terminates_when_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeProcess:
        returncode = None
        stderr = None

        def poll(self):
            return None

        def terminate(self):
            calls.append("terminate")

        def wait(self, timeout=None):
            calls.append(f"wait:{timeout}")
            self.returncode = 1
            return 1

        def kill(self):
            calls.append("kill")

    monkeypatch.setattr("video_workflow.compose.subprocess.Popen", lambda *a, **k: FakeProcess())
    event = threading.Event()
    event.set()
    with pytest.raises(CompositionCancelled):
        _run_ffmpeg(["ffmpeg"], "test", cancel_event=event)
    assert calls[:2] == ["terminate", "wait:5"]


def test_render_lecture_removes_powerpoint_export_border_and_places_logo_flush(
    tmp_path: Path,
) -> None:
    slide = Image.new("RGB", (1280, 720), (255, 255, 255))
    slide.paste((30, 140, 70), (14, 8, 1266, 712))
    slide_path = tmp_path / "bordered-slide.png"
    slide.save(slide_path)
    logo = tmp_path / "logo.png"
    Image.new("RGB", (200, 100), (250, 220, 0)).save(logo)
    source = tmp_path / "source.mp4"
    _make_audio(source, duration=1.0)
    output = tmp_path / "lecture.mp4"

    render_lecture(
        {1: slide_path},
        (FrameSpan(1, 0, 24),),
        source,
        logo,
        output,
        fps=24,
        logo_width_ratio=0.1,
        margin_px=0,
    )

    sample = _frame(output, 0.5, tmp_path / "border-frame.png")
    assert _close(sample.getpixel((640, 0)), (30, 140, 70))
    assert _close(sample.getpixel((640, 719)), (30, 140, 70))
    assert _close(sample.getpixel((0, 360)), (30, 140, 70))
    assert _close(sample.getpixel((1279, 719)), (250, 220, 0))


def test_normalize_outro_preserves_full_portrait_frame_and_audio(tmp_path: Path) -> None:
    image = Image.new("RGB", (180, 320), (100, 100, 100))
    for box, color in (
        ((0, 0, 30, 30), (240, 20, 20)),
        ((150, 0, 180, 30), (20, 240, 20)),
        ((0, 290, 30, 320), (20, 20, 240)),
        ((150, 290, 180, 320), (240, 240, 20)),
    ):
        patch = Image.new("RGB", (box[2] - box[0], box[3] - box[1]), color)
        image.paste(patch, box[:2])
    image_path = tmp_path / "portrait.png"
    image.save(image_path)
    source = tmp_path / "portrait.mp4"
    _make_video_from_image(source, image_path, duration=0.75, frequency=880)
    output = tmp_path / "normalized.mp4"

    normalize_outro(source, output, fps=24)

    info = probe_media(output)
    assert (info.width, info.height, info.fps) == (1280, 720, 24)
    assert info.has_audio is True
    assert info.duration_ms == pytest.approx(750, abs=45)
    sample = _frame(output, 0.3, tmp_path / "portrait-frame.png")
    assert _close(sample.getpixel((20, 360)), (0, 0, 0))
    assert _close(sample.getpixel((450, 20)), (240, 20, 20))
    assert _close(sample.getpixel((820, 20)), (20, 240, 20))
    assert _close(sample.getpixel((450, 700)), (20, 20, 240))
    assert _close(sample.getpixel((820, 700)), (240, 240, 20))


def test_normalize_outro_adds_silence_when_source_has_no_audio(tmp_path: Path) -> None:
    image = tmp_path / "landscape.png"
    Image.new("RGB", (1280, 720), (50, 60, 70)).save(image)
    source = tmp_path / "silent.mp4"
    _make_video_from_image(source, image, duration=0.5, frequency=None)
    output = tmp_path / "normalized.mp4"

    normalize_outro(source, output, fps=24)

    info = probe_media(output)
    assert info.has_audio is True
    assert info.duration_ms == pytest.approx(500, abs=45)


def test_join_parts_places_outro_audio_after_lecture_audio(tmp_path: Path) -> None:
    image = tmp_path / "frame.png"
    Image.new("RGB", (1280, 720), (80, 90, 100)).save(image)
    lecture = tmp_path / "lecture.mp4"
    outro_source = tmp_path / "outro-source.mp4"
    outro = tmp_path / "outro.mp4"
    final = tmp_path / "final.mp4"
    _make_video_from_image(lecture, image, duration=1.0, frequency=440)
    _make_video_from_image(outro_source, image, duration=0.5, frequency=880)
    normalize_outro(outro_source, outro, fps=24)

    join_parts(lecture, outro, final)

    info = probe_media(final)
    assert info.duration_ms == pytest.approx(1500, abs=55)
    assert _audio_frequency(final, 0.3) == pytest.approx(440, abs=35)
    assert _audio_frequency(final, 1.2) == pytest.approx(880, abs=45)
