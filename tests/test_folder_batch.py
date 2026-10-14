from __future__ import annotations

import os
from pathlib import Path

from video_workflow import folder_batch
from video_workflow.folder_batch import discover_folder_jobs


def _file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"input")
    return path


def test_discovers_multiple_pairs_in_any_recursive_directory(tmp_path: Path) -> None:
    lesson_dir = tmp_path / "Lớp 8" / "Bài 1"
    _file(lesson_dir / "one.pptx")
    _file(lesson_dir / "one.txt")
    _file(lesson_dir / "two.PPTX")
    _file(lesson_dir / "TWO.TXT")
    _file(tmp_path / "root-lesson.pptx")
    _file(tmp_path / "root-lesson.txt")

    result = discover_folder_jobs(tmp_path)

    assert result.issues == ()
    assert [job.pptx.name for job in result.jobs] == [
        "root-lesson.pptx",
        "one.pptx",
        "two.PPTX",
    ]
    assert [(job.pptx.stem.casefold(), job.script.stem.casefold()) for job in result.jobs] == [
        ("root-lesson", "root-lesson"),
        ("one", "one"),
        ("two", "two"),
    ]
    assert result.jobs[1].artifact_directory == lesson_dir / "output" / "one"
    assert result.jobs[1].output_name == "one.mp4"


def test_reports_each_orphan_and_ambiguous_casefolded_stem(tmp_path: Path) -> None:
    folder = tmp_path / "lessons"
    _file(folder / "deck-only.pptx")
    _file(folder / "script-only.txt")
    _file(folder / "LESSON.pptx")
    _file(folder / "lesson.PPTX")
    _file(folder / "lesson.txt")

    result = discover_folder_jobs(tmp_path)

    assert result.jobs == ()
    assert [issue.message for issue in result.issues] == [
        "deck-only: missing TXT",
        "lesson: ambiguous files (2 PPTX, 1 TXT)",
        "script-only: missing PPTX",
    ]


def test_ignores_output_hidden_and_application_temporary_directories(
    tmp_path: Path,
) -> None:
    for directory in (
        tmp_path / "output",
        tmp_path / "OUTPUT",
        tmp_path / ".hidden",
        tmp_path / ".video-workflow-stage-123",
    ):
        _file(directory / "ignored.pptx")
        _file(directory / "ignored.txt")
    _file(tmp_path / "visible" / "kept.pptx")
    _file(tmp_path / "visible" / "kept.txt")

    result = discover_folder_jobs(tmp_path)

    assert [job.pptx.name for job in result.jobs] == ["kept.pptx"]
    assert result.issues == ()


def test_does_not_follow_directory_symlinks_or_discover_same_file_twice(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    _file(real / "lesson.pptx")
    _file(real / "lesson.txt")
    try:
        (tmp_path / "alias").symlink_to(real, target_is_directory=True)
    except OSError:
        return

    result = discover_folder_jobs(tmp_path)

    assert [job.pptx for job in result.jobs] == [real / "lesson.pptx"]


def test_reports_unreadable_directory_and_continues_other_folders(
    tmp_path: Path, monkeypatch
) -> None:
    good = tmp_path / "good"
    _file(good / "lesson.pptx")
    _file(good / "lesson.txt")
    denied = tmp_path / "denied"

    def fake_walk(root, *, topdown, onerror, followlinks):
        assert Path(root) == tmp_path
        assert topdown is True
        assert followlinks is False
        yield str(tmp_path), ["good", "denied"], []
        onerror(PermissionError(13, "Permission denied", str(denied)))
        yield str(good), [], ["lesson.pptx", "lesson.txt"]

    monkeypatch.setattr(folder_batch.os, "walk", fake_walk)

    result = discover_folder_jobs(tmp_path)

    assert [job.pptx for job in result.jobs] == [good / "lesson.pptx"]
    assert len(result.issues) == 1
    assert result.issues[0].folder == denied
    assert "Permission denied" in result.issues[0].message


def test_rejects_a_root_that_is_not_a_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    result = discover_folder_jobs(missing)

    assert result.jobs == ()
    assert result.issues == (
        folder_batch.FolderIssue(missing, "input root is not a directory"),
    )


def test_job_order_is_deterministic_across_walk_order(
    tmp_path: Path, monkeypatch
) -> None:
    left = tmp_path / "z"
    right = tmp_path / "a"
    for directory, stem in ((left, "Beta"), (right, "alpha")):
        _file(directory / f"{stem}.pptx")
        _file(directory / f"{stem}.txt")
    real_walk = os.walk

    def reversed_walk(root, **kwargs):
        rows = list(real_walk(root, **kwargs))
        yield from reversed(rows)

    monkeypatch.setattr(folder_batch.os, "walk", reversed_walk)

    result = discover_folder_jobs(tmp_path)

    assert [job.pptx.name for job in result.jobs] == ["alpha.pptx", "Beta.pptx"]
