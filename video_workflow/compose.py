from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path

from .probe import probe_media
from .timeline import FrameSpan


class CompositionError(RuntimeError):
    """Raised when FFmpeg cannot create a valid workflow artifact."""


class CompositionCancelled(CompositionError):
    """Raised when an active FFmpeg operation is cancelled."""


def _ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise CompositionError("ffmpeg is not available on PATH")
    return executable


def _run_ffmpeg(
    argv: list[str],
    action: str,
    *,
    cancel_event: threading.Event | None = None,
    log_path: Path | None = None,
) -> None:
    log_handle = None
    try:
        if log_path is None:
            log_handle = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        else:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            log_handle = Path(log_path).open("a+", encoding="utf-8")
            log_handle.write(f"\n[{action}]\n")
            log_handle.flush()
        process = subprocess.Popen(
            argv, stdout=subprocess.DEVNULL, stderr=log_handle, text=True, shell=False
        )
        while process.poll() is None:
            if cancel_event is not None and cancel_event.wait(0.1):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise CompositionCancelled(f"{action} cancelled")
            if cancel_event is None:
                process.wait()
        if process.returncode:
            log_handle.flush()
            log_handle.seek(0)
            detail = log_handle.read().strip()
            raise CompositionError(f"{action} failed: {detail or f'ffmpeg exited with {process.returncode}'}")
    except CompositionError:
        raise
    except OSError as exc:
        raise CompositionError(f"{action} failed: {exc}") from exc
    finally:
        if log_handle is not None:
            log_handle.close()


def _concat_quote(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "\\'")


def _write_slide_concat(
    path: Path,
    slides: Mapping[int, Path],
    spans: Sequence[FrameSpan],
    fps: int,
) -> None:
    lines: list[str] = []
    for span in spans:
        image = slides.get(span.slide_id)
        if image is None or not Path(image).is_file():
            raise CompositionError(f"missing rendered image for slide {span.slide_id}")
        frame_count = span.end_frame - span.start_frame
        if frame_count <= 0:
            raise CompositionError(f"slide {span.slide_id} has no output frames")
        lines.append(f"file '{_concat_quote(Path(image))}'")
        lines.append(f"duration {frame_count / fps:.9f}")
    lines.append(f"file '{_concat_quote(Path(slides[spans[-1].slide_id]))}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_lecture(
    slides: Mapping[int, Path],
    spans: Sequence[FrameSpan],
    source_video: Path,
    logo: Path,
    target: Path,
    *,
    fps: int,
    logo_width_ratio: float,
    margin_px: int,
    cancel_event: threading.Event | None = None,
    log_path: Path | None = None,
) -> None:
    if not spans:
        raise CompositionError("cannot render a lecture without slide spans")
    if fps <= 0:
        raise CompositionError("fps must be positive")
    if not 0 < logo_width_ratio <= 1:
        raise CompositionError("logo width ratio must be between 0 and 1")
    if margin_px < 0:
        raise CompositionError("logo margin must not be negative")
    source_video = Path(source_video)
    logo = Path(logo)
    target = Path(target)
    if not source_video.is_file():
        raise CompositionError(f"source video does not exist: {source_video}")
    if not logo.is_file():
        raise CompositionError(f"logo does not exist: {logo}")

    expected_start = spans[0].start_frame
    for span in spans:
        if span.start_frame != expected_start:
            raise CompositionError("slide frame spans must be contiguous")
        expected_start = span.end_frame
    total_frames = spans[-1].end_frame - spans[0].start_frame
    if spans[0].start_frame != 0 or total_frames <= 0:
        raise CompositionError("slide frame spans must begin at frame zero")
    duration = total_frames / fps
    logo_width = max(1, round(1280 * logo_width_ratio))
    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="slide-concat-", dir=target.parent) as temporary:
        concat_file = Path(temporary) / "slides.ffconcat"
        _write_slide_concat(concat_file, slides, spans, fps)
        filter_graph = (
            f"[0:v]scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black,"
            "crop=1252:704:14:8,scale=1280:720,"
            f"fps={fps},setsar=1[base];"
            f"[1:v]scale={logo_width}:-1[logo];"
            f"[base][logo]overlay=x=W-w-{margin_px}:y=H-h-{margin_px}:"
            "eof_action=repeat:format=auto[v];"
            f"[2:a:0]atrim=start=0:end={duration:.9f},asetpts=PTS-STARTPTS,"
            "aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo[a]"
        )
        argv = [
            _ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-i",
            str(logo.resolve()),
            "-i",
            str(source_video.resolve()),
            "-filter_complex",
            filter_graph,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-frames:v",
            str(total_frames),
            "-t",
            f"{duration:.9f}",
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            "-y",
            str(target.resolve()),
        ]
        _run_ffmpeg(argv, "lecture render", cancel_event=cancel_event, log_path=log_path)
    if not target.is_file() or target.stat().st_size == 0:
        raise CompositionError("lecture render did not produce an output file")


def normalize_outro(
    outro: Path,
    target: Path,
    *,
    fps: int,
    cancel_event: threading.Event | None = None,
    log_path: Path | None = None,
) -> None:
    outro = Path(outro)
    target = Path(target)
    if fps <= 0:
        raise CompositionError("fps must be positive")
    info = probe_media(outro)
    if info.width is None or info.height is None:
        raise CompositionError("outro does not contain a video stream")
    duration = info.duration_ms / 1000
    total_frames = max(1, (info.duration_ms * fps + 500) // 1000)
    target.parent.mkdir(parents=True, exist_ok=True)

    argv = [
        _ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(outro.resolve()),
    ]
    audio_input = "0:a:0"
    if not info.has_audio:
        argv += [
            "-f",
            "lavfi",
            "-t",
            f"{duration:.9f}",
            "-i",
            "anullsrc=r=48000:cl=stereo",
        ]
        audio_input = "1:a:0"
    filter_graph = (
        f"[0:v]scale=1280:720:force_original_aspect_ratio=decrease,"
        f"pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black,fps={fps},"
        "setsar=1,setpts=PTS-STARTPTS[v];"
        f"[{audio_input}]atrim=start=0:end={duration:.9f},asetpts=PTS-STARTPTS,"
        "aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo[a]"
    )
    argv += [
        "-filter_complex",
        filter_graph,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-frames:v",
        str(total_frames),
        "-t",
        f"{duration:.9f}",
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-y",
        str(target.resolve()),
    ]
    _run_ffmpeg(argv, "outro normalization", cancel_event=cancel_event, log_path=log_path)
    if not target.is_file() or target.stat().st_size == 0:
        raise CompositionError("outro normalization did not produce an output file")


def join_parts(
    lecture: Path,
    outro: Path,
    target: Path,
    *,
    cancel_event: threading.Event | None = None,
    log_path: Path | None = None,
) -> None:
    lecture = Path(lecture)
    outro = Path(outro)
    target = Path(target)
    for label, path in (("lecture", lecture), ("outro", outro)):
        if not path.is_file():
            raise CompositionError(f"{label} file does not exist: {path}")
        info = probe_media(path)
        if not info.has_audio or info.width != 1280 or info.height != 720:
            raise CompositionError(f"{label} is not a normalized 1280x720 video with audio")
    target.parent.mkdir(parents=True, exist_ok=True)
    filter_graph = (
        "[0:v]settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p[v0];"
        "[0:a]aresample=48000,asetpts=PTS-STARTPTS,"
        "aformat=sample_rates=48000:channel_layouts=stereo[a0];"
        "[1:v]settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p[v1];"
        "[1:a]aresample=48000,asetpts=PTS-STARTPTS,"
        "aformat=sample_rates=48000:channel_layouts=stereo[a1];"
        "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
    )
    argv = [
        _ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(lecture.resolve()),
        "-i",
        str(outro.resolve()),
        "-filter_complex",
        filter_graph,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-y",
        str(target.resolve()),
    ]
    _run_ffmpeg(argv, "final join", cancel_event=cancel_event, log_path=log_path)
    if not target.is_file() or target.stat().st_size == 0:
        raise CompositionError("final join did not produce an output file")
