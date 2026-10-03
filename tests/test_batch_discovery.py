from __future__ import annotations

from pathlib import Path

from video_workflow.batch_discovery import discover_lessons, lesson_outputs


def _touch_lesson(root: Path, name: str) -> Path:
    lesson = root / name
    lesson.mkdir()
    for filename in (
        f"P1_{name}_V8.mp4",
        f"P2_{name}_V8.mp4",
        f"{name}_1_Slide.pptx",
        f"{name}_2_Slide.pptx",
    ):
        (lesson / filename).touch()
    return lesson


def test_discovers_complete_lessons_in_numeric_order(tmp_path: Path) -> None:
    for name in ("T10", "T8", "T9"):
        _touch_lesson(tmp_path, name)
    (tmp_path / "notes").mkdir()

    lessons, errors = discover_lessons(tmp_path)

    assert errors == []
    assert [lesson.name for lesson in lessons] == ["T8", "T9", "T10"]
    assert lessons[0].part1_video == tmp_path / "T8" / "P1_T8_V8.mp4"
    assert lessons[0].part2_video == tmp_path / "T8" / "P2_T8_V8.mp4"
    assert lessons[0].part1_pptx == tmp_path / "T8" / "T8_1_Slide.pptx"
    assert lessons[0].part2_pptx == tmp_path / "T8" / "T8_2_Slide.pptx"


def test_output_paths_are_deterministic_and_beneath_lesson_output(tmp_path: Path) -> None:
    lesson_root = _touch_lesson(tmp_path, "T8")
    lesson = discover_lessons(tmp_path)[0][0]

    outputs = lesson_outputs(lesson)

    assert outputs.output_dir == lesson_root / "output"
    assert outputs.merged_pptx == lesson_root / "output" / "T8_Slide.pptx"
    assert outputs.reference_dir == lesson_root / "output" / "slide-images"
    assert outputs.part1_timeline == lesson_root / "output" / "P1_T8_timeline.json"
    assert outputs.part2_timeline == lesson_root / "output" / "P2_T8_timeline.json"
    assert outputs.confidence_report == lesson_root / "output" / "alignment-report.json"
    assert outputs.part1_video == lesson_root / "output" / "P1_T8_slide.mp4"
    assert outputs.part2_video == lesson_root / "output" / "P2_T8_slide.mp4"
    assert outputs.final_video == lesson_root / "output" / "T8_final.mp4"


def test_missing_input_is_reported_without_hiding_valid_sibling(tmp_path: Path) -> None:
    _touch_lesson(tmp_path, "T8")
    broken = _touch_lesson(tmp_path, "T9")
    (broken / "P2_T9_V8.mp4").unlink()

    lessons, errors = discover_lessons(tmp_path)

    assert [lesson.name for lesson in lessons] == ["T8"]
    assert [(error.lesson, error.status) for error in errors] == [("T9", "failed")]
    assert "part 2 video" in (errors[0].error or "")


def test_case_insensitive_duplicate_is_rejected(tmp_path: Path) -> None:
    lesson = _touch_lesson(tmp_path, "T8")
    (lesson / "p1_t8_v8.MP4").touch()

    lessons, errors = discover_lessons(tmp_path)

    assert lessons == []
    assert len(errors) == 1
    assert "multiple part 1 video" in (errors[0].error or "")


def test_discovery_does_not_recurse_into_output(tmp_path: Path) -> None:
    lesson = _touch_lesson(tmp_path, "T8")
    output = lesson / "output"
    output.mkdir()
    (output / "P1_T8_V8.mp4").touch()

    lessons, errors = discover_lessons(tmp_path)

    assert len(lessons) == 1
    assert errors == []
