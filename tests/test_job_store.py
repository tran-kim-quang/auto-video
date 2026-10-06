from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from video_workflow.job_models import (
    GlobalSettings,
    JobRecord,
    JobStage,
    JobStatus,
    validate_output_name,
)
from video_workflow.json_store import JsonStore


def _job(tmp_path: Path, *, status: JobStatus = JobStatus.WAITING) -> JobRecord:
    return JobRecord.new(
        source_media=tmp_path / "đầu vào có dấu.mp3",
        pptx=tmp_path / "bài giảng.pptx",
        timeline=tmp_path / "timeline.txt",
        output_name="kết quả",
        output_directory=tmp_path / "thư mục output",
        status=status,
    )


def test_models_validate_output_names_and_build_path(tmp_path: Path) -> None:
    assert {item.value for item in JobStatus} == {
        "waiting",
        "running",
        "completed",
        "failed",
        "interrupted",
    }
    assert {item.value for item in JobStage} == {
        "validating",
        "exporting_slides",
        "rendering_lecture",
        "preparing_outro",
        "joining",
        "verifying",
        "normalizing_first",
        "normalizing_second",
    }
    assert validate_output_name("Bài giảng 01") == "Bài giảng 01.mp4"
    assert validate_output_name("lesson.MP4") == "lesson.MP4"
    job = _job(tmp_path)
    assert job.output_name == "kết quả.mp4"
    assert job.output_path == tmp_path / "thư mục output" / "kết quả.mp4"
    assert "+00:00" in job.created_at


@pytest.mark.parametrize(
    "name", ["", "../bad", "a/b", "a\\b", "bad:name", "CON", "con.mp4", "name. "]
)
def test_rejects_unsafe_windows_output_names(name: str) -> None:
    with pytest.raises(ValueError):
        validate_output_name(name)


def test_settings_and_jobs_round_trip_unicode_paths(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / ".workflow_data")
    settings = GlobalSettings(
        logo=tmp_path / "ảnh logo.png", outro=tmp_path / "video kết thúc.mp4"
    )
    jobs = [
        _job(tmp_path),
        replace(
            _job(tmp_path), id="second", status=JobStatus.INTERRUPTED, error="đã dừng"
        ),
    ]

    store.save_settings(settings)
    store.save_jobs(jobs)

    assert store.load_settings() == settings
    assert store.load_jobs() == jobs
    raw = (store.root / "jobs.json").read_text(encoding="utf-8")
    assert "kết quả" in raw
    assert "\\u" not in raw


def test_failed_atomic_replace_keeps_previous_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JsonStore(tmp_path / "data")
    original = [_job(tmp_path)]
    store.save_jobs(original)

    def fail_replace(*_args) -> None:
        raise OSError("disk busy")

    monkeypatch.setattr("video_workflow.json_store.os.replace", fail_replace)
    with pytest.raises(OSError, match="disk busy"):
        store.save_jobs([replace(original[0], id="changed")])

    assert (
        json.loads((store.root / "jobs.json").read_text(encoding="utf-8"))["jobs"][0][
            "id"
        ]
        == original[0].id
    )


def test_corrupt_jobs_are_backed_up_and_reported(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "data")
    store.root.mkdir(parents=True)
    (store.root / "jobs.json").write_text('{"jobs": [', encoding="utf-8")

    assert store.load_jobs() == []
    assert any(store.root.glob("jobs.json.corrupt-*"))
    assert store.warnings and "jobs.json" in store.warnings[0]


def test_valid_json_with_unsupported_schema_is_also_preserved(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "data")
    store.root.mkdir(parents=True)
    (store.root / "jobs.json").write_text(
        '{"schema_version": 99, "jobs": []}', encoding="utf-8"
    )

    assert store.load_jobs() == []
    assert not (store.root / "jobs.json").exists()
    assert any(store.root.glob("jobs.json.corrupt-*"))


def test_merge_job_round_trips_both_video_paths(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "data")
    job = JobRecord.new_merge(
        first_video=tmp_path / "video một.mp4",
        second_video=tmp_path / "video hai.mp4",
        output_name="đã ghép",
        output_directory=tmp_path / "output",
    )

    store.save_jobs([job])

    loaded = store.load_jobs()[0]
    assert loaded.kind.value == "merge"
    assert loaded.source_media == tmp_path / "video một.mp4"
    assert loaded.secondary_media == tmp_path / "video hai.mp4"
    assert loaded.pptx is None
    assert loaded.timeline is None


def test_merge_job_round_trips_dependencies_and_batch_flags(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "data")
    job = JobRecord.new_merge(
        first_video=tmp_path / "part_1.mp4",
        second_video=tmp_path / "part_2.mp4",
        output_name="lesson",
        output_directory=tmp_path / "output",
        write_report=False,
        overwrite_output=True,
        dependency_job_ids=("part-1-job", "part-2-job"),
    )

    store.save_jobs([job])

    payload = json.loads((store.root / "jobs.json").read_text(encoding="utf-8"))
    loaded = store.load_jobs()[0]
    assert payload["jobs"][0]["dependency_job_ids"] == [
        "part-1-job",
        "part-2-job",
    ]
    assert loaded.dependency_job_ids == ("part-1-job", "part-2-job")
    assert loaded.write_report is False
    assert loaded.overwrite_output is True


def test_legacy_job_defaults_to_no_dependencies(tmp_path: Path) -> None:
    payload = _job(tmp_path).to_dict()
    payload.pop("dependency_job_ids", None)

    assert JobRecord.from_dict(payload).dependency_job_ids == ()


def test_legacy_job_without_kind_loads_as_slide_job(tmp_path: Path) -> None:
    payload = _job(tmp_path).to_dict()
    payload.pop("kind", None)

    loaded = JobRecord.from_dict(payload)

    assert loaded.kind.value == "slide"
    assert loaded.secondary_media is None


def test_slide_job_round_trips_disabled_report_and_legacy_defaults_to_enabled(
    tmp_path: Path,
) -> None:
    job = JobRecord.new(
        source_media=tmp_path / "source.mp4",
        pptx=tmp_path / "slides.pptx",
        timeline=tmp_path / "timeline.txt",
        output_name="lesson_1",
        output_directory=tmp_path / "output",
        write_report=False,
        use_outro=False,
    )

    payload = job.to_dict()
    assert JobRecord.from_dict(payload).write_report is False
    assert JobRecord.from_dict(payload).use_outro is False

    payload.pop("write_report")
    payload.pop("use_outro")
    assert JobRecord.from_dict(payload).write_report is True
    assert JobRecord.from_dict(payload).use_outro is True


def test_existing_batch_jobs_infer_outro_from_part_suffix(tmp_path: Path) -> None:
    part1 = JobRecord.new(
        source_media=tmp_path / "source_1.mp4",
        pptx=tmp_path / "slides.pptx",
        timeline=tmp_path / "timeline_1.txt",
        output_name="TOAN8_B1_T1_1",
        output_directory=tmp_path / "output",
        write_report=False,
    ).to_dict()
    part1.pop("use_outro")
    part1.pop("overwrite_output")
    part2 = dict(part1, output_name="TOAN8_B1_T1_2.mp4")

    assert JobRecord.from_dict(part1).use_outro is False
    assert JobRecord.from_dict(part2).use_outro is True
    assert JobRecord.from_dict(part1).overwrite_output is True
    assert JobRecord.from_dict(part2).overwrite_output is True


def test_legacy_manual_job_does_not_enable_output_overwrite(tmp_path: Path) -> None:
    payload = _job(tmp_path).to_dict()
    payload.pop("overwrite_output")

    assert JobRecord.from_dict(payload).overwrite_output is False
