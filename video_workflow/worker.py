from __future__ import annotations

import queue
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable

if sys.platform == "win32":
    import pythoncom
else:
    pythoncom = None

from .job_models import JobKind, JobStage, JobStatus
from .merge_pipeline import MergeRequest, merge_videos
from .pipeline import BuildReport, BuildRequest, WorkflowCancelled, build_video
from .queue_controller import QueueController, QueueStateError


@contextmanager
def _com_context():
    if pythoncom is None:
        yield
        return
    pythoncom.CoInitialize()
    try:
        yield
    finally:
        pythoncom.CoUninitialize()


@dataclass(frozen=True, slots=True)
class WorkerEvent:
    kind: str
    job_id: str | None = None
    message: str | None = None


BuildFunction = Callable[..., BuildReport | object | None]


class QueueWorker:
    def __init__(
        self,
        controller: QueueController,
        *,
        build: BuildFunction = build_video,
        merge_build: BuildFunction | None = merge_videos,
        events: queue.Queue[WorkerEvent] | None = None,
    ) -> None:
        self.controller = controller
        self.build, self.merge_build = build, merge_build
        self.events: queue.Queue[WorkerEvent] = (
            events if events is not None else queue.Queue()
        )
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._current_job_id: str | None = None

    @property
    def current_job_id(self) -> str | None:
        with self._state_lock:
            return self._current_job_id

    def _set_current(self, value: str | None) -> None:
        with self._state_lock:
            self._current_job_id = value

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.is_alive():
            return
        self._stop.clear()
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._run, name="video-workflow-worker", daemon=True
        )
        self._thread.start()
        self.wake()

    def wake(self) -> None:
        self._wake.set()

    def _status(self, job_id: str) -> JobStatus | None:
        return next(
            (job.status for job in self.controller.jobs() if job.id == job_id), None
        )

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._cancel.set()
        current = self.current_job_id
        if current is not None and self._status(current) is JobStatus.RUNNING:
            try:
                self.controller.mark_interrupted(current, "application closed")
            except QueueStateError:
                pass
            self.events.put(WorkerEvent("interrupted", current, "application closed"))
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _emit_stage(self, job_id: str, stage: str) -> None:
        self.controller.set_stage(job_id, JobStage(stage))
        self.events.put(WorkerEvent("stage", job_id, stage))

    def _run(self) -> None:
        with _com_context():
            while not self._stop.is_set():
                job = self.controller.claim_next()
                if job is None:
                    self._wake.wait()
                    self._wake.clear()
                    continue
                self._cancel.clear()
                self._set_current(job.id)
                self.events.put(WorkerEvent("started", job.id, None))
                log_path = self.controller.store.root / "logs" / f"{job.id}.log"
                try:
                    if job.kind is JobKind.MERGE:
                        if job.secondary_media is None or self.merge_build is None:
                            raise RuntimeError("merge builder is not configured")
                        request = MergeRequest(
                            first_video=job.source_media,
                            second_video=job.secondary_media,
                            output=job.output_path,
                        )
                        builder = self.merge_build
                    else:
                        if job.pptx is None or job.timeline is None:
                            raise RuntimeError("slide job is missing PPTX or timeline")
                        settings = self.controller.settings
                        request = BuildRequest(
                            source_media=job.source_media,
                            pptx=job.pptx,
                            timeline=job.timeline,
                            logo=settings.logo,
                            outro=settings.outro if job.use_outro else None,
                            output=job.output_path,
                            write_report=job.write_report,
                        )
                        builder = self.build
                    builder(
                        request,
                        on_stage=lambda stage, job_id=job.id: self._emit_stage(
                            job_id, stage
                        ),
                        cancel_event=self._cancel,
                        log_path=log_path,
                    )
                except WorkflowCancelled as exc:
                    if self._status(job.id) is JobStatus.RUNNING:
                        self.controller.mark_interrupted(job.id, str(exc))
                    self.events.put(WorkerEvent("interrupted", job.id, str(exc)))
                except Exception as exc:
                    if self._status(job.id) is JobStatus.RUNNING:
                        self.controller.mark_failed(job.id, str(exc))
                    self.events.put(WorkerEvent("failed", job.id, str(exc)))
                else:
                    if self._status(job.id) is JobStatus.RUNNING:
                        self.controller.mark_completed(job.id)
                        self.events.put(WorkerEvent("completed", job.id, None))
                finally:
                    self._set_current(None)
