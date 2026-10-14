from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

from .job_models import GlobalSettings, JobRecord, JobStage, JobStatus, utc_now
from .json_store import JsonStore
from .pipeline import NarratedBuildRequest, build_input_signature


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
        if logo is None or outro is None:
            raise ValueError("logo and outro are required")
        logo, outro = Path(logo), Path(outro)
        if not logo.is_file():
            raise ValueError(f"logo file does not exist: {logo}")
        if not outro.is_file():
            raise ValueError(f"outro file does not exist: {outro}")
        with self._lock:
            self._settings = replace(self._settings, logo=logo, outro=outro)
            self.store.save_settings(self._settings)
            return self._settings

    def set_voicestudio_settings(
        self,
        *,
        api_url: str,
        profile_id: str,
        backend_override: Path | None,
        inference_config_version: str,
    ) -> GlobalSettings:
        with self._lock:
            self._settings = replace(
                self._settings,
                voicestudio_api_url=api_url,
                profile_id=profile_id,
                backend_override=(
                    Path(backend_override) if backend_override is not None else None
                ),
                inference_config_version=inference_config_version,
            )
            self._settings.voice_config()
            self.store.save_settings(self._settings)
            return self._settings

    @staticmethod
    def request_for(job: JobRecord) -> NarratedBuildRequest:
        return NarratedBuildRequest(
            pptx=job.pptx,
            script=job.script,
            logo=job.logo,
            outro=job.outro,
            artifact_directory=job.artifact_directory,
            profile_config=job.voice_config(),
        )

    def _validate_job_paths(self, job: JobRecord) -> str | None:
        for label in ("pptx", "script", "logo", "outro"):
            path = Path(getattr(job, label))
            if not path.is_file():
                return f"{label} file does not exist: {path}"
        if not job.artifact_directory.parent.is_dir():
            return (
                "artifact parent directory does not exist: "
                f"{job.artifact_directory.parent}"
            )
        return None

    def enqueue(
        self,
        *,
        pptx: Path,
        script: Path,
        artifact_directory: Path,
        output_name: str,
    ) -> JobRecord:
        with self._lock:
            settings = self._settings
            if settings.logo is None or settings.outro is None:
                raise ValueError("logo and outro are required before adding jobs")
            artifact_directory = Path(artifact_directory)
            artifact_directory.parent.mkdir(parents=True, exist_ok=True)
            request = NarratedBuildRequest(
                pptx=Path(pptx),
                script=Path(script),
                logo=settings.logo,
                outro=settings.outro,
                artifact_directory=artifact_directory,
                profile_config=settings.voice_config(),
            )
            job = JobRecord.new(
                pptx=request.pptx,
                script=request.script,
                artifact_directory=request.artifact_directory,
                output_name=output_name,
                logo=request.logo,
                outro=request.outro,
                voicestudio_api_url=settings.voicestudio_api_url,
                profile_id=settings.profile_id,
                backend_override=settings.backend_override,
                inference_config_version=settings.inference_config_version,
                input_signature=build_input_signature(request),
            )
            error = self._validate_job_paths(job)
            if error:
                raise ValueError(error)
            self._jobs.append(job)
            self.store.save_jobs(self._jobs)
            return job

    def claim_next(self) -> JobRecord | None:
        with self._lock:
            if any(job.status is JobStatus.RUNNING for job in self._jobs):
                return None
            changed = False
            for index, job in enumerate(self._jobs):
                if job.status is not JobStatus.WAITING:
                    continue
                error = self._validate_job_paths(job)
                if error:
                    self._jobs[index] = replace(
                        job,
                        status=JobStatus.FAILED,
                        stage=None,
                        error=error,
                        finished_at=utc_now(),
                    )
                    changed = True
                    continue
                claimed = replace(
                    job,
                    status=JobStatus.RUNNING,
                    stage=JobStage.VALIDATING,
                    progress_current=0,
                    progress_total=0,
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

    def set_progress(self, job_id: str, current: int, total: int) -> JobRecord:
        if current < 0 or total < 0 or current > total:
            raise ValueError("job progress must satisfy 0 <= current <= total")
        with self._lock:
            index = self._index(job_id)
            job = self._jobs[index]
            if job.status is not JobStatus.RUNNING:
                raise QueueStateError("progress can only be changed for a running job")
            updated = replace(job, progress_current=current, progress_total=total)
            self._jobs[index] = updated
            self.store.save_jobs(self._jobs)
            return updated

    def _finish(self, job_id: str, status: JobStatus, error: str | None) -> JobRecord:
        with self._lock:
            index = self._index(job_id)
            job = self._jobs[index]
            if job.status is not JobStatus.RUNNING:
                raise QueueStateError(f"only a running job can become {status.value}")
            updated = replace(
                job,
                status=status,
                stage=None,
                error=error,
                finished_at=utc_now(),
            )
            self._jobs[index] = updated
            self.store.save_jobs(self._jobs)
            return updated

    def mark_completed(self, job_id: str) -> JobRecord:
        return self._finish(job_id, JobStatus.COMPLETED, None)

    def mark_failed(self, job_id: str, error: str) -> JobRecord:
        return self._finish(job_id, JobStatus.FAILED, error)

    def mark_interrupted(
        self, job_id: str, error: str = "application closed"
    ) -> JobRecord:
        return self._finish(job_id, JobStatus.INTERRUPTED, error)

    def retry(self, job_id: str) -> JobRecord:
        with self._lock:
            index = self._index(job_id)
            job = self._jobs[index]
            if job.status not in {JobStatus.FAILED, JobStatus.INTERRUPTED}:
                raise QueueStateError("only failed or interrupted jobs can be retried")
            retried = replace(
                job,
                status=JobStatus.WAITING,
                stage=None,
                progress_current=0,
                progress_total=0,
                error=None,
                started_at=None,
                finished_at=None,
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

    def restore_available_path_jobs(self) -> tuple[JobRecord, ...]:
        with self._lock:
            restored: list[JobRecord] = []
            updated_jobs: list[JobRecord] = []
            for job in self._jobs:
                path_error = bool(job.error and " file does not exist:" in job.error)
                if (
                    job.status is JobStatus.FAILED
                    and path_error
                    and self._validate_job_paths(job) is None
                ):
                    job = replace(
                        job,
                        status=JobStatus.WAITING,
                        error=None,
                        stage=None,
                        started_at=None,
                        finished_at=None,
                        created_at=utc_now(),
                    )
                    restored.append(job)
                updated_jobs.append(job)
            if restored:
                self._jobs = updated_jobs
                self.store.save_jobs(self._jobs)
            return tuple(restored)
