from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from ..job_models import JobStatus, utc_now
from .output_location import output_paths
from .runner import RunResult, Update, describe_error
from .state import WebJob, WebStore


class DuplicateJobError(ValueError):
    pass


class JobStateError(ValueError):
    pass


RunFn = Callable[[WebJob, threading.Event, Update], RunResult]


class JobScheduler:
    def __init__(
        self,
        store: WebStore,
        run: RunFn,
        *,
        max_active: int = 3,
    ) -> None:
        if max_active <= 0:
            raise ValueError("max_active must be positive")
        self._store = store
        self._run = run
        self._max_active = max_active
        self._condition = threading.Condition(threading.RLock())
        self._jobs = store.load_jobs()
        self._cancel_events: dict[str, threading.Event] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._dispatcher: threading.Thread | None = None
        self._stopping = False
        changed = False
        for index, job in enumerate(self._jobs):
            if job.status is JobStatus.RUNNING:
                self._jobs[index] = replace(
                    job,
                    status=JobStatus.INTERRUPTED,
                    error="Máy chủ đã khởi động lại khi đang xử lý",
                    finished_at=utc_now(),
                )
                changed = True
        if changed:
            self._store.save_jobs(self._jobs)

    def start(self) -> None:
        with self._condition:
            if self._dispatcher is not None and self._dispatcher.is_alive():
                return
            self._stopping = False
            self._dispatcher = threading.Thread(
                target=self._dispatch,
                name="web-job-dispatcher",
                daemon=True,
            )
            self._dispatcher.start()

    def stop(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        with self._condition:
            self._stopping = True
            for cancel_event in self._cancel_events.values():
                cancel_event.set()
            self._condition.notify_all()
            dispatcher = self._dispatcher
        if dispatcher is not None:
            dispatcher.join(max(0.0, deadline - time.monotonic()))
        with self._condition:
            workers = list(self._workers.values())
        for worker in workers:
            worker.join(max(0.0, deadline - time.monotonic()))

    def _save(self) -> None:
        self._store.save_jobs(self._jobs)

    def _index(self, job_id: str) -> int:
        for index, job in enumerate(self._jobs):
            if job.id == job_id:
                return index
        raise JobStateError(f"Không tìm thấy job {job_id}")

    def _active_leaf(self, batch_id: str, leaf_relpath: str, *, exclude: str | None = None) -> bool:
        return any(
            job.id != exclude
            and job.batch_id == batch_id
            and job.leaf_relpath == leaf_relpath
            and job.status in {JobStatus.WAITING, JobStatus.RUNNING}
            for job in self._jobs
        )

    def _active_output_job(
        self,
        output_root: str,
        leaf_relpath: str,
        *,
        exclude: str | None = None,
    ) -> WebJob | None:
        destination = output_paths(Path(output_root), leaf_relpath)
        for job in self._jobs:
            if (
                job.id != exclude
                and job.output_root is not None
                and job.status in {JobStatus.WAITING, JobStatus.RUNNING}
                and output_paths(Path(job.output_root), job.leaf_relpath)
                == destination
            ):
                return job
        return None

    def enqueue(
        self,
        batch_id: str,
        leaves: Sequence[tuple[str, int]],
        profile_id: str,
        output_root: str | None = None,
    ) -> list[WebJob]:
        with self._condition:
            seen: set[str] = set()
            seen_outputs: dict[tuple[Path, Path], str] = {}
            for leaf_relpath, _script_chars in leaves:
                if leaf_relpath in seen or self._active_leaf(batch_id, leaf_relpath):
                    raise DuplicateJobError(
                        f"Bài {leaf_relpath} đang chờ hoặc đang chạy"
                    )
                seen.add(leaf_relpath)
                if output_root is not None:
                    destination = output_paths(Path(output_root), leaf_relpath)
                    previous = seen_outputs.get(destination)
                    if previous is not None:
                        raise DuplicateJobError(
                            f"Trùng vị trí lưu với bài {previous}"
                        )
                    active = self._active_output_job(output_root, leaf_relpath)
                    if active is not None:
                        raise DuplicateJobError(
                            f"Trùng vị trí lưu với bài {active.leaf_relpath}"
                        )
                    seen_outputs[destination] = leaf_relpath
            created = [
                WebJob.new(
                    batch_id=batch_id,
                    leaf_relpath=leaf_relpath,
                    profile_id=profile_id,
                    script_chars=script_chars,
                    output_root=output_root,
                )
                for leaf_relpath, script_chars in leaves
            ]
            self._jobs.extend(created)
            self._save()
            self._condition.notify_all()
            return created

    def cancel(self, job_id: str) -> WebJob:
        with self._condition:
            index = self._index(job_id)
            job = self._jobs[index]
            if job.status is JobStatus.WAITING:
                job = replace(
                    job,
                    status=JobStatus.INTERRUPTED,
                    error="Đã huỷ",
                    stage=None,
                    finished_at=utc_now(),
                )
                self._jobs[index] = job
                self._save()
                self._condition.notify_all()
                return job
            if job.status is JobStatus.RUNNING:
                self._cancel_events[job.id].set()
                return job
            raise JobStateError("Chỉ có thể huỷ job đang chờ hoặc đang chạy")

    def retry(self, job_id: str) -> WebJob:
        with self._condition:
            index = self._index(job_id)
            job = self._jobs[index]
            if job.status not in {JobStatus.FAILED, JobStatus.INTERRUPTED}:
                raise JobStateError("Chỉ có thể thử lại job lỗi hoặc đã gián đoạn")
            if self._active_leaf(job.batch_id, job.leaf_relpath, exclude=job.id):
                raise DuplicateJobError(
                    f"Bài {job.leaf_relpath} đang chờ hoặc đang chạy"
                )
            if job.output_root is not None:
                active = self._active_output_job(
                    job.output_root,
                    job.leaf_relpath,
                    exclude=job.id,
                )
                if active is not None:
                    raise DuplicateJobError(
                        f"Trùng vị trí lưu với bài {active.leaf_relpath}"
                    )
            retried = replace(
                job,
                status=JobStatus.WAITING,
                stage=None,
                progress_current=0,
                progress_total=0,
                error=None,
                created_at=utc_now(),
                started_at=None,
                finished_at=None,
                video_path=None,
                vtt_path=None,
                tts_seconds=None,
            )
            self._jobs[index] = retried
            self._save()
            self._condition.notify_all()
            return retried

    def jobs(self) -> tuple[WebJob, ...]:
        with self._condition:
            return tuple(self._jobs)

    def update_output_paths(
        self, job_id: str, video_path: str, vtt_path: str
    ) -> WebJob:
        with self._condition:
            index = self._index(job_id)
            updated = replace(
                self._jobs[index], video_path=video_path, vtt_path=vtt_path
            )
            self._jobs[index] = updated
            self._save()
            return updated

    def _dispatch(self) -> None:
        while True:
            with self._condition:
                if self._stopping:
                    return
                running = sum(
                    job.status is JobStatus.RUNNING for job in self._jobs
                )
                waiting = sorted(
                    (
                        (index, job)
                        for index, job in enumerate(self._jobs)
                        if job.status is JobStatus.WAITING
                    ),
                    key=lambda pair: pair[1].created_at,
                )
                while running < self._max_active and waiting:
                    index, job = waiting.pop(0)
                    running_job = replace(
                        job,
                        status=JobStatus.RUNNING,
                        started_at=utc_now(),
                    )
                    self._jobs[index] = running_job
                    cancel_event = threading.Event()
                    self._cancel_events[job.id] = cancel_event
                    worker = threading.Thread(
                        target=self._execute,
                        args=(running_job, cancel_event),
                        name=f"web-job-{job.id[:8]}",
                        daemon=True,
                    )
                    self._workers[job.id] = worker
                    worker.start()
                    running += 1
                self._save()
                self._condition.wait(timeout=0.1)

    def _execute(self, job: WebJob, cancel_event: threading.Event) -> None:
        def update(
            stage: str | None,
            progress_current: int | None,
            progress_total: int | None,
        ) -> None:
            with self._condition:
                index = self._index(job.id)
                current = self._jobs[index]
                self._jobs[index] = replace(
                    current,
                    stage=current.stage if stage is None else stage,
                    progress_current=(
                        current.progress_current
                        if progress_current is None
                        else progress_current
                    ),
                    progress_total=(
                        current.progress_total
                        if progress_total is None
                        else progress_total
                    ),
                )
                self._save()

        try:
            result = self._run(job, cancel_event, update)
        except BaseException as exc:
            message = describe_error(exc)
            status = (
                JobStatus.INTERRUPTED
                if cancel_event.is_set() or message == "Đã huỷ"
                else JobStatus.FAILED
            )
            with self._condition:
                index = self._index(job.id)
                self._jobs[index] = replace(
                    self._jobs[index],
                    status=status,
                    error="Đã huỷ" if status is JobStatus.INTERRUPTED else message,
                    finished_at=utc_now(),
                )
                self._save()
                self._finish_worker(job.id)
        else:
            with self._condition:
                index = self._index(job.id)
                status = (
                    JobStatus.INTERRUPTED
                    if cancel_event.is_set()
                    else JobStatus.COMPLETED
                )
                self._jobs[index] = replace(
                    self._jobs[index],
                    status=status,
                    error="Đã huỷ" if status is JobStatus.INTERRUPTED else None,
                    video_path=(
                        None if status is JobStatus.INTERRUPTED else str(result.video_path)
                    ),
                    vtt_path=(
                        None
                        if status is JobStatus.INTERRUPTED or result.vtt_path is None
                        else str(result.vtt_path)
                    ),
                    tts_seconds=(
                        None if status is JobStatus.INTERRUPTED else result.tts_seconds
                    ),
                    finished_at=utc_now(),
                )
                self._save()
                self._finish_worker(job.id)

    def _finish_worker(self, job_id: str) -> None:
        self._cancel_events.pop(job_id, None)
        self._workers.pop(job_id, None)
        self._condition.notify_all()
