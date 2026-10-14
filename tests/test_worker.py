from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from video_workflow.job_models import JobStatus
from video_workflow.json_store import JsonStore
from video_workflow.pipeline import WorkflowCancelled
from video_workflow.queue_controller import QueueController
from video_workflow.worker import QueueWorker


class _Supervisor:
    def __init__(self, client=object()) -> None:
        self.client = client
        self.ready = 0
        self.closed = 0

    def ensure_ready(self):
        self.ready += 1
        return self.client

    def close(self):
        self.closed += 1


def _controller(tmp_path: Path, count: int = 1) -> QueueController:
    logo, outro = tmp_path / "logo.png", tmp_path / "outro.mp4"
    logo.write_bytes(b"logo")
    outro.write_bytes(b"outro")
    controller = QueueController(JsonStore(tmp_path / "data"))
    controller.set_global_assets(logo, outro)
    for index in range(count):
        pptx = tmp_path / f"lesson-{index}.pptx"
        script = tmp_path / f"lesson-{index}.txt"
        pptx.write_bytes(f"pptx-{index}".encode())
        script.write_text("Một.", encoding="utf-8")
        controller.enqueue(
            pptx=pptx,
            script=script,
            artifact_directory=tmp_path / "output" / f"lesson-{index}",
            output_name=f"lesson-{index}.mp4",
        )
    return controller


def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


def test_worker_ensures_voicestudio_builds_serially_and_emits_all_stages(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path, 2)
    supervisor = _Supervisor()
    events: queue.Queue = queue.Queue()
    active = 0
    max_active = 0
    requests = []

    def build(request, *, on_stage, on_progress, **_kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        requests.append(request)
        for stage in (
            "validating",
            "synthesizing_audio",
            "assembling_narration",
            "exporting_slides",
            "rendering_lecture",
            "preparing_outro",
            "joining",
            "verifying",
            "publishing",
        ):
            on_stage(stage)
        on_progress(1, 1)
        active -= 1

    worker = QueueWorker(
        controller,
        build=build,
        supervisor_factory=lambda **_kwargs: supervisor,
        events=events,
    )
    worker.start()
    _wait_until(lambda: all(job.status is JobStatus.COMPLETED for job in controller.jobs()))
    worker.stop()

    assert max_active == 1
    assert len(requests) == 2
    assert supervisor.ready == 2
    assert supervisor.closed == 1
    assert all(request.logo.name == "logo.png" for request in requests)
    kinds = []
    while not events.empty():
        kinds.append(events.get_nowait().kind)
    assert kinds.count("started") == 2
    assert kinds.count("progress") == 2
    assert kinds.count("completed") == 2


def test_worker_close_stops_owned_supervisor_after_queue_thread_exits(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path, 0)
    supervisor = _Supervisor()
    worker = QueueWorker(
        controller,
        build=lambda *_args, **_kwargs: None,
        supervisor_factory=lambda **_kwargs: supervisor,
    )

    worker.start()
    worker.stop()

    assert not worker.is_alive()
    assert supervisor.closed == 0


def test_cancel_waits_for_current_builder_then_marks_interrupted(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    supervisor = _Supervisor()
    entered = threading.Event()

    def build(_request, *, cancel_event, **_kwargs):
        entered.set()
        cancel_event.wait(2)
        raise WorkflowCancelled("cancelled")

    worker = QueueWorker(
        controller,
        build=build,
        supervisor_factory=lambda **_kwargs: supervisor,
    )
    worker.start()
    assert entered.wait(1)
    worker.stop(timeout=2)

    assert controller.jobs()[0].status is JobStatus.INTERRUPTED
    assert supervisor.closed == 1


def test_older_queued_job_with_changed_signature_fails_before_publication(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    controller.jobs()[0].script.write_text("Đã thay đổi.", encoding="utf-8")
    builds = []
    worker = QueueWorker(
        controller,
        build=lambda *args, **kwargs: builds.append(args),
        supervisor_factory=lambda **_kwargs: _Supervisor(),
    )

    worker.start()
    _wait_until(lambda: controller.jobs()[0].status is JobStatus.FAILED)
    worker.stop()

    assert builds == []
    assert "changed after it was queued" in controller.jobs()[0].error


def test_one_failed_lesson_does_not_prevent_next_waiting_lesson(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path, 2)
    calls = []

    def build(request, **_kwargs):
        calls.append(request.pptx.name)
        if len(calls) == 1:
            raise RuntimeError("first failed")

    worker = QueueWorker(
        controller,
        build=build,
        supervisor_factory=lambda **_kwargs: _Supervisor(),
    )
    worker.start()
    _wait_until(
        lambda: all(
            job.status in {JobStatus.COMPLETED, JobStatus.FAILED}
            for job in controller.jobs()
        )
    )
    worker.stop()

    assert calls == ["lesson-0.pptx", "lesson-1.pptx"]
    assert [job.status for job in controller.jobs()] == [
        JobStatus.FAILED,
        JobStatus.COMPLETED,
    ]
