from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .compose import CompositionCancelled, join_parts, normalize_outro, render_lecture
from .narration import (
    NarrationCancelled,
    NarrationResult,
    build_narration,
)
from .narration_script import load_slide_script, validate_script_slide_count
from .probe import MediaInfo, MediaProbeError, probe_media
from .slide_export import count_pptx_slides, export_slides
from .timeline import TimelineError, parse_timeline, validate_timeline
from .voicestudio_client import VoiceStudioClient, VoiceStudioConfig


class WorkflowError(RuntimeError):
    """Raised when the requested video cannot be built safely."""


class WorkflowCancelled(WorkflowError):
    """Raised when a queued narrated build is cancelled."""


@dataclass(frozen=True, slots=True)
class NarratedBuildRequest:
    pptx: Path
    script: Path
    logo: Path
    outro: Path
    artifact_directory: Path
    profile_config: VoiceStudioConfig
    fps: int = 24
    logo_width_ratio: float = 0.12
    margin_px: int = 0
    pipeline_version: str = "narrated-v1"


@dataclass(frozen=True, slots=True)
class NarratedBuildReport:
    output: str
    input_signature: dict[str, Any]
    inputs: dict[str, dict[str, str]]
    profile_config: dict[str, Any]
    slide_count: int
    slides: list[dict[str, Any]]
    total_narration_duration_ms: int
    outro_duration_ms: int
    expected_duration_ms: int
    actual_duration_ms: int
    checks: list[str]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> NarratedBuildReport:
        return cls(
            output=str(payload["output"]),
            input_signature=dict(payload["input_signature"]),
            inputs=dict(payload["inputs"]),
            profile_config=dict(payload["profile_config"]),
            slide_count=int(payload["slide_count"]),
            slides=list(payload["slides"]),
            total_narration_duration_ms=int(payload["total_narration_duration_ms"]),
            outro_duration_ms=int(payload["outro_duration_ms"]),
            expected_duration_ms=int(payload["expected_duration_ms"]),
            actual_duration_ms=int(payload["actual_duration_ms"]),
            checks=list(payload["checks"]),
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise WorkflowError(f"cannot hash input file {path}: {exc}") from exc
    return digest.hexdigest()


def _input_record(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def build_input_signature(request: NarratedBuildRequest) -> dict[str, Any]:
    inputs = {
        label: _input_record(Path(getattr(request, label)))
        for label in ("pptx", "script", "logo", "outro")
    }
    return {
        "pipeline_version": request.pipeline_version,
        "inputs": inputs,
        "profile_config": asdict(request.profile_config),
        "render": {
            "fps": request.fps,
            "logo_width_ratio": request.logo_width_ratio,
            "margin_px": request.margin_px,
            "width": 1280,
            "height": 720,
        },
    }


def _validate_request(request: NarratedBuildRequest) -> None:
    for label in ("pptx", "script", "logo", "outro"):
        path = Path(getattr(request, label))
        if not path.is_file():
            raise WorkflowError(f"{label} file does not exist: {path}")
    if request.fps <= 0:
        raise WorkflowError("fps must be positive")
    if not 0 < request.logo_width_ratio <= 1:
        raise WorkflowError("logo width ratio must be between 0 and 1")
    if request.margin_px < 0:
        raise WorkflowError("logo margin must not be negative")


def _verify_final(info: MediaInfo, expected_duration_ms: int, fps: int) -> list[str]:
    if (info.width, info.height) != (1280, 720):
        raise WorkflowError(f"final video is {info.width}x{info.height}, expected 1280x720")
    if info.fps is None or abs(float(info.fps) - fps) > 0.01:
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


def _atomic_json(path: Path, payload: object) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _load_completed_report(
    request: NarratedBuildRequest, signature: dict[str, Any]
) -> NarratedBuildReport | None:
    root = Path(request.artifact_directory)
    report_path = root / "report.json"
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("input_signature") != signature:
            return None
        report = NarratedBuildReport.from_dict(payload)
        required = [
            root / Path(report.output).name,
            report_path,
            root / "timeline.json",
            root / "combined.wav",
            root / "audio-manifest.json",
        ]
        for row in report.slides:
            required.append(root / str(row["audio_file"]))
            required.append(root / str(row["slide_image"]))
        if not all(path.is_file() and path.stat().st_size > 0 for path in required):
            return None
        _validate_retained_artifacts(root, report, request)
        final_info = probe_media(root / Path(report.output).name)
        _verify_final(final_info, report.expected_duration_ms, request.fps)
        return report
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        MediaProbeError,
        TimelineError,
        WorkflowError,
    ):
        return None


def _validate_retained_artifacts(
    root: Path, report: NarratedBuildReport, request: NarratedBuildRequest
) -> None:
    if report.slide_count <= 0 or not report.slides:
        raise WorkflowError("completed report has an invalid slide count")
    slide_ids = [int(row["slide_id"]) for row in report.slides]
    if (
        slide_ids[0] not in {1, 2}
        or slide_ids[-1] != report.slide_count
        or slide_ids != list(range(slide_ids[0], report.slide_count + 1))
    ):
        raise WorkflowError("completed report has an invalid slide range")

    timeline = parse_timeline(root / "timeline.json")
    validate_timeline(
        timeline,
        slide_count=report.slide_count,
        audio_duration_ms=report.total_narration_duration_ms,
        fps=request.fps,
    )
    expected_timeline = [
        (int(row["slide_id"]), int(row["start_ms"]), int(row["end_ms"]))
        for row in report.slides
    ]
    actual_timeline = [
        (cue.slide_id, cue.start_ms, cue.end_ms) for cue in timeline.slides
    ]
    if actual_timeline != expected_timeline:
        raise WorkflowError("completed timeline does not match report.json")

    manifest = json.loads((root / "audio-manifest.json").read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("config") != asdict(request.profile_config)
        or not isinstance(manifest.get("slides"), list)
        or len(manifest["slides"]) != len(report.slides)
    ):
        raise WorkflowError("completed audio manifest is invalid")

    for report_row, manifest_row in zip(
        report.slides, manifest["slides"], strict=True
    ):
        if not isinstance(manifest_row, dict):
            raise WorkflowError("completed audio manifest contains an invalid slide")
        slide_id = int(report_row["slide_id"])
        audio_relative = f"audio/slide_{slide_id:03d}.wav"
        image_relative = f"slide-images/slide_{slide_id:03d}.png"
        if (
            report_row.get("audio_file") != audio_relative
            or report_row.get("slide_image") != image_relative
            or manifest_row.get("slide_id") != slide_id
            or manifest_row.get("wav") != audio_relative
            or manifest_row.get("text_sha256") != report_row.get("text_sha256")
            or manifest_row.get("duration_ms") != report_row.get("duration_ms")
        ):
            raise WorkflowError("completed audio manifest does not match report.json")
        audio_path = root / audio_relative
        if (
            manifest_row.get("size") != audio_path.stat().st_size
            or manifest_row.get("content_sha256") != _sha256(audio_path)
        ):
            raise WorkflowError("completed narration WAV does not match its manifest")
        audio_info = probe_media(audio_path)
        if (
            not audio_info.has_audio
            or audio_info.duration_ms != int(manifest_row["duration_ms"])
            or audio_info.sample_rate != int(manifest_row["sample_rate"])
            or audio_info.channels != int(manifest_row["channels"])
        ):
            raise WorkflowError("completed narration WAV metadata is invalid")

    combined_info = probe_media(root / "combined.wav")
    if (
        not combined_info.has_audio
        or abs(combined_info.duration_ms - report.total_narration_duration_ms)
        > 20 * len(report.slides)
    ):
        raise WorkflowError("completed combined narration is invalid")


def _publish_artifact_tree(staging: Path, destination: Path) -> None:
    backup = destination.with_name(
        f".{destination.name}.backup-{uuid.uuid4().hex}"
    )
    had_previous = destination.exists()
    try:
        if had_previous:
            os.replace(destination, backup)
        try:
            os.replace(staging, destination)
        except Exception:
            if had_previous and backup.exists():
                os.replace(backup, destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    except OSError as exc:
        raise WorkflowError(f"cannot publish artifact directory: {exc}") from exc


def _report_slides(
    narration: NarrationResult, images: dict[int, Path]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for slide in narration.slides:
        rows.append(
            {
                "slide_id": slide.slide_id,
                "text": slide.text,
                "text_sha256": slide.text_sha256,
                "audio_file": f"audio/{slide.path.name}",
                "slide_image": f"slide-images/{images[slide.slide_id].name}",
                "start_ms": slide.start_ms,
                "end_ms": slide.end_ms,
                "duration_ms": slide.duration_ms,
                "start": _timestamp(slide.start_ms),
                "end": _timestamp(slide.end_ms),
                "generation": slide.generation_metadata,
            }
        )
    return rows


def _timestamp(milliseconds: int) -> str:
    minutes, remainder = divmod(milliseconds, 60_000)
    seconds, millis = divmod(remainder, 1000)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def build_narrated_video(
    request: NarratedBuildRequest,
    *,
    client: VoiceStudioClient,
    cache_root: Path,
    on_stage: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
    log_path: Path | None = None,
) -> NarratedBuildReport:
    request = NarratedBuildRequest(
        pptx=Path(request.pptx),
        script=Path(request.script),
        logo=Path(request.logo),
        outro=Path(request.outro),
        artifact_directory=Path(request.artifact_directory),
        profile_config=request.profile_config,
        fps=request.fps,
        logo_width_ratio=request.logo_width_ratio,
        margin_px=request.margin_px,
        pipeline_version=request.pipeline_version,
    )

    def check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise WorkflowCancelled("workflow cancelled")

    def stage(name: str) -> None:
        check_cancelled()
        if on_stage is not None:
            on_stage(name)

    staging: Path | None = None
    try:
        stage("validating")
        _validate_request(request)
        initial_signature = build_input_signature(request)
        completed = _load_completed_report(request, initial_signature)
        if completed is not None:
            return completed

        slide_count = count_pptx_slides(request.pptx)
        segments = load_slide_script(request.script)
        validate_script_slide_count(segments, slide_count)
        outro_info = probe_media(request.outro)
        if outro_info.width is None or outro_info.height is None:
            raise WorkflowError("outro does not contain a video stream")

        destination = request.artifact_directory
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.staging-", dir=destination.parent
            )
        )
        cache_key = hashlib.sha256(str(request.pptx.resolve()).encode()).hexdigest()[:16]
        stage("synthesizing_audio")
        narration = build_narration(
            segments,
            client,
            staging,
            Path(cache_root) / f"{request.pptx.stem}-{cache_key}",
            request.profile_config,
            on_progress=on_progress,
            cancel_event=cancel_event,
            log_path=log_path,
        )
        stage("assembling_narration")
        timeline = parse_timeline(narration.timeline_path)
        spans = validate_timeline(
            timeline,
            slide_count=slide_count,
            audio_duration_ms=narration.total_duration_ms,
            fps=request.fps,
        )
        stage("exporting_slides")
        images = export_slides(
            request.pptx,
            [span.slide_id for span in spans],
            staging / "slide-images",
            width=1280,
            height=720,
            cancel_event=cancel_event,
        )
        check_cancelled()
        lecture = staging / ".lecture.mp4"
        stage("rendering_lecture")
        render_lecture(
            slides=images,
            spans=spans,
            narration_audio=narration.combined_audio_path,
            logo=request.logo,
            target=lecture,
            fps=request.fps,
            logo_width_ratio=request.logo_width_ratio,
            margin_px=request.margin_px,
            cancel_event=cancel_event,
            log_path=log_path,
        )
        normalized_outro = staging / ".outro.normalized.mp4"
        stage("preparing_outro")
        normalize_outro(
            request.outro,
            normalized_outro,
            fps=request.fps,
            cancel_event=cancel_event,
            log_path=log_path,
        )
        final = staging / f"{request.pptx.stem}.mp4"
        stage("joining")
        join_parts(
            lecture,
            normalized_outro,
            final,
            cancel_event=cancel_event,
            log_path=log_path,
        )
        if not final.is_file() or final.stat().st_size == 0:
            raise WorkflowError("composition did not create the final video")
        stage("verifying")
        expected_duration = narration.total_duration_ms + outro_info.duration_ms
        final_info = probe_media(final)
        checks = _verify_final(final_info, expected_duration, request.fps)
        report = NarratedBuildReport(
            output=str((request.artifact_directory / final.name).resolve()),
            input_signature=initial_signature,
            inputs=dict(initial_signature["inputs"]),
            profile_config=asdict(request.profile_config),
            slide_count=slide_count,
            slides=_report_slides(narration, images),
            total_narration_duration_ms=narration.total_duration_ms,
            outro_duration_ms=outro_info.duration_ms,
            expected_duration_ms=expected_duration,
            actual_duration_ms=final_info.duration_ms,
            checks=checks,
        )
        lecture.unlink(missing_ok=True)
        normalized_outro.unlink(missing_ok=True)
        _atomic_json(staging / "report.json", asdict(report))
        check_cancelled()
        if build_input_signature(request) != initial_signature:
            raise WorkflowError("inputs changed while job was running")
        stage("publishing")
        _publish_artifact_tree(staging, request.artifact_directory)
        staging = None
        return report
    except (NarrationCancelled, CompositionCancelled, WorkflowCancelled) as exc:
        raise WorkflowCancelled(str(exc)) from exc
    except WorkflowError:
        raise
    except Exception as exc:
        if cancel_event is not None and cancel_event.is_set():
            raise WorkflowCancelled("workflow cancelled") from exc
        raise WorkflowError(str(exc)) from exc
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
