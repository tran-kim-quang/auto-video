from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .batch_discovery import discover_lessons, lesson_outputs
from .batch_models import LessonInputs, LessonResult
from .merge_pipeline import MergeRequest, merge_videos
from .pipeline import BuildRequest, build_video
from .pptx_merge import merge_pptx_as_images
from .powerpoint import count_pptx_slides
from .probe import probe_media
from .slide_alignment import AlignmentConfig, align_video_to_slides
from .timeline import parse_timeline, validate_timeline


@dataclass(frozen=True, slots=True)
class BatchRequest:
    source_root: Path
    assets_dir: Path
    fps: int = 24


@dataclass(frozen=True, slots=True)
class BatchReport:
    results: list[LessonResult]

    @property
    def failed(self) -> int:
        return sum(item.status == "failed" for item in self.results)


def _valid_video(path: Path, fps: int) -> bool:
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    try:
        info = probe_media(path)
    except Exception:
        return False
    return (
        (info.width, info.height) == (1280, 720)
        and info.fps == Fraction(fps, 1)
        and info.has_audio
        and info.video_codec == "h264"
        and info.audio_codec == "aac"
    )


def _valid_pptx(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    try:
        return count_pptx_slides(path) == 40
    except Exception:
        return False


def _valid_timeline(path: Path, source: Path, allowed: range, fps: int) -> bool:
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    try:
        timeline = parse_timeline(path)
        if any(cue.slide_id not in allowed for cue in timeline.slides):
            return False
        info = probe_media(source)
        if not info.has_audio or info.audio_duration_ms is None:
            return False
        validate_timeline(timeline, 40, info.audio_duration_ms, fps)
        return True
    except Exception:
        return False


def _remove_video_artifact(path: Path) -> None:
    path.unlink(missing_ok=True)
    Path(f"{path}.report.json").unlink(missing_ok=True)


def _reference_images(directory: Path) -> tuple[Path, ...]:
    return tuple(directory / f"slide-{number:02d}.png" for number in range(1, 41))


def _write_combined_alignment_report(output: Path, part_reports: list[Path]) -> None:
    payload: dict[str, object] = {}
    for index, report in enumerate(part_reports, start=1):
        if report.is_file():
            payload[f"part{index}"] = json.loads(report.read_text(encoding="utf-8"))
    if not payload:
        return
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)


def _run_lesson(
    lesson: LessonInputs,
    request: BatchRequest,
    logo: Path,
    outro: Path,
) -> LessonResult:
    outputs = lesson_outputs(lesson)
    if _valid_video(outputs.final_video, request.fps):
        return LessonResult(lesson.name, "skipped", outputs.final_video)

    outputs.output_dir.mkdir(parents=True, exist_ok=True)
    if outputs.final_video.exists():
        _remove_video_artifact(outputs.final_video)

    references = _reference_images(outputs.reference_dir)
    if not _valid_pptx(outputs.merged_pptx):
        outputs.merged_pptx.unlink(missing_ok=True)
        if outputs.reference_dir.exists():
            shutil.rmtree(outputs.reference_dir)
        references = merge_pptx_as_images(
            lesson.part1_pptx,
            lesson.part2_pptx,
            outputs.merged_pptx,
            outputs.reference_dir,
        )
    elif not all(path.is_file() and path.stat().st_size > 0 for path in references):
        references = merge_pptx_as_images(
            lesson.part1_pptx,
            lesson.part2_pptx,
            outputs.merged_pptx,
            outputs.reference_dir,
        )

    part_reports = [
        outputs.output_dir / ".P1_alignment.json",
        outputs.output_dir / ".P2_alignment.json",
    ]
    config = AlignmentConfig()
    if not _valid_timeline(outputs.part1_timeline, lesson.part1_video, range(1, 21), request.fps):
        outputs.part1_timeline.unlink(missing_ok=True)
        align_video_to_slides(
            lesson.part1_video,
            references[:20],
            list(range(1, 21)),
            outputs.part1_timeline,
            part_reports[0],
            config,
        )
    if not _valid_timeline(outputs.part2_timeline, lesson.part2_video, range(21, 41), request.fps):
        outputs.part2_timeline.unlink(missing_ok=True)
        align_video_to_slides(
            lesson.part2_video,
            references[20:],
            list(range(21, 41)),
            outputs.part2_timeline,
            part_reports[1],
            config,
        )
    _write_combined_alignment_report(outputs.confidence_report, part_reports)

    if not _valid_video(outputs.part1_video, request.fps):
        _remove_video_artifact(outputs.part1_video)
        build_video(
            BuildRequest(
                source_media=lesson.part1_video,
                pptx=outputs.merged_pptx,
                timeline=outputs.part1_timeline,
                logo=logo,
                outro=None,
                output=outputs.part1_video,
                fps=request.fps,
            )
        )
    if not _valid_video(outputs.part2_video, request.fps):
        _remove_video_artifact(outputs.part2_video)
        build_video(
            BuildRequest(
                source_media=lesson.part2_video,
                pptx=outputs.merged_pptx,
                timeline=outputs.part2_timeline,
                logo=logo,
                outro=outro,
                output=outputs.part2_video,
                fps=request.fps,
            )
        )

    merge_videos(
        MergeRequest(
            first_video=outputs.part1_video,
            second_video=outputs.part2_video,
            output=outputs.final_video,
            fps=request.fps,
        )
    )
    if not _valid_video(outputs.final_video, request.fps):
        raise RuntimeError("final video failed media validation")
    return LessonResult(lesson.name, "completed", outputs.final_video)


def run_batch(request: BatchRequest) -> BatchReport:
    request = BatchRequest(Path(request.source_root), Path(request.assets_dir), request.fps)
    lessons, discovery_errors = discover_lessons(request.source_root)
    results = list(discovery_errors)
    logo = request.assets_dir / "logo.png"
    outro = request.assets_dir / "Outro720.mp4"
    for lesson in lessons:
        try:
            if not logo.is_file():
                raise FileNotFoundError(f"missing logo: {logo}")
            if not outro.is_file():
                raise FileNotFoundError(f"missing outro: {outro}")
            results.append(_run_lesson(lesson, request, logo, outro))
        except Exception as exc:
            results.append(LessonResult(lesson.name, "failed", error=str(exc)))
    results.sort(key=lambda item: int(item.lesson[1:]) if item.lesson[1:].isdigit() else item.lesson)
    return BatchReport(results)

