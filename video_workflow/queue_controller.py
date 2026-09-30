from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

from .job_models import GlobalSettings, JobRecord, JobStage, JobStatus, utc_now
from .json_store import JsonStore


class QueueStateError(RuntimeError):
    pass


class QueueController:
    def __init__(self, store: JsonStore) -> None:
        self.store = store
        self._lock = threading.RLock()
        self._settings = store.load_settings()
        self._jobs = store.load_jobs()

    @property
    def settings(self) -> GlobalSettings:
        with self._lock:
            return self._settings

    def jobs(self) -> tuple[JobRecord, ...]:
        with self._lock:
            return tuple(self._jobs)

    def reload(self) -> None:
        with self._lock:
            self._settings = self.store.load_settings()
            self._jobs = self.store.load_jobs()

    def set_global_assets(self, logo: Path | None, outro: Path | None) -> GlobalSettings:
        logo = Path(logo) if logo is not None else None
        outro = Path(outro) if outro is not None else None
        if logo is not None and not logo.is_file():
            raise ValueError(f"logo file does not exist: {logo}")
        if outro is not None and not outro.is_file():
            raise ValueError(f"outro file does not exist: {outro}")
        with self._lock:
            self._settings = GlobalSettings(logo, outro)
            self.store.save_settings(self._settings)
            return self._settings

    def _validate_job_paths(self, job: JobRecord) -> str | None:
        for label in ("source_media", "pptx", "timeline"):
            path = Path(getattr(job, label))
            if not path.is_file():
                return f"{label} file does not exist: {path}"
        if not job.output_directory.is_dir():
            return f"output directory does not exist: {job.output_directory}"
        if job.output_path.exists():
            return f"output already exists: {job.output_path}"
        return None

    def enqueue(
        self,
        *,
        source_media: Path,
        pptx: Path,
        timeline: Path,
        output_name: str,
        output_directory: Path,
    ) -> JobRecord:
        job = JobRecord.new(
            source_media=source_media,
            pptx=pptx,
            timeline=timeline,
            output_name=output_name,
            output_directory=output_directory,
        )
        error = self._validate_job_paths(job)
        if error:
            raise ValueError(error)
        with self._lock:
            self._jobs.append(job)
            self.store.save_jobs(self._jobs)
        return job

    def _globals_ready(self) -> bool:
        return (
            (self._settings.logo is None or self._settings.logo.is_file())
            and (self._settings.outro is None or self._settings.outro.is_file())
        )

    def claim_next(self) -> JobRecord | None:
        with self._lock:
            if any(job.status is JobStatus.RUNNING for job in self._jobs) or not self._globals_ready():
                return None
            changed = False
            for index, job in enumerate(self._jobs):
                if job.status is not JobStatus.WAITING:
                    continue
                error = self._validate_job_paths(job)
                if error:
                    self._jobs[index] = replace(
                        job, status=JobStatus.FAILED, error=error, stage=None, finished_at=utc_now()
                    )
                    changed = True
                    continue
                claimed = replace(
                    job,
                    status=JobStatus.RUNNING,
                    stage=JobStage.VALIDATING,
                    error=None,
                    started_at=utc_now(),
                    finished_at=None,
                )
                self._jobs[index] = claimed
                self.store.save_jobs(self._jobs)
                return claimed
            if changed:
                self.store.save_jobs(self._jobs)
            return None

    def _index(self, job_id: str) -> int:
        for index, job in enumerate(self._jobs):
            if job.id == job_id:
                return index
        raise QueueStateError(f"unknown job: {job_id}")

    def set_stage(self, job_id: str, stage: JobStage | str) -> JobRecord:
        stage = JobStage(stage)
        with self._lock:
            index = self._index(job_id)
            job = self._jobs[index]
            if job.status is not JobStatus.RUNNING:
                raise QueueStateError("stage can only be changed for a running job")
            updated = replace(job, stage=stage)
            self._jobs[index] = updated
            self.store.save_jobs(self._jobs)
            return updated

    def _finish(self, job_id: str, status: JobStatus, error: str | None) -> JobRecord:
        with self._lock:
            index = self._index(job_id)
            job = self._jobs[index]
            if job.status is not JobStatus.RUNNING:
                raise QueueStateError(f"only a running job can become {status.value}")
            updated = replace(job, status=status, stage=None, error=error, finished_at=utc_now())
            self._jobs[index] = updated
            self.store.save_jobs(self._jobs)
            return updated

    def mark_completed(self, job_id: str) -> JobRecord:
        return self._finish(job_id, JobStatus.COMPLETED, None)

    def mark_failed(self, job_id: str, error: str) -> JobRecord:
        return self._finish(job_id, JobStatus.FAILED, error)

    def mark_interrupted(self, job_id: str, error: str = "application closed") -> JobRecord:
        return self._finish(job_id, JobStatus.INTERRUPTED, error)

    def retry(self, job_id: str) -> JobRecord:
        with self._lock:
            index = self._index(job_id)
            job = self._jobs[index]
            if job.status not in {JobStatus.FAILED, JobStatus.INTERRUPTED}:
                raise QueueStateError("only failed or interrupted jobs can be retried")
            retried = replace(
                job, status=JobStatus.WAITING, stage=None, error=None, started_at=None, finished_at=None,
                created_at=utc_now(),
            )
            error = self._validate_job_paths(retried)
            if error:
                raise ValueError(error)
            self._jobs.pop(index)
            self._jobs.append(retried)
            self.store.save_jobs(self._jobs)
            return retried

    def remove(self, job_id: str) -> None:
        with self._lock:
            index = self._index(job_id)
            if self._jobs[index].status is not JobStatus.WAITING:
                raise QueueStateError("only waiting jobs can be removed")
            self._jobs.pop(index)
            self.store.save_jobs(self._jobs)

    def recover_startup(self) -> None:
        with self._lock:
            changed = False
            recovered: list[JobRecord] = []
            for job in self._jobs:
                if job.status is JobStatus.RUNNING:
                    job = replace(
                        job,
                        status=JobStatus.INTERRUPTED,
                        stage=None,
                        error="application closed during processing",
                        finished_at=utc_now(),
                    )
                    changed = True
                recovered.append(job)
            self._jobs = recovered
            if changed:
                self.store.save_jobs(self._jobs)
