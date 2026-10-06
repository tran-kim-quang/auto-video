from __future__ import annotations

import os
from pathlib import Path

import pytest

from video_workflow.folder_batch import discover_folder_jobs, queue_folder_jobs
from video_workflow.json_store import JsonStore
from video_workflow.queue_controller import QueueController


def _file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"input")
    return path


def test_discovers_pdf_style_parts_only_in_recursive_leaf_folders(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Lớp 8" / "Toán 8"
    first_leaf = root / "Bài 1_Đơn thức"
    second_leaf = root / "Chương 2" / "Bài 3_Phép cộng"
    _file(first_leaf / "TOAN7_C4_B13_T38_1.pptx")
    _file(first_leaf / "TOAN7_C4_B13_T38_1.mp4")
    _file(first_leaf / "timeline_slide_TOAN7_C4_B13_T38_1.txt")
    _file(first_leaf / "TOAN7_C4_B13_T38_2.pptx")
    _file(first_leaf / "TOAN7_C4_B13_T38_2.mp4")
    _file(first_leaf / "TOAN7_C4_B13_T38_2.txt")
    _file(second_leaf / "TOAN8_B3_T4_2.pptx")
    _file(second_leaf / "TOAN8_B3_T4_2.m4a")
    _file(second_leaf / "timeline_slide_TOAN8_B3_T4_2.txt")
    _file(first_leaf / "output" / "must-not-be-scanned.pptx")

    result = discover_folder_jobs(root)

    assert [(issue.folder, issue.message) for issue in result.issues] == [
        (second_leaf, "TOAN8_B3_T4: missing part 1")
    ]
    assert [job.output for job in result.jobs] == [
        first_leaf / "output" / "TOAN7_C4_B13_T38_1.mp4",
        first_leaf / "output" / "TOAN7_C4_B13_T38_2.mp4",
        second_leaf / "output" / "TOAN8_B3_T4_2.mp4",
    ]
    assert result.jobs[0].pptx == first_leaf / "TOAN7_C4_B13_T38_1.pptx"
    assert result.jobs[1].source_media == first_leaf / "TOAN7_C4_B13_T38_2.mp4"
    assert result.jobs[1].timeline == first_leaf / "TOAN7_C4_B13_T38_2.txt"
    assert result.jobs[2].timeline.name == "timeline_slide_TOAN8_B3_T4_2.txt"


def test_reports_incomplete_and_ambiguous_parts_without_hiding_valid_jobs(
    tmp_path: Path,
) -> None:
    leaf = tmp_path / "Bài 1"
    _file(leaf / "TOAN8_B1_T1_1.pptx")
    _file(leaf / "TOAN8_B1_T1_1.mp4")
    _file(leaf / "TOAN8_B1_T1_2.pptx")
    _file(leaf / "TOAN8_B1_T1_2.mp4")
    _file(leaf / "TOAN8_B1_T1_2.wav")
    _file(leaf / "timeline_slide_TOAN8_B1_T1_2.txt")

    result = discover_folder_jobs(tmp_path)

    assert result.jobs == ()
    messages = [issue.message for issue in result.issues]
    assert messages == [
        "TOAN8_B1_T1_1: missing timeline",
        "TOAN8_B1_T1_2: multiple media files",
    ]


def test_rejects_a_root_that_is_not_a_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    result = discover_folder_jobs(missing)

    assert result.jobs == ()
    assert len(result.issues) == 1
    assert result.issues[0].folder == missing
    assert result.issues[0].message == "input root is not a directory"


def test_reports_decks_without_parts_and_parts_without_a_pptx(tmp_path: Path) -> None:
    leaf = tmp_path / "Bài 1"
    _file(leaf / "TOAN8_B1_T1.pptx")
    _file(leaf / "TOAN8_B1_T2_1.mp4")
    _file(leaf / "TOAN8_B1_T2_1.txt")

    result = discover_folder_jobs(tmp_path)

    assert result.jobs == ()
    assert [issue.message for issue in result.issues] == [
        "TOAN8_B1_T1: PPTX name must end with _1 or _2",
        "TOAN8_B1_T2_1: missing PPTX",
    ]


def test_reports_direct_timeline_without_a_pptx(tmp_path: Path) -> None:
    leaf = tmp_path / "Bài 1"
    _file(leaf / "TOAN8_B1_T2_1.txt")

    result = discover_folder_jobs(tmp_path)

    assert result.jobs == ()
    assert [issue.message for issue in result.issues] == [
        "TOAN8_B1_T2_1: missing PPTX"
    ]


def test_rescan_queues_every_job_and_keeps_existing_video_until_render(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Toan8"
    leaf = root / "Bài 1"
    for name in (
        "TOAN8_B1_T1_1.pptx",
        "TOAN8_B1_T1_1.mp4",
        "timeline_slide_TOAN8_B1_T1_1.txt",
        "TOAN8_B1_T1_2.pptx",
        "TOAN8_B1_T1_2.mp4",
        "timeline_slide_TOAN8_B1_T1_2.txt",
    ):
        _file(leaf / name)
    existing = _file(leaf / "output" / "TOAN8_B1_T1_1.mp4")
    existing_merged = _file(leaf / "output" / "TOAN8_B1_T1.mp4")
    matching_report = _file(Path(f"{existing}.report.json"))
    stale_report = _file(leaf / "output" / "old_fixed.mp4.report.json")
    old_video = _file(leaf / "output" / "old_fixed.mp4")
    controller = QueueController(JsonStore(tmp_path / "data"))

    first = queue_folder_jobs(controller, root)
    second = queue_folder_jobs(controller, root)

    assert [job.output_path for job in first.queued] == [
        leaf / "output" / "TOAN8_B1_T1_1.mp4",
        leaf / "output" / "TOAN8_B1_T1_2.mp4",
        leaf / "output" / "TOAN8_B1_T1.mp4",
    ]
    assert all(job.write_report is False for job in first.queued)
    assert all(job.overwrite_output is True for job in first.queued)
    assert first.skipped == ()
    assert not matching_report.exists()
    assert not stale_report.exists()
    assert existing.read_bytes() == b"input"
    assert existing_merged.read_bytes() == b"input"
    assert old_video.exists()
    assert [job.output_path for job in second.queued] == [
        leaf / "output" / "TOAN8_B1_T1_1.mp4",
        leaf / "output" / "TOAN8_B1_T1_2.mp4",
        leaf / "output" / "TOAN8_B1_T1.mp4",
    ]
    assert first.queued[2].source_media == first.queued[0].output_path
    assert first.queued[2].secondary_media == first.queued[1].output_path
    assert first.queued[2].dependency_job_ids == (
        first.queued[0].id,
        first.queued[1].id,
    )
    assert second.queued[2].dependency_job_ids == (
        second.queued[0].id,
        second.queued[1].id,
    )
    assert set(first.queued[2].dependency_job_ids).isdisjoint(
        second.queued[2].dependency_job_ids
    )
    assert second.skipped == ()
    assert len(controller.jobs()) == 6


def test_batch_marks_outro_for_part_2_only(tmp_path: Path) -> None:
    root = tmp_path / "Toan8"
    leaf = root / "Bài 1"
    for name in (
        "TOAN8_B1_T1_1.pptx",
        "TOAN8_B1_T1_1.mp4",
        "timeline_slide_TOAN8_B1_T1_1.txt",
        "TOAN8_B1_T1_2.pptx",
        "TOAN8_B1_T1_2.mp4",
        "TOAN8_B1_T1_2.txt",
    ):
        _file(leaf / name)
    controller = QueueController(JsonStore(tmp_path / "data"))

    result = queue_folder_jobs(controller, root)

    slide_jobs = [job for job in result.queued if job.kind.value == "slide"]
    assert [job.use_outro for job in slide_jobs] == [False, True]
    assert [job.kind.value for job in result.queued] == ["slide", "slide", "merge"]
    assert all(job.write_report is False for job in result.queued)
    assert all(job.overwrite_output is True for job in result.queued)


def test_similar_lesson_names_in_one_leaf_are_grouped_independently(
    tmp_path: Path,
) -> None:
    leaf = tmp_path / "Bài 1"
    for stem in ("LESSON_X_1", "LESSON_X_2", "LESSON_1_1", "LESSON_1_2"):
        _file(leaf / f"{stem}.pptx")
        _file(leaf / f"{stem}.mp4")
        _file(leaf / f"{stem}.txt")
    controller = QueueController(JsonStore(tmp_path / "data"))

    result = queue_folder_jobs(controller, tmp_path)

    assert result.issues == ()
    assert [job.output_name for job in result.queued] == [
        "LESSON_1_1.mp4",
        "LESSON_1_2.mp4",
        "LESSON_1.mp4",
        "LESSON_X_1.mp4",
        "LESSON_X_2.mp4",
        "LESSON_X.mp4",
    ]
    assert result.queued[2].dependency_job_ids == (
        result.queued[0].id,
        result.queued[1].id,
    )
    assert result.queued[5].dependency_job_ids == (
        result.queued[3].id,
        result.queued[4].id,
    )


def test_single_valid_part_is_queued_and_reports_missing_counterpart(
    tmp_path: Path,
) -> None:
    leaf = tmp_path / "Bài 1"
    for name in ("ONLY_2.pptx", "ONLY_2.mp4", "ONLY_2.txt"):
        _file(leaf / name)
    controller = QueueController(JsonStore(tmp_path / "data"))

    result = queue_folder_jobs(controller, tmp_path)

    assert [job.output_name for job in result.queued] == ["ONLY_2.mp4"]
    assert [(issue.folder, issue.message) for issue in result.issues] == [
        (leaf, "ONLY: missing part 1")
    ]


@pytest.mark.skipif(
    os.path.normcase("A") == os.path.normcase("a"),
    reason="case-distinct leaf folders are a POSIX filesystem scenario",
)
def test_case_distinct_linux_leaf_folders_do_not_cross_pair(tmp_path: Path) -> None:
    upper_leaf = tmp_path / "A"
    lower_leaf = tmp_path / "a"
    for leaf, stem in ((upper_leaf, "LESSON_1"), (lower_leaf, "LESSON_2")):
        _file(leaf / f"{stem}.pptx")
        _file(leaf / f"{stem}.mp4")
        _file(leaf / f"{stem}.txt")

    result = discover_folder_jobs(tmp_path)

    assert {job.pptx.parent for job in result.jobs} == {upper_leaf, lower_leaf}
    assert {(issue.folder, issue.message) for issue in result.issues} == {
        (upper_leaf, "LESSON: missing part 2"),
        (lower_leaf, "LESSON: missing part 1"),
    }
