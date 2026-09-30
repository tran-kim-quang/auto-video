from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

import pytest

from video_workflow.job_models import JobStatus
from video_workflow.json_store import JsonStore
from video_workflow.pipeline import WorkflowCancelled
from video_workflow.queue_controller import QueueController
from video_workflow.worker import QueueWorker


def _controller(tmp_path: Path, count: int = 2) -> QueueController:
    files = [tmp_path / name for name in ("source.mp3", "slides.pptx", "timeline.txt", "logo.png", "outro.mp4")]
    for path in files:
        path.write_bytes(b"x")
    output = tmp_path / "output"
    output.mkdir()
    controller = QueueController(JsonStore(tmp_path / "data"))
    controller.set_global_assets(files[3], files[4])
    for index in range(count):
        controller.enqueue(
            source_media=files[0], pptx=files[1], timeline=files[2],
            output_name=f"job-{index}", output_directory=output,
        )
    return controller


def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


def test_worker_runs_one_job_at_a_time_and_continues_after_failure(tmp_path: Path) -> None:
    controller = _controller(tmp_path, 3)
    active = 0
    max_active = 0
    calls: list[str] = []

    def build(request, **kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        calls.append(request.output.name)
        active -= 1
        if len(calls) == 1:
            raise RuntimeError("first failed")

    worker = QueueWorker(controller, build=build)
    worker.start()
    worker.wake()
    _wait_until(lambda: all(job.status in {JobStatus.COMPLETED, JobStatus.FAILED} for job in controller.jobs()))
    worker.stop()

    assert max_active == 1
    assert calls == ["job-0.mp4", "job-1.mp4", "job-2.mp4"]
    assert [job.status for job in controller.jobs()] == [JobStatus.FAILED, JobStatus.COMPLETED, JobStatus.COMPLETED]


def test_worker_persists_stages_and_emits_events(tmp_path: Path) -> None:
    controller = _controller(tmp_path, 1)
    events: queue.Queue = queue.Queue()

    def build(_request, *, on_stage, **_kwargs):
        for stage in ("validating", "exporting_slides", "rendering_lecture", "preparing_outro", "joining", "verifying"):
            on_stage(stage)

    worker = QueueWorker(controller, build=build, events=events)
    worker.start()
    worker.wake()
    _wait_until(lambda: controller.jobs()[0].status is JobStatus.COMPLETED)
    worker.stop()
    emitted = []
    while not events.empty():
        emitted.append(events.get_nowait().kind)
    assert emitted[0] == "started"
    assert emitted.count("stage") == 6
    assert emitted[-1] == "completed"


def test_worker_pauses_for_missing_globals_and_resumes_on_wake(tmp_path: Path) -> None:
    controller = _controller(tmp_path, 1)
    logo, outro = controller.settings.logo, controller.settings.outro
    logo.unlink()
    calls: list[str] = []
    worker = QueueWorker(controller, build=lambda request, **kwargs: calls.append(request.output.name))
    worker.start()
    worker.wake()
    time.sleep(0.1)
    assert calls == []
    logo.write_bytes(b"x")
    controller.set_global_assets(logo, outro)
    worker.wake()
    _wait_until(lambda: controller.jobs()[0].status is JobStatus.COMPLETED)
    worker.stop()
    assert calls == ["job-0.mp4"]


def test_stop_cancels_build_marks_interrupted_and_balances_com(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _controller(tmp_path, 1)
    entered = threading.Event()
    com_calls: list[str] = []
    monkeypatch.setattr("video_workflow.worker.pythoncom.CoInitialize", lambda: com_calls.append("init"))
    monkeypatch.setattr("video_workflow.worker.pythoncom.CoUninitialize", lambda: com_calls.append("uninit"))

    def build(_request, *, cancel_event, **_kwargs):
        entered.set()
        cancel_event.wait(3)
        raise WorkflowCancelled("cancelled")

    worker = QueueWorker(controller, build=build)
    worker.start()
    worker.wake()
    assert entered.wait(1)
    worker.stop(timeout=2)

    assert not worker.is_alive()
    assert controller.jobs()[0].status is JobStatus.INTERRUPTED
    assert com_calls == ["init", "uninit"]
