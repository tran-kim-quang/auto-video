from __future__ import annotations

import json
import tempfile
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from .compose import CompositionCancelled, join_parts, normalize_video
from .pipeline import WorkflowCancelled, WorkflowError, _publish_video, _verify_final
from .probe import probe_media


@dataclass(frozen=True, slots=True)
class MergeRequest:
    first_video: Path
    second_video: Path
    output: Path
    fps: int = 24


@dataclass(frozen=True, slots=True)
class MergeReport:
    output: str
    inputs: dict[str, str]
    first_duration_ms: int
    second_duration_ms: int
    expected_duration_ms: int
    actual_duration_ms: int
    checks: list[str]


def _validate_paths(request: MergeRequest) -> None:
    for label in ("first_video", "second_video"):
        path = Path(getattr(request, label))
        if not path.is_file():
            raise WorkflowError(f"{label} file does not exist: {path}")
    if request.output.exists():
        raise WorkflowError(f"output already exists: {request.output}")
    report_path = Path(f"{request.output}.report.json")
    if report_path.exists():
        raise WorkflowError(f"report already exists: {report_path}")
    if request.fps <= 0:
        raise WorkflowError("fps must be positive")


def merge_videos(
    request: MergeRequest,
    *,
    on_stage: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    log_path: Path | None = None,
) -> MergeReport:
    request = MergeRequest(
        first_video=Path(request.first_video),
        second_video=Path(request.second_video),
        output=Path(request.output),
        fps=request.fps,
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
        first_info = probe_media(request.first_video)
        second_info = probe_media(request.second_video)
        for label, info in (("first_video", first_info), ("second_video", second_info)):
            if info.width is None or info.height is None:
                raise WorkflowError(f"{label} does not contain a video stream")
        expected_duration_ms = first_info.duration_ms + second_info.duration_ms
        request.output.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(
            prefix="merge-video-", dir=request.output.parent
        ) as temporary:
            staging = Path(temporary)
            normalized_first = staging / "first.mp4"
            normalized_second = staging / "second.mp4"
            staged_final = staging / "final.mp4"

            stage("normalizing_first")
            normalize_video(
                request.first_video,
                normalized_first,
                fps=request.fps,
                cancel_event=cancel_event,
                log_path=log_path,
            )
            stage("normalizing_second")
            normalize_video(
                request.second_video,
                normalized_second,
                fps=request.fps,
                cancel_event=cancel_event,
                log_path=log_path,
            )
            stage("joining")
            join_parts(
                normalized_first,
                normalized_second,
                staged_final,
                cancel_event=cancel_event,
                log_path=log_path,
            )
            stage("verifying")
            final_info = probe_media(staged_final)
            checks = _verify_final(final_info, expected_duration_ms, request.fps)
            report = MergeReport(
                output=str(request.output.resolve()),
                inputs={
                    "first_video": str(request.first_video.resolve()),
                    "second_video": str(request.second_video.resolve()),
                },
                first_duration_ms=first_info.duration_ms,
                second_duration_ms=second_info.duration_ms,
                expected_duration_ms=expected_duration_ms,
                actual_duration_ms=final_info.duration_ms,
                checks=checks,
            )
            staged_report = staging / "report.json"
            staged_report.write_text(
                json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            check_cancelled()
            _publish_video(staged_final, request.output, overwrite=False)
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
