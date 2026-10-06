from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LessonInputs:
    name: str
    root: Path
    part1_video: Path
    part2_video: Path
    part1_pptx: Path
    part2_pptx: Path


@dataclass(frozen=True, slots=True)
class LessonOutputs:
    output_dir: Path
    merged_pptx: Path
    reference_dir: Path
    part1_timeline: Path
    part2_timeline: Path
    confidence_report: Path
    part1_video: Path
    part2_video: Path
    final_video: Path
    part1_report: Path
    part2_report: Path
    final_report: Path


@dataclass(frozen=True, slots=True)
class LessonResult:
    lesson: str
    status: str
    output: Path | None = None
    error: str | None = None

