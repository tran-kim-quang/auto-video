from __future__ import annotations

import json
import tempfile
import threading
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from collections.abc import Callable

from .compose import CompositionCancelled, join_parts, normalize_outro, render_lecture
from .slide_export import count_pptx_slides, export_slides
from .probe import MediaInfo, probe_media
from .timeline import parse_timeline, validate_timeline


class WorkflowError(RuntimeError):
    """Raised when the requested video cannot be built safely."""


class WorkflowCancelled(WorkflowError):
    """Raised when a queued build is cancelled."""


@dataclass(frozen=True, slots=True)
class BuildRequest:
    source_media: Path
    pptx: Path
    timeline: Path
    logo: Path | None
    outro: Path | None
    output: Path
    fps: int = 24
    logo_width_ratio: float = 0.12
    margin_px: int = 0


@dataclass(frozen=True, slots=True)
class BuildReport:
    output: str
    inputs: dict[str, str | None]
    slide_count: int
    lecture_end_ms: int
    source_audio_duration_ms: int
    trimmed_source_tail_ms: int
    outro_duration_ms: int
    expected_duration_ms: int
    actual_duration_ms: int
    checks: list[str]


def _validate_paths(request: BuildRequest) -> None:
    for label in ("source_media", "pptx", "timeline"):
        path = Path(getattr(request, label))
        if not path.is_file():
            raise WorkflowError(f"{label} file does not exist: {path}")
    for label in ("logo", "outro"):
        path = getattr(request, label)
        if path is not None and not Path(path).is_file():
            raise WorkflowError(f"{label} file does not exist: {path}")
    if request.output.exists():
        raise WorkflowError(f"output already exists: {request.output}")
    report_path = Path(f"{request.output}.report.json")
    if report_path.exists():
        raise WorkflowError(f"report already exists: {report_path}")
    if request.fps <= 0:
        raise WorkflowError("fps must be positive")
    if request.logo is not None:
        if not 0 < request.logo_width_ratio <= 1:
            raise WorkflowError("logo width ratio must be between 0 and 1")
        if request.margin_px < 0:
            raise WorkflowError("logo margin must not be negative")


def _verify_final(info: MediaInfo, expected_duration_ms: int, fps: int) -> list[str]:
    if (info.width, info.height) != (1280, 720):
        raise WorkflowError(f"final video is {info.width}x{info.height}, expected 1280x720")
    if info.fps != Fraction(fps, 1):
        raise WorkflowError(f"final video is {info.fps} fps, expected {fps} fps")
    if not info.has_audio or info.video_codec != "h264" or info.audio_codec != "aac":
        raise WorkflowError("final video must contain H.264 video and AAC audio")
    tolerance_ms = round(1000 / fps) + 50
    if abs(info.duration_ms - expected_duration_ms) > tolerance_ms:
        raise WorkflowError(
            f"final duration is {info.duration_ms} ms, expected {expected_duration_ms} ms "
            f"(tolerance {tolerance_ms} ms)"
        )
    return ["video_1280x720", f"video_{fps}fps", "h264_aac_streams", "duration"]


def build_video(
    request: BuildRequest,
    *,
    on_stage: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    log_path: Path | None = None,
) -> BuildReport:
    request = BuildRequest(
        source_media=Path(request.source_media),
        pptx=Path(request.pptx),
        timeline=Path(request.timeline),
        logo=Path(request.logo) if request.logo is not None else None,
        outro=Path(request.outro) if request.outro is not None else None,
        output=Path(request.output),
        fps=request.fps,
        logo_width_ratio=request.logo_width_ratio,
        margin_px=request.margin_px,
    )
    def check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise WorkflowCancelled("workflow cancelled")

    def stage(name: str) -> None:
        check_cancelled()
        if on_stage is not None:
            on_stage(name)

    stage("validating")
    _validate_paths(request)
    try:
        source_info = probe_media(request.source_media)
        if not source_info.has_audio or source_info.audio_duration_ms is None:
            raise WorkflowError("source video does not contain an audio stream")
        outro_duration_ms = 0
        if request.outro is not None:
            outro_info = probe_media(request.outro)
            if outro_info.width is None or outro_info.height is None:
                raise WorkflowError("outro does not contain a video stream")
            outro_duration_ms = outro_info.duration_ms
        slide_count = count_pptx_slides(request.pptx)
        timeline = parse_timeline(request.timeline)
        spans = validate_timeline(
            timeline,
            slide_count=slide_count,
            audio_duration_ms=source_info.audio_duration_ms,
            fps=request.fps,
        )
        lecture_end_ms = timeline.slides[-1].end_ms
        expected_duration_ms = lecture_end_ms + outro_duration_ms
        request.output.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="slide-video-", dir=request.output.parent) as temporary:
            staging = Path(temporary)
            stage("exporting_slides")
            images = export_slides(
                request.pptx,
                [span.slide_id for span in spans],
                staging / "slides",
                width=1280,
                height=720,
                cancel_event=cancel_event,
            )
            check_cancelled()
            lecture = staging / "lecture.mp4"
            staged_final = lecture
            stage("rendering_lecture")
            render_lecture(
                images,
                spans,
                request.source_media,
                request.logo,
                lecture,
                fps=request.fps,
                logo_width_ratio=request.logo_width_ratio,
                margin_px=request.margin_px,
                cancel_event=cancel_event,
                log_path=log_path,
            )
            if request.outro is not None:
                normalized_outro = staging / "outro.mp4"
                staged_final = staging / "final.mp4"
                stage("preparing_outro")
                normalize_outro(
                    request.outro, normalized_outro, fps=request.fps,
                    cancel_event=cancel_event, log_path=log_path,
                )
                stage("joining")
                join_parts(
                    lecture, normalized_outro, staged_final,
                    cancel_event=cancel_event, log_path=log_path,
                )
            if not staged_final.is_file():
                raise WorkflowError("composition did not create the staged final video")
            stage("verifying")
            final_info = probe_media(staged_final)
            checks = _verify_final(final_info, expected_duration_ms, request.fps)
            report = BuildReport(
                output=str(request.output.resolve()),
                inputs={
                    "source_media": str(request.source_media.resolve()),
                    "pptx": str(request.pptx.resolve()),
                    "timeline": str(request.timeline.resolve()),
                    "logo": str(request.logo.resolve()) if request.logo is not None else None,
                    "outro": str(request.outro.resolve()) if request.outro is not None else None,
                },
                slide_count=len(timeline.slides),
                lecture_end_ms=lecture_end_ms,
                source_audio_duration_ms=source_info.audio_duration_ms,
                trimmed_source_tail_ms=max(0, source_info.audio_duration_ms - lecture_end_ms),
                outro_duration_ms=outro_duration_ms,
                expected_duration_ms=expected_duration_ms,
                actual_duration_ms=final_info.duration_ms,
                checks=checks,
            )
            staged_report = staging / "report.json"
            staged_report.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            staged_final.replace(request.output)
            staged_report.replace(Path(f"{request.output}.report.json"))
            return report
    except (WorkflowCancelled, CompositionCancelled) as exc:
        raise WorkflowCancelled(str(exc)) from exc
    except WorkflowError:
        raise
    except Exception as exc:
        if cancel_event is not None and cancel_event.is_set():
            raise WorkflowCancelled("workflow cancelled") from exc
        raise WorkflowError(str(exc)) from exc
