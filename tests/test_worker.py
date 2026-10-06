from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from video_workflow.job_models import JobStatus
from video_workflow.json_store import JsonStore
from video_workflow.pipeline import WorkflowCancelled
from video_workflow.queue_controller import QueueController
from video_workflow.worker import QueueWorker

ROOT = Path(__file__).resolve().parents[1]


def test_worker_imports_without_pythoncom_on_linux() -> None:
    code = (
        "import builtins, sys; original=builtins.__import__; "
        "builtins.__import__=lambda name,*a,**k: "
        "(_ for _ in ()).throw(ImportError('blocked')) if name=='pythoncom' "
        "else original(name,*a,**k); "
        "sys.platform='linux'; import video_workflow.worker"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def _controller(
    tmp_path: Path, count: int = 2, *, global_assets: bool = True
) -> QueueController:
    files = [
        tmp_path / name
        for name in (
            "source.mp3",
            "slides.pptx",
            "timeline.txt",
            "logo.png",
            "outro.mp4",
        )
    ]
    for path in files:
        path.write_bytes(b"x")
    output = tmp_path / "output"
    output.mkdir()
    controller = QueueController(JsonStore(tmp_path / "data"))
    if global_assets:
        controller.set_global_assets(files[3], files[4])
    for index in range(count):
        controller.enqueue(
            source_media=files[0],
            pptx=files[1],
            timeline=files[2],
            output_name=f"job-{index}",
            output_directory=output,
        )
    return controller


def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


def test_worker_runs_one_job_at_a_time_and_continues_after_failure(
    tmp_path: Path,
) -> None:
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
    _wait_until(
        lambda: all(
            job.status in {JobStatus.COMPLETED, JobStatus.FAILED}
            for job in controller.jobs()
        )
    )
    worker.stop()

    assert max_active == 1
    assert calls == ["job-0.mp4", "job-1.mp4", "job-2.mp4"]
    assert [job.status for job in controller.jobs()] == [
        JobStatus.FAILED,
        JobStatus.COMPLETED,
        JobStatus.COMPLETED,
    ]


def test_worker_persists_stages_and_emits_events(tmp_path: Path) -> None:
    controller = _controller(tmp_path, 1)
    events: queue.Queue = queue.Queue()

    def build(_request, *, on_stage, **_kwargs):
        for stage in (
            "validating",
            "exporting_slides",
            "rendering_lecture",
            "preparing_outro",
            "joining",
            "verifying",
        ):
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
    worker = QueueWorker(
        controller, build=lambda request, **kwargs: calls.append(request.output.name)
    )
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


def test_worker_builds_job_without_global_assets(tmp_path: Path) -> None:
    controller = _controller(tmp_path, 1, global_assets=False)
    requests = []
    worker = QueueWorker(
        controller, build=lambda request, **_kwargs: requests.append(request)
    )
    worker.start()
    worker.wake()
    try:
        _wait_until(
            lambda: controller.jobs()[0].status is JobStatus.COMPLETED, timeout=0.3
        )
    finally:
        worker.stop(timeout=1)

    assert requests[0].logo is None
    assert requests[0].outro is None


def test_worker_passes_batch_output_settings_to_slide_builder(tmp_path: Path) -> None:
    controller = _controller(tmp_path, 0)
    existing_output = tmp_path / "output" / "lesson_1.mp4"
    existing_output.write_bytes(b"previous-video")
    controller.enqueue(
        source_media=tmp_path / "source.mp3",
        pptx=tmp_path / "slides.pptx",
        timeline=tmp_path / "timeline.txt",
        output_name="lesson_1",
        output_directory=tmp_path / "output",
        write_report=False,
        overwrite_output=True,
    )
    requests = []
    worker = QueueWorker(
        controller, build=lambda request, **_kwargs: requests.append(request)
    )

    worker.start()
    try:
        _wait_until(
            lambda: controller.jobs()[0].status is JobStatus.COMPLETED, timeout=0.3
        )
    finally:
        worker.stop(timeout=1)

    assert requests[0].write_report is False
    assert requests[0].overwrite_output is True
    assert existing_output.read_bytes() == b"previous-video"


def test_worker_uses_global_outro_for_part_2_only(tmp_path: Path) -> None:
    controller = _controller(tmp_path, 0)
    for part, use_outro in ((1, False), (2, True)):
        controller.enqueue(
            source_media=tmp_path / "source.mp3",
            pptx=tmp_path / "slides.pptx",
            timeline=tmp_path / "timeline.txt",
            output_name=f"lesson_{part}",
            output_directory=tmp_path / "output",
            use_outro=use_outro,
        )
    requests = []
    worker = QueueWorker(
        controller, build=lambda request, **_kwargs: requests.append(request)
    )

    worker.start()
    try:
        _wait_until(
            lambda: all(job.status is JobStatus.COMPLETED for job in controller.jobs()),
            timeout=0.3,
        )
    finally:
        worker.stop(timeout=1)

    assert [request.outro for request in requests] == [None, tmp_path / "outro.mp4"]


def test_stop_cancels_build_marks_interrupted_and_balances_com(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _controller(tmp_path, 1)
    entered = threading.Event()
    com_calls: list[str] = []
    monkeypatch.setattr(
        "video_workflow.worker.pythoncom",
        SimpleNamespace(
            CoInitialize=lambda: com_calls.append("init"),
            CoUninitialize=lambda: com_calls.append("uninit"),
        ),
    )

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


def test_stop_after_publish_boundary_allows_worker_to_complete(tmp_path: Path) -> None:
    controller = _controller(tmp_path, 1)
    published = threading.Event()
    release = threading.Event()

    def build(request, **_kwargs):
        request.output.write_bytes(b"published")
        published.set()
        release.wait(1)

    worker = QueueWorker(controller, build=build)
    worker.start()
    assert published.wait(1)
    stopper = threading.Thread(target=worker.stop, kwargs={"timeout": 1})
    stopper.start()
    assert worker._cancel.wait(1)
    release.set()
    stopper.join(1)

    assert not stopper.is_alive()
    assert controller.jobs()[0].status is JobStatus.COMPLETED


def test_stop_between_claim_and_cancel_reset_is_not_lost(tmp_path: Path) -> None:
    controller = _controller(tmp_path, 1)
    claimed = threading.Event()
    release_claim = threading.Event()
    original_claim_next = controller.claim_next

    def blocking_claim_next():
        job = original_claim_next()
        if job is not None:
            claimed.set()
            release_claim.wait(1)
        return job

    controller.claim_next = blocking_claim_next
    cancel_states: list[bool] = []

    def build(_request, *, cancel_event, **_kwargs):
        cancel_states.append(cancel_event.is_set())
        if cancel_event.is_set():
            raise WorkflowCancelled("cancelled")

    worker = QueueWorker(controller, build=build)
    worker.start()
    assert claimed.wait(1)
    stopper = threading.Thread(target=worker.stop, kwargs={"timeout": 1})
    stopper.start()
    assert worker._stop.wait(1)
    release_claim.set()
    stopper.join(1)

    assert not stopper.is_alive()
    assert cancel_states == [True]
    assert controller.jobs()[0].status is JobStatus.INTERRUPTED


def test_worker_com_context_is_noop_on_linux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _controller(tmp_path, 1)
    monkeypatch.setattr("video_workflow.worker.pythoncom", None)
    calls: list[str] = []
    worker = QueueWorker(
        controller,
        build=lambda request, **kwargs: calls.append(request.output.name),
    )
    worker.start()
    _wait_until(lambda: controller.jobs()[0].status is JobStatus.COMPLETED)
    worker.stop()
    assert calls == ["job-0.mp4"]


def test_stop_does_not_lose_wake_between_loop_check_and_wait(tmp_path: Path) -> None:
    controller = _controller(tmp_path, 0)
    worker = QueueWorker(controller, build=lambda *_args, **_kwargs: None)
    clear_entered = threading.Event()
    release_clear = threading.Event()
    original_clear = worker._wake.clear

    def blocking_clear() -> None:
        clear_entered.set()
        release_clear.wait(1)
        original_clear()

    worker._wake.clear = blocking_clear
    worker.start()
    assert clear_entered.wait(1)

    stopper = threading.Thread(target=worker.stop, kwargs={"timeout": 0.05})
    stopper.start()
    stopper.join(1)
    assert not stopper.is_alive()
    release_clear.set()
    time.sleep(0.05)

    was_alive = worker.is_alive()
    if was_alive:
        worker._wake.set()
        worker._thread.join(1)

    assert was_alive is False


def test_worker_dispatches_merge_job_and_persists_merge_stages(tmp_path: Path) -> None:
    controller = _controller(tmp_path, 0)
    job = controller.enqueue_merge(
        first_video=tmp_path / "source.mp3",
        second_video=tmp_path / "outro.mp4",
        output_name="merged",
        output_directory=tmp_path / "output",
    )
    requests = []
    events: queue.Queue = queue.Queue()

    def unexpected_slide_build(*_args, **_kwargs):
        raise AssertionError("slide builder must not handle merge jobs")

    def merge_build(request, *, on_stage, **_kwargs):
        requests.append(request)
        for stage in (
            "validating",
            "normalizing_first",
            "normalizing_second",
            "joining",
            "verifying",
        ):
            on_stage(stage)

    worker = QueueWorker(
        controller,
        build=unexpected_slide_build,
        merge_build=merge_build,
        events=events,
    )
    worker.start()
    worker.wake()
    try:
        _wait_until(lambda: controller.jobs()[0].status is JobStatus.COMPLETED)
    finally:
        worker.stop()

    assert requests[0].first_video == job.source_media
    assert requests[0].second_video == job.secondary_media
    assert requests[0].output == job.output_path
    stage_messages = []
    while not events.empty():
        event = events.get_nowait()
        if event.kind == "stage":
            stage_messages.append(event.message)
    assert stage_messages == [
        "validating",
        "normalizing_first",
        "normalizing_second",
        "joining",
        "verifying",
    ]


def test_worker_passes_batch_output_settings_to_merge_builder(tmp_path: Path) -> None:
    controller = _controller(tmp_path, 0)
    parts = []
    for part in (1, 2):
        job = controller.enqueue(
            source_media=tmp_path / "source.mp3",
            pptx=tmp_path / "slides.pptx",
            timeline=tmp_path / "timeline.txt",
            output_name=f"lesson_{part}",
            output_directory=tmp_path / "output",
            overwrite_output=True,
        )
        assert controller.claim_next().id == job.id
        job.output_path.write_bytes(f"part-{part}".encode())
        controller.mark_completed(job.id)
        parts.append(job)
    merged = controller.enqueue_merge(
        first_video=parts[0].output_path,
        second_video=parts[1].output_path,
        output_name="lesson",
        output_directory=tmp_path / "output",
        write_report=False,
        overwrite_output=True,
        dependency_job_ids=(parts[0].id, parts[1].id),
    )
    requests = []
    worker = QueueWorker(
        controller,
        build=lambda *_args, **_kwargs: None,
        merge_build=lambda request, **_kwargs: requests.append(request),
    )

    worker.start()
    try:
        _wait_until(
            lambda: next(job for job in controller.jobs() if job.id == merged.id).status
            is JobStatus.COMPLETED
        )
    finally:
        worker.stop(timeout=1)

    assert requests[0].first_video.name == "lesson_1.mp4"
    assert requests[0].second_video.name == "lesson_2.mp4"
    assert requests[0].write_report is False
    assert requests[0].overwrite_output is True


@pytest.mark.integration
def test_default_worker_runs_merge_pipeline_from_queue(tmp_path: Path) -> None:
    controller = _controller(tmp_path, 0)
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    for path, color, size, fps in (
        (first, "red", "320x240", 12),
        (second, "blue", "240x320", 30),
    ):
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s={size}:r={fps}:d=0.25",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-y",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
    job = controller.enqueue_merge(
        first_video=first,
        second_video=second,
        output_name="merged",
        output_directory=tmp_path / "output",
    )
    worker = QueueWorker(controller)
    worker.start()
    worker.wake()
    try:
        _wait_until(
            lambda: controller.jobs()[0].status
            in {JobStatus.COMPLETED, JobStatus.FAILED}
        )
    finally:
        worker.stop()

    assert controller.jobs()[0].status is JobStatus.COMPLETED
    assert job.output_path.stat().st_size > 0
