from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from collections.abc import Sequence

from PIL import Image, ImageChops, ImageOps, ImageStat

from .probe import probe_media
from .timeline import SlideCue, Timeline, validate_timeline


class AlignmentError(RuntimeError):
    """Raised when video frames cannot produce a trustworthy timeline."""


@dataclass(frozen=True, slots=True)
class AlignmentConfig:
    sample_interval_ms: int = 1000
    refine_interval_ms: int = 100
    min_stable_ms: int = 1500
    min_confidence: float = 0.70


@dataclass(frozen=True, slots=True)
class SampleMatch:
    timestamp_ms: int
    slide_index: int
    confidence: float


@dataclass(frozen=True, slots=True)
class AlignmentReport:
    video: str
    timeline_path: str
    slide_ids: list[int]
    duration_ms: int
    average_confidence: float
    minimum_confidence: float
    samples: list[dict[str, int | float]]


def _normalized(image: Image.Image) -> Image.Image:
    grayscale = image.convert("L")
    width, height = grayscale.size
    border_x = max(1, round(width * 0.02))
    border_y = max(1, round(height * 0.02))
    if width > border_x * 2 and height > border_y * 2:
        grayscale = grayscale.crop((border_x, border_y, width - border_x, height - border_y))
    return ImageOps.autocontrast(grayscale.resize((160, 90), Image.Resampling.LANCZOS))


def _normalized_similarity(left: Image.Image, right: Image.Image) -> float:
    difference = ImageChops.difference(left, right)
    mean_difference = ImageStat.Stat(difference).mean[0]
    return max(0.0, min(1.0, 1.0 - mean_difference / 255.0))


def _notebook_slide_region(image: Image.Image) -> Image.Image | None:
    width, height = image.size
    if width < 640 or height < 360 or abs(width / height - 16 / 9) > 0.05:
        return None
    return image.crop(
        (
            round(width * 0.2328),
            round(height * 0.1417),
            round(width * 0.7672),
            round(height * 0.6722),
        )
    )


def _longest_run(indices: Sequence[int]) -> tuple[int, int] | None:
    if not indices:
        return None
    best = (indices[0], indices[0])
    start = prior = indices[0]
    for value in indices[1:]:
        if value != prior + 1:
            if prior - start > best[1] - best[0]:
                best = (start, prior)
            start = value
        prior = value
    if prior - start > best[1] - best[0]:
        best = (start, prior)
    return best


def _detected_dark_slide_region(image: Image.Image) -> Image.Image | None:
    width, height = image.size
    if width < 640 or height < 360:
        return None
    sample = image.convert("L").resize((160, 90), Image.Resampling.BILINEAR)
    pixels = sample.load()
    dark = [[pixels[x, y] < 175 for x in range(160)] for y in range(90)]
    rows = [y for y in range(90) if sum(dark[y]) >= 24]
    row_run = _longest_run(rows)
    if row_run is None or row_run[1] - row_run[0] < 18:
        return None
    top, bottom = row_run
    run_height = bottom - top + 1
    columns = [
        x
        for x in range(160)
        if sum(dark[y][x] for y in range(top, bottom + 1)) >= run_height * 0.55
    ]
    column_run = _longest_run(columns)
    if column_run is None or column_run[1] - column_run[0] < 45:
        return None
    left, right = column_run
    crop = (
        max(0, math.floor(left * width / 160)),
        max(0, math.floor(top * height / 90)),
        min(width, math.ceil((right + 1) * width / 160)),
        min(height, math.ceil((bottom + 1) * height / 90)),
    )
    return image.crop(crop)


def _candidate_views(image: Image.Image) -> tuple[Image.Image, ...]:
    views = [image]
    for embedded in (_notebook_slide_region(image), _detected_dark_slide_region(image)):
        if embedded is not None:
            views.append(embedded)
    return tuple(views)


def image_similarity(left: Image.Image, right: Image.Image) -> float:
    normalized_right = _normalized(right)
    return max(
        _normalized_similarity(_normalized(view), normalized_right)
        for view in _candidate_views(left)
    )


def monotonic_matches(
    frames: Sequence[Image.Image], slides: Sequence[Image.Image]
) -> list[SampleMatch]:
    if not frames or not slides:
        raise AlignmentError("alignment requires at least one frame and one slide")
    normalized_slides = [_normalized(slide) for slide in slides]
    normalized_frames = [
        [_normalized(view) for view in _candidate_views(frame)] for frame in frames
    ]
    score_rows = [
        [
            max(_normalized_similarity(view, slide) for view in frame_views)
            for slide in normalized_slides
        ]
        for frame_views in normalized_frames
    ]
    previous = score_rows[0][:]
    parents: list[list[int]] = [[-1] * len(slides)]
    transition_penalty = 0.015
    for row in score_rows[1:]:
        current: list[float] = []
        row_parents: list[int] = []
        best_score = previous[0]
        best_index = 0
        for slide_index, score in enumerate(row):
            candidate = previous[slide_index]
            if candidate > best_score:
                best_score = candidate
                best_index = slide_index
            penalty = transition_penalty if best_index != slide_index else 0.0
            current.append(score + best_score - penalty)
            row_parents.append(best_index)
        previous = current
        parents.append(row_parents)

    selected = [0] * len(frames)
    selected[-1] = max(range(len(slides)), key=previous.__getitem__)
    for frame_index in range(len(frames) - 1, 0, -1):
        selected[frame_index - 1] = parents[frame_index][selected[frame_index]]
    return [
        SampleMatch(index, slide_index, score_rows[index][slide_index])
        for index, slide_index in enumerate(selected)
    ]


def debounce_matches(
    matches: Sequence[SampleMatch], min_stable_ms: int
) -> list[SampleMatch]:
    cleaned = list(matches)
    if len(cleaned) < 3:
        return cleaned
    changed = True
    while changed:
        changed = False
        run_start = 0
        while run_start < len(cleaned):
            run_end = run_start + 1
            while (
                run_end < len(cleaned)
                and cleaned[run_end].slide_index == cleaned[run_start].slide_index
            ):
                run_end += 1
            if 0 < run_start and run_end < len(cleaned):
                duration = cleaned[run_end].timestamp_ms - cleaned[run_start].timestamp_ms
                left = cleaned[run_start - 1].slide_index
                right = cleaned[run_end].slide_index
                if duration < min_stable_ms and left == right:
                    cleaned[run_start:run_end] = [
                        SampleMatch(item.timestamp_ms, left, item.confidence)
                        for item in cleaned[run_start:run_end]
                    ]
                    changed = True
                    break
            run_start = run_end
    return cleaned


def sample_video_frames(
    video: Path,
    duration_ms: int,
    interval_ms: int,
    target_dir: Path,
    *,
    start_ms: int = 0,
    end_ms: int | None = None,
) -> list[tuple[int, Image.Image]]:
    if interval_ms <= 0:
        raise AlignmentError("frame sampling interval must be positive")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AlignmentError("ffmpeg is not available on PATH")
    final_ms = min(duration_ms, end_ms if end_ms is not None else duration_ms)
    if final_ms <= start_ms:
        return []
    target_dir.mkdir(parents=True, exist_ok=True)
    count = max(1, math.ceil((final_ms - start_ms) / interval_ms))
    pattern = target_dir / "frame-%06d.png"
    argv = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if start_ms:
        argv += ["-ss", f"{start_ms / 1000:.3f}"]
    argv += [
        "-i", str(Path(video)),
        "-t", f"{(final_ms - start_ms) / 1000:.3f}",
        "-vf", f"fps=1000/{interval_ms}",
        "-frames:v", str(count),
        "-y", str(pattern),
    ]
    try:
        subprocess.run(argv, check=True, capture_output=True, text=True, shell=False)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise AlignmentError(f"ffmpeg frame extraction failed: {detail.strip()}") from exc
    frames: list[tuple[int, Image.Image]] = []
    for index, path in enumerate(sorted(target_dir.glob("frame-*.png"))):
        timestamp = min(start_ms + index * interval_ms, duration_ms - 1)
        with Image.open(path) as image:
            frames.append((timestamp, image.convert("RGB").copy()))
    if not frames:
        raise AlignmentError("ffmpeg did not extract any video frames")
    return frames


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def align_video_to_slides(
    video: Path,
    slide_images: Sequence[Path],
    slide_ids: Sequence[int],
    timeline_path: Path,
    report_path: Path,
    config: AlignmentConfig = AlignmentConfig(),
) -> AlignmentReport:
    if len(slide_images) != len(slide_ids) or not slide_ids:
        raise AlignmentError("slide images and slide IDs must be non-empty and equal in length")
    if list(slide_ids) != sorted(set(slide_ids)):
        raise AlignmentError("slide IDs must be strictly increasing")
    info = probe_media(Path(video))
    if not info.has_audio or info.audio_duration_ms is None:
        raise AlignmentError("source video must contain audio")
    duration_ms = info.audio_duration_ms
    slides: list[Image.Image] = []
    for path in slide_images:
        with Image.open(path) as image:
            slides.append(image.convert("RGB").copy())

    with tempfile.TemporaryDirectory(prefix="slide-alignment-") as temporary:
        temporary_path = Path(temporary)
        sampled = sample_video_frames(
            Path(video), duration_ms, config.sample_interval_ms, temporary_path / "coarse"
        )
        coarse_matches = monotonic_matches([image for _, image in sampled], slides)
        transition_windows = [
            (sampled[index - 1][0], sampled[index][0])
            for index in range(1, len(sampled))
            if coarse_matches[index - 1].slide_index != coarse_matches[index].slide_index
        ]
        for window_index, (window_start, window_end) in enumerate(transition_windows):
            refined = sample_video_frames(
                Path(video),
                duration_ms,
                config.refine_interval_ms,
                temporary_path / f"refine-{window_index}",
                start_ms=window_start,
                end_ms=min(duration_ms, window_end + config.refine_interval_ms),
            )
            sampled = [
                item for item in sampled if not window_start <= item[0] <= window_end
            ]
            sampled.extend(refined)
        sampled = sorted({timestamp: image for timestamp, image in sampled}.items())
    raw = monotonic_matches([image for _, image in sampled], slides)
    timestamped = [
        SampleMatch(timestamp, match.slide_index, match.confidence)
        for (timestamp, _), match in zip(sampled, raw, strict=True)
    ]
    cleaned = debounce_matches(timestamped, config.min_stable_ms)
    average = sum(item.confidence for item in cleaned) / len(cleaned)
    minimum = min(item.confidence for item in cleaned)
    if average < config.min_confidence:
        raise AlignmentError(
            f"alignment confidence {average:.3f} is below {config.min_confidence:.3f}"
        )

    cues: list[SlideCue] = []
    start = 0
    active = cleaned[0].slide_index
    for item in cleaned[1:]:
        if item.slide_index == active:
            continue
        if item.timestamp_ms > start:
            cues.append(SlideCue(slide_ids[active], start, item.timestamp_ms))
        start = item.timestamp_ms
        active = item.slide_index
    cues.append(SlideCue(slide_ids[active], start, duration_ms))
    if any(cue.end_ms <= cue.start_ms for cue in cues):
        raise AlignmentError("alignment produced a non-positive slide duration")
    timeline = Timeline(tuple(cues))
    validate_timeline(
        timeline,
        slide_count=max(slide_ids),
        audio_duration_ms=duration_ms,
        fps=24,
    )
    timeline_payload = [
        {
            "slide_id": cue.slide_id,
            "start_ms": cue.start_ms,
            "end_ms": cue.end_ms,
            "duration_ms": cue.end_ms - cue.start_ms,
        }
        for cue in cues
    ]
    report = AlignmentReport(
        video=str(Path(video).resolve()),
        timeline_path=str(Path(timeline_path).resolve()),
        slide_ids=list(slide_ids),
        duration_ms=duration_ms,
        average_confidence=round(average, 6),
        minimum_confidence=round(minimum, 6),
        samples=[
            {
                "timestamp_ms": item.timestamp_ms,
                "slide_id": slide_ids[item.slide_index],
                "confidence": round(item.confidence, 6),
            }
            for item in cleaned
        ],
    )
    _atomic_json(Path(timeline_path), timeline_payload)
    _atomic_json(Path(report_path), asdict(report))
    return report
