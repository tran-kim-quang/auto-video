from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from video_workflow.job_models import JobStatus
from video_workflow.pipeline import WorkflowCancelled
from video_workflow.voicestudio_client import VoiceStudioOutOfMemory
from video_workflow.web.runner import RunResult
from video_workflow.web.scheduler import (
    DuplicateJobError,
    JobScheduler,
    JobStateError,
)
from video_workflow.web.state import WebJob, WebStore


def _wait(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for scheduler state")


def test_at_most_three_running(tmp_path: Path) -> None:
    releases: dict[str, threading.Event] = {}

    def run(job, cancel, update):
        releases.setdefault(job.id, threading.Event()).wait(2)
        return RunResult(Path(f"/{job.id}.mp4"), 1.0)

    scheduler = JobScheduler(WebStore(tmp_path), run, max_active=3)
    jobs = scheduler.enqueue("batch", [(f"leaf-{i}", 10) for i in range(5)], "voice")
    scheduler.start()
    try:
        _wait(lambda: sum(j.status is JobStatus.RUNNING for j in scheduler.jobs()) == 3)
        assert sum(j.status is JobStatus.WAITING for j in scheduler.jobs()) == 2
        releases[jobs[0].id].set()
        _wait(lambda: scheduler.jobs()[3].status is JobStatus.RUNNING)
    finally:
        for event in releases.values():
            event.set()
        scheduler.stop()


def test_duplicate_leaf_rejected(tmp_path: Path) -> None:
    release = threading.Event()

    def run(job, cancel, update):
        release.wait(2)
        return RunResult(Path("/video.mp4"), 1.0)

    scheduler = JobScheduler(
        WebStore(tmp_path),
        run,
    )
    scheduler.enqueue("batch", [("leaf", 10)], "voice")

    with pytest.raises(DuplicateJobError, match="Bài leaf đang chờ hoặc đang chạy"):
        scheduler.enqueue("batch", [("leaf", 10)], "voice")
    assert len(scheduler.jobs()) == 1

    scheduler.start()
    release.set()
    try:
        _wait(lambda: scheduler.jobs()[0].status is JobStatus.COMPLETED)
        assert len(scheduler.enqueue("batch", [("leaf", 10)], "voice")) == 1
    finally:
        scheduler.stop()


def test_duplicate_output_in_one_enqueue_is_rejected_atomically(tmp_path: Path) -> None:
    scheduler = JobScheduler(WebStore(tmp_path / "state"), lambda *_args: None)

    with pytest.raises(
        DuplicateJobError,
        match="^Trùng vị trí lưu với bài A/Toan6/B/T1$",
    ):
        scheduler.enqueue(
            "batch",
            [("A/Toan6/B/T1", 10), ("C/Toan6/B/T1", 20)],
            "voice",
            output_root=str(tmp_path / "out"),
        )

    assert scheduler.jobs() == ()


def test_same_relative_output_in_different_roots_is_allowed(tmp_path: Path) -> None:
    scheduler = JobScheduler(WebStore(tmp_path / "state"), lambda *_args: None)

    first = scheduler.enqueue(
        "batch",
        [("A/Toan6/B/T1", 10)],
        "voice",
        output_root=str(tmp_path / "first"),
    )[0]
    second = scheduler.enqueue(
        "batch",
        [("C/Toan6/B/T1", 10)],
        "voice",
        output_root=str(tmp_path / "second"),
    )[0]

    assert first.output_root == str(tmp_path / "first")
    assert second.output_root == str(tmp_path / "second")


def test_completed_job_records_vtt_path(tmp_path: Path) -> None:
    def run(job, cancel, update):
        return RunResult(Path("/video.mp4"), 1.0, Path("/video/vi.vtt"))

    scheduler = JobScheduler(WebStore(tmp_path), run)
    scheduler.enqueue("batch", [("leaf", 10)], "voice")
    scheduler.start()
    try:
        _wait(lambda: scheduler.jobs()[0].status is JobStatus.COMPLETED)
        assert scheduler.jobs()[0].vtt_path == "/video/vi.vtt"
    finally:
        scheduler.stop()


def test_cancel_waiting_and_running(tmp_path: Path) -> None:
    def run(job, cancel, update):
        while not cancel.wait(0.01):
            pass
        raise WorkflowCancelled("cancelled")

    scheduler = JobScheduler(WebStore(tmp_path), run, max_active=1)
    first, second = scheduler.enqueue(
        "batch", [("first", 10), ("second", 10)], "voice"
    )
    scheduler.start()
    try:
        _wait(lambda: scheduler.jobs()[0].status is JobStatus.RUNNING)
        assert scheduler.cancel(second.id).status is JobStatus.INTERRUPTED
        scheduler.cancel(first.id)
        _wait(lambda: scheduler.jobs()[0].status is JobStatus.INTERRUPTED)
        assert scheduler.jobs()[0].error == "Đã huỷ"
    finally:
        scheduler.stop()


def test_failure_records_vietnamese_error(tmp_path: Path) -> None:
    def run(job, cancel, update):
        raise VoiceStudioOutOfMemory("CUDA OOM")

    scheduler = JobScheduler(WebStore(tmp_path), run)
    scheduler.enqueue("batch", [("leaf", 10)], "voice")
    scheduler.start()
    try:
        _wait(lambda: scheduler.jobs()[0].status is JobStatus.FAILED)
        assert scheduler.jobs()[0].error == "VoiceStudio hết bộ nhớ GPU"
    finally:
        scheduler.stop()


def test_recover_marks_running_interrupted(tmp_path: Path) -> None:
    store = WebStore(tmp_path)
    running = replace(
        WebJob.new(
            batch_id="batch",
            leaf_relpath="leaf",
            profile_id="voice",
            script_chars=10,
        ),
        status=JobStatus.RUNNING,
    )
    store.save_jobs([running])

    scheduler = JobScheduler(store, lambda *_args: None)

    assert scheduler.jobs()[0].status is JobStatus.INTERRUPTED
    assert scheduler.jobs()[0].error == "Máy chủ đã khởi động lại khi đang xử lý"


def test_stop_interrupts_running_jobs(tmp_path: Path) -> None:
    release = threading.Event()

    def run(job, cancel, update):
        while not cancel.is_set() and not release.wait(0.01):
            pass
        if cancel.is_set():
            raise WorkflowCancelled("server stopping")
        return RunResult(Path("/video.mp4"), 1.0)

    store = WebStore(tmp_path)
    scheduler = JobScheduler(store, run)
    scheduler.enqueue("batch", [("leaf", 10)], "voice")
    scheduler.start()
    _wait(lambda: scheduler.jobs()[0].status is JobStatus.RUNNING)

    try:
        scheduler.stop(timeout=0.2)
        assert scheduler.jobs()[0].status is JobStatus.INTERRUPTED
        assert WebStore(tmp_path).load_jobs()[0].status is JobStatus.INTERRUPTED
    finally:
        release.set()
        scheduler.stop()


def test_retry_only_failed_or_interrupted(tmp_path: Path) -> None:
    store = WebStore(tmp_path)
    completed = replace(
        WebJob.new(
            batch_id="batch",
            leaf_relpath="leaf",
            profile_id="voice",
            script_chars=10,
        ),
        status=JobStatus.COMPLETED,
    )
    store.save_jobs([completed])
    scheduler = JobScheduler(store, lambda *_args: None)

    with pytest.raises(JobStateError):
        scheduler.retry(completed.id)


def test_retry_preserves_output_root(tmp_path: Path) -> None:
    store = WebStore(tmp_path)
    failed = replace(
        WebJob.new(
            batch_id="batch",
            leaf_relpath="A/Toan6/B/T1",
            profile_id="voice",
            script_chars=10,
            output_root="/tmp/results",
        ),
        status=JobStatus.FAILED,
        video_path="/tmp/old.mp4",
        vtt_path="/tmp/old/vi.vtt",
    )
    store.save_jobs([failed])
    scheduler = JobScheduler(store, lambda *_args: None)

    retried = scheduler.retry(failed.id)

    assert retried.output_root == "/tmp/results"
    assert retried.video_path is None
    assert retried.vtt_path is None


def test_update_output_paths_persists_without_changing_status(tmp_path: Path) -> None:
    store = WebStore(tmp_path)
    completed = replace(
        WebJob.new(
            batch_id="batch",
            leaf_relpath="A/Toan6/B/T1",
            profile_id="voice",
            script_chars=10,
            output_root="/tmp/results",
        ),
        status=JobStatus.COMPLETED,
        video_path="/tmp/results/old.mp4",
        vtt_path="/tmp/results/old/vi.vtt",
    )
    store.save_jobs([completed])
    scheduler = JobScheduler(store, lambda *_args: None)

    updated = scheduler.update_output_paths(
        completed.id,
        "/tmp/results/parked.mp4",
        "/tmp/results/parked/vi.vtt",
    )

    assert updated.status is JobStatus.COMPLETED
    assert updated.video_path == "/tmp/results/parked.mp4"
    assert updated.vtt_path == "/tmp/results/parked/vi.vtt"
    persisted = WebStore(tmp_path).load_jobs()[0]
    assert persisted == updated
