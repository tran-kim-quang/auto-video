from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class TimelineError(ValueError):
    """Raised when timeline metadata cannot produce an unambiguous video."""


@dataclass(frozen=True, slots=True)
class SlideCue:
    slide_id: int
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class Timeline:
    slides: tuple[SlideCue, ...]
    tail: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class FrameSpan:
    slide_id: int
    start_frame: int
    end_frame: int


_TXT_RE = re.compile(
    r"^Slide\s+(?P<id>\d+)\s*:\s*(?P<start>\d{1,2}:\d{2}(?::\d{2})?\.\d{3})"
    r"\s*-->\s*(?P<end>\d{1,2}:\d{2}(?::\d{2})?\.\d{3})\s*$",
    re.IGNORECASE,
)
_TAIL_RE = re.compile(
    r"^Outro/blank\s*:\s*(?P<start>\d{1,2}:\d{2}(?::\d{2})?\.\d{3})"
    r"\s*-->\s*(?P<end>\d{1,2}:\d{2}(?::\d{2})?\.\d{3})\s*$",
    re.IGNORECASE,
)


def _timestamp_ms(value: str) -> int:
    fields = value.split(":")
    if len(fields) == 2:
        hours = 0
        minutes, seconds_ms = fields
    elif len(fields) == 3:
        hours, minutes, seconds_ms = fields
    else:
        raise TimelineError(f"invalid timestamp: {value}")
    seconds, milliseconds = seconds_ms.split(".")
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(milliseconds)


def _integer(row: dict[str, Any], key: str, index: int) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TimelineError(f"row {index}: {key} must be an integer")
    return value


def _parse_json(path: Path) -> Timeline:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TimelineError(f"cannot read JSON timeline: {exc}") from exc
    if not isinstance(payload, list) or not payload:
        raise TimelineError("JSON timeline must be a non-empty array")

    slides: list[SlideCue] = []
    for index, raw in enumerate(payload, start=1):
        if not isinstance(raw, dict):
            raise TimelineError(f"row {index}: expected an object")
        slide_id = _integer(raw, "slide_id", index)
        start_ms = _integer(raw, "start_ms", index)
        end_ms = _integer(raw, "end_ms", index)
        if "duration_ms" in raw:
            duration_ms = _integer(raw, "duration_ms", index)
            if abs((end_ms - start_ms) - duration_ms) > 20:
                raise TimelineError(
                    f"row {index}: duration_ms differs from end_ms - start_ms by more than 20 ms"
                )
        slides.append(SlideCue(slide_id=slide_id, start_ms=start_ms, end_ms=end_ms))
    return Timeline(tuple(slides))


def _parse_txt(path: Path) -> Timeline:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise TimelineError(f"cannot read text timeline: {exc}") from exc

    slides: list[SlideCue] = []
    tail: tuple[int, int] | None = None
    for line_number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        slide_match = _TXT_RE.match(line)
        if slide_match:
            if tail is not None:
                raise TimelineError("Outro/blank must be the final row")
            slides.append(
                SlideCue(
                    slide_id=int(slide_match.group("id")),
                    start_ms=_timestamp_ms(slide_match.group("start")),
                    end_ms=_timestamp_ms(slide_match.group("end")),
                )
            )
            continue
        tail_match = _TAIL_RE.match(line)
        if tail_match:
            if tail is not None:
                raise TimelineError("timeline contains more than one Outro/blank row")
            tail = (_timestamp_ms(tail_match.group("start")), _timestamp_ms(tail_match.group("end")))
            continue
        raise TimelineError(f"line {line_number}: unsupported timeline row: {line}")
    if not slides:
        raise TimelineError("timeline contains no slide rows")
    return Timeline(tuple(slides), tail)


def parse_timeline(path: Path) -> Timeline:
    path = Path(path)
    if path.suffix.lower() == ".json":
        return _parse_json(path)
    return _parse_txt(path)


def _to_frame(milliseconds: int, fps: int) -> int:
    return (milliseconds * fps + 500) // 1000


def validate_timeline(
    timeline: Timeline,
    slide_count: int,
    audio_duration_ms: int,
    fps: int,
) -> tuple[FrameSpan, ...]:
    if fps <= 0:
        raise TimelineError("fps must be positive")
    if not timeline.slides:
        raise TimelineError("timeline contains no slide rows")
    if timeline.slides[0].start_ms != 0:
        raise TimelineError("first slide must start at 0 ms")

    normalized: list[SlideCue] = []
    prior_end: int | None = None
    for index, cue in enumerate(timeline.slides, start=1):
        if not 1 <= cue.slide_id <= slide_count:
            raise TimelineError(f"row {index}: slide_id {cue.slide_id} is not present in the PPTX")
        start_ms = cue.start_ms
        if prior_end is not None:
            difference = start_ms - prior_end
            if abs(difference) > 20:
                raise TimelineError(f"row {index}: gap or overlap is {difference} ms")
            start_ms = prior_end
        if cue.end_ms <= start_ms:
            raise TimelineError(f"row {index}: slide duration must be positive")
        normalized.append(SlideCue(cue.slide_id, start_ms, cue.end_ms))
        prior_end = cue.end_ms

    final_end = normalized[-1].end_ms
    if final_end > audio_duration_ms:
        raise TimelineError("source audio is shorter than the slide timeline")
    if timeline.tail is not None:
        tail_start, tail_end = timeline.tail
        if abs(tail_start - final_end) > 20:
            raise TimelineError("Outro/blank must begin at the final slide boundary")
        if tail_end > audio_duration_ms + 20:
            raise TimelineError("Outro/blank extends beyond source audio")
        if tail_end <= tail_start:
            raise TimelineError("Outro/blank duration must be positive")

    spans = tuple(
        FrameSpan(
            slide_id=cue.slide_id,
            start_frame=_to_frame(cue.start_ms, fps),
            end_frame=_to_frame(cue.end_ms, fps),
        )
        for cue in normalized
    )
    if any(span.end_frame <= span.start_frame for span in spans):
        raise TimelineError("a slide is shorter than one output frame")
    return spans
