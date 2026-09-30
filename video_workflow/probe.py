from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any


class MediaProbeError(RuntimeError):
    """Raised when media metadata cannot be read or is incomplete."""


@dataclass(frozen=True, slots=True)
class MediaInfo:
    duration_ms: int
    width: int | None
    height: int | None
    fps: Fraction | None
    audio_duration_ms: int | None
    has_audio: bool
    video_codec: str | None = None
    audio_codec: str | None = None


def _milliseconds(value: Any) -> int | None:
    if value in (None, "N/A", ""):
        return None
    try:
        return round(float(value) * 1000)
    except (TypeError, ValueError) as exc:
        raise MediaProbeError(f"invalid duration reported by ffprobe: {value!r}") from exc


def _frame_rate(value: Any) -> Fraction | None:
    if not value or value == "0/0":
        return None
    try:
        rate = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise MediaProbeError(f"invalid frame rate reported by ffprobe: {value!r}") from exc
    return rate if rate > 0 else None


def probe_media(path: Path) -> MediaInfo:
    path = Path(path)
    if not path.is_file():
        raise MediaProbeError(f"media file does not exist: {path}")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise MediaProbeError("ffprobe is not available on PATH")
    argv = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,width,height,avg_frame_rate,duration",
        "-of",
        "json",
        str(path.resolve()),
    ]
    try:
        completed = subprocess.run(argv, check=True, capture_output=True, text=True, shell=False)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise MediaProbeError(f"ffprobe failed for {path}: {detail.strip()}") from exc
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise MediaProbeError(f"ffprobe returned invalid JSON for {path}") from exc

    streams = payload.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    format_duration = _milliseconds(payload.get("format", {}).get("duration"))
    if format_duration is None:
        durations = [_milliseconds(stream.get("duration")) for stream in streams]
        format_duration = max((value for value in durations if value is not None), default=None)
    if format_duration is None or format_duration <= 0:
        raise MediaProbeError(f"ffprobe did not report a positive duration for {path}")

    width = int(video["width"]) if video and video.get("width") is not None else None
    height = int(video["height"]) if video and video.get("height") is not None else None
    fps = _frame_rate(video.get("avg_frame_rate")) if video else None
    audio_duration = _milliseconds(audio.get("duration")) if audio else None
    if audio is not None and audio_duration is None:
        audio_duration = format_duration
    return MediaInfo(
        duration_ms=format_duration,
        width=width,
        height=height,
        fps=fps,
        audio_duration_ms=audio_duration,
        has_audio=audio is not None,
        video_codec=str(video.get("codec_name")) if video and video.get("codec_name") else None,
        audio_codec=str(audio.get("codec_name")) if audio and audio.get("codec_name") else None,
    )
