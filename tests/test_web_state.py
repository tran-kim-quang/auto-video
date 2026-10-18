from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from video_workflow.job_models import JobStatus
from video_workflow.web.estimate import (
    estimate_job_seconds,
    remaining_seconds,
    seconds_per_char,
)
from video_workflow.web.state import BatchRecord, WebJob, WebStore


def _job(chars: int = 100) -> WebJob:
    return WebJob.new(
        batch_id="batch-1",
        leaf_relpath="root/lesson",
        profile_id="599fc048",
        script_chars=chars,
    )


def test_round_trip_web_job_and_batch(tmp_path: Path) -> None:
    store = WebStore(tmp_path)
    job = _job()
    batch = BatchRecord(id="batch-1", root_name="root", created_at="2026-10-19T00:00:00+00:00")

    store.save_jobs([job])
    store.add_batch(batch)

    assert store.load_jobs() == [job]
    assert store.load_batches() == [batch]
    assert WebJob.from_dict(job.to_dict()) == job


def test_default_voice_and_persistence(tmp_path: Path) -> None:
    assert WebStore(tmp_path).default_voice() == "599fc048"

    WebStore(tmp_path).set_default_voice("abc12345")

    assert WebStore(tmp_path).default_voice() == "abc12345"


def test_corrupt_jobs_file_is_preserved(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "jobs.json").write_text("{broken", encoding="utf-8")

    assert WebStore(tmp_path).load_jobs() == []
    assert not (tmp_path / "jobs.json").exists()
    assert len(list(tmp_path.glob("jobs.json.corrupt-*"))) == 1


def test_estimates_use_completed_tts_history() -> None:
    first = replace(
        _job(10_000),
        status=JobStatus.COMPLETED,
        tts_seconds=300,
    )
    second = replace(
        _job(5_000),
        status=JobStatus.COMPLETED,
        tts_seconds=100,
    )

    assert seconds_per_char([]) == 0.032
    assert seconds_per_char([first, second]) == pytest.approx(400 / 15_000)
    assert estimate_job_seconds(27_340, 0.032) == 995


def test_remaining_seconds_during_synthesis() -> None:
    job = replace(
        _job(27_340),
        stage="synthesizing_audio",
        progress_current=10,
        progress_total=40,
    )

    assert remaining_seconds(job, 0.032) == 776
    assert remaining_seconds(replace(job, stage="exporting_slides"), 0.032) is None
