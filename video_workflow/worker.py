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

from .job_models import JobRecord, JobStage, JobStatus
from .pipeline import (
    NarratedBuildReport,
    WorkflowCancelled,
    build_input_signature,
    build_narrated_video,
)
from .queue_controller import QueueController
from .voicestudio_supervisor import VoiceStudioSupervisor


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


BuildFunction = Callable[..., NarratedBuildReport | object | None]


class QueueWorker:
    def __init__(
        self,
        controller: QueueController,
        *,
        build: BuildFunction = build_narrated_video,
        supervisor_factory: Callable[..., VoiceStudioSupervisor] = VoiceStudioSupervisor,
        events: queue.Queue[WorkerEvent] | None = None,
    ) -> None:
        self.controller = controller
        self.build = build
        self._supervisor_factory = supervisor_factory
        self.events: queue.Queue[WorkerEvent] = events if events is not None else queue.Queue()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._current_job_id: str | None = None
        self._supervisor: VoiceStudioSupervisor | None = None
        self._supervisor_key: tuple[str, str, str | None, str] | None = None

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

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._cancel.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def cancel_current(self) -> None:
        self._cancel.set()

    def _status(self, job_id: str) -> JobStatus | None:
        return next(
            (job.status for job in self.controller.jobs() if job.id == job_id), None
        )

    def _emit_stage(self, job_id: str, stage: str) -> None:
        self.controller.set_stage(job_id, JobStage(stage))
        self.events.put(WorkerEvent("stage", job_id, stage))

    def _emit_progress(self, job_id: str, current: int, total: int) -> None:
        self.controller.set_progress(job_id, current, total)
        self.events.put(WorkerEvent("progress", job_id, f"{current}/{total}"))

    @staticmethod
    def _key(job: JobRecord) -> tuple[str, str, str | None, str]:
        return (
            job.voicestudio_api_url,
            job.profile_id,
            str(job.backend_override) if job.backend_override is not None else None,
            job.inference_config_version,
        )

    def _ready_client(self, job: JobRecord):
        key = self._key(job)
        if self._supervisor is not None and self._supervisor_key != key:
            self._supervisor.close()
            self._supervisor = None
            self._supervisor_key = None
        if self._supervisor is None:
            self._supervisor = self._supervisor_factory(
                config=job.voice_config(),
                backend_override=job.backend_override,
                log_directory=self.controller.store.root / "logs",
            )
            self._supervisor_key = key
        return self._supervisor.ensure_ready()

    def _run_job(self, job: JobRecord) -> None:
        request = self.controller.request_for(job)
        if build_input_signature(request) != job.input_signature:
            raise RuntimeError("job inputs changed after it was queued; scan the folder again")
        self._emit_stage(job.id, JobStage.STARTING_VOICESTUDIO.value)
        client = self._ready_client(job)
        self._emit_stage(job.id, JobStage.VALIDATING_PROFILE.value)
        self.build(
            request,
            client=client,
            cache_root=self.controller.store.root / "tts-cache",
            on_stage=lambda stage: self._emit_stage(job.id, stage),
            on_progress=lambda current, total: self._emit_progress(
                job.id, current, total
            ),
            cancel_event=self._cancel,
            log_path=self.controller.store.root / "logs" / f"{job.id}.log",
        )

    def _run(self) -> None:
        try:
            with _com_context():
                while not self._stop.is_set():
                    job = self.controller.claim_next()
                    if job is None:
                        self._wake.wait(0.5)
                        self._wake.clear()
                        continue
                    self._cancel.clear()
                    if self._stop.is_set():
                        self._cancel.set()
                    self._set_current(job.id)
                    self.events.put(WorkerEvent("started", job.id))
                    try:
                        self._run_job(job)
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
                            self.events.put(WorkerEvent("completed", job.id))
                    finally:
                        self._set_current(None)
        finally:
            if self._supervisor is not None:
                self._supervisor.close()
                self._supervisor = None
                self._supervisor_key = None
