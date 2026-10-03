from __future__ import annotations

import re
from pathlib import Path

from .batch_models import LessonInputs, LessonOutputs, LessonResult


_LESSON_RE = re.compile(r"^T(?P<number>\d+)$", re.IGNORECASE)


def _matches(files: list[Path], pattern: str) -> list[Path]:
    matcher = re.compile(pattern, re.IGNORECASE)
    return [path for path in files if matcher.fullmatch(path.name)]


def discover_lessons(root: Path) -> tuple[list[LessonInputs], list[LessonResult]]:
    root = Path(root)
    folders: list[tuple[tuple[str, ...], int, Path]] = []
    for path in root.rglob("*"):
        match = _LESSON_RE.fullmatch(path.name)
        relative_parts = path.relative_to(root).parts
        if (
            path.is_dir()
            and match
            and not any(part.lower() == "output" for part in relative_parts)
        ):
            parent_key = tuple(part.casefold() for part in relative_parts[:-1])
            folders.append((parent_key, int(match.group("number")), path))

    lessons: list[LessonInputs] = []
    errors: list[LessonResult] = []
    for _, _, folder in sorted(folders, key=lambda item: (item[0], item[1])):
        name = folder.name
        files = [path for path in folder.iterdir() if path.is_file()]
        expected = (
            ("part 1 video", rf"P1_{re.escape(name)}_+V\d+\.mp4"),
            ("part 2 video", rf"P2_{re.escape(name)}_+V\d+\.mp4"),
            ("part 1 PPTX", rf"{re.escape(name)}_1_Slide\.pptx"),
            ("part 2 PPTX", rf"{re.escape(name)}_2_Slide\.pptx"),
        )
        resolved: list[Path] = []
        problem: str | None = None
        for label, pattern in expected:
            matches = _matches(files, pattern)
            if not matches:
                problem = f"missing {label}"
                break
            if len(matches) > 1:
                problem = f"multiple {label} files"
                break
            resolved.append(matches[0])
        if problem is not None:
            errors.append(LessonResult(lesson=name, status="failed", error=problem))
            continue
        lessons.append(
            LessonInputs(
                name=name,
                root=folder,
                part1_video=resolved[0],
                part2_video=resolved[1],
                part1_pptx=resolved[2],
                part2_pptx=resolved[3],
            )
        )
    return lessons, errors


def lesson_outputs(lesson: LessonInputs) -> LessonOutputs:
    output = lesson.root / "output"
    part1_video = output / f"P1_{lesson.name}_slide.mp4"
    part2_video = output / f"P2_{lesson.name}_slide.mp4"
    final_video = output / f"{lesson.name}_final.mp4"
    return LessonOutputs(
        output_dir=output,
        merged_pptx=output / f"{lesson.name}_Slide.pptx",
        reference_dir=output / "slide-images",
        part1_timeline=output / f"P1_{lesson.name}_timeline.json",
        part2_timeline=output / f"P2_{lesson.name}_timeline.json",
        confidence_report=output / "alignment-report.json",
        part1_video=part1_video,
        part2_video=part2_video,
        final_video=final_video,
        part1_report=Path(f"{part1_video}.report.json"),
        part2_report=Path(f"{part2_video}.report.json"),
        final_report=Path(f"{final_video}.report.json"),
    )
