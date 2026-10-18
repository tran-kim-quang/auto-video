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
