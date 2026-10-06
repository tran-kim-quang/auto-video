from __future__ import annotations

from pathlib import Path

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

    assert result.issues == ()
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


def test_queues_new_outputs_skips_existing_or_queued_and_deletes_stale_report(
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
    matching_report = _file(Path(f"{existing}.report.json"))
    stale_report = _file(leaf / "output" / "old_fixed.mp4.report.json")
    old_video = _file(leaf / "output" / "old_fixed.mp4")
    controller = QueueController(JsonStore(tmp_path / "data"))

    first = queue_folder_jobs(controller, root)
    second = queue_folder_jobs(controller, root)

    assert [job.output_path for job in first.queued] == [
        leaf / "output" / "TOAN8_B1_T1_2.mp4"
    ]
    assert first.queued[0].write_report is False
    assert first.skipped == (existing,)
    assert not matching_report.exists()
    assert not stale_report.exists()
    assert old_video.exists()
    assert second.queued == ()
    assert second.skipped == (
        existing,
        leaf / "output" / "TOAN8_B1_T1_2.mp4",
    )
    assert len(controller.jobs()) == 1


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

    assert [job.use_outro for job in result.queued] == [False, True]
