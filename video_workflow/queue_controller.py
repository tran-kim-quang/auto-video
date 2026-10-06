from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

from .job_models import GlobalSettings, JobKind, JobRecord, JobStage, JobStatus, utc_now
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

    def set_global_assets(
        self, logo: Path | None, outro: Path | None
    ) -> GlobalSettings:
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

    def _validate_job_paths(
        self, job: JobRecord, *, allow_missing_inputs: bool = False
    ) -> str | None:
        if not allow_missing_inputs:
            if job.kind is JobKind.MERGE:
                required = (
                    ("source_media", job.source_media),
                    ("second_video", job.secondary_media),
                )
            else:
                required = (
                    ("source_media", job.source_media),
                    ("pptx", job.pptx),
                    ("timeline", job.timeline),
                )
            for label, path in required:
                if path is None or not Path(path).is_file():
                    return f"{label} file does not exist: {path}"
        if not job.output_directory.is_dir():
            return f"output directory does not exist: {job.output_directory}"
        if job.output_path.exists() and not job.overwrite_output:
            return f"output already exists: {job.output_path}"
        return None

    def dependency_state(self, job: JobRecord) -> tuple[bool, str | None]:
        with self._lock:
            if not job.dependency_job_ids:
                return True, None
            jobs_by_id = {candidate.id: candidate for candidate in self._jobs}
            for dependency_id in job.dependency_job_ids:
                if dependency_id not in jobs_by_id:
                    return False, f"missing dependency: {dependency_id}"
            for dependency_id in job.dependency_job_ids:
                dependency = jobs_by_id[dependency_id]
                if dependency.status is not JobStatus.COMPLETED:
                    return False, None
            return True, None

    def enqueue(
        self,
        *,
        source_media: Path,
        pptx: Path,
        timeline: Path,
        output_name: str,
        output_directory: Path,
        write_report: bool = True,
        use_outro: bool = True,
        overwrite_output: bool = False,
    ) -> JobRecord:
        job = JobRecord.new(
            source_media=source_media,
            pptx=pptx,
            timeline=timeline,
            output_name=output_name,
            output_directory=output_directory,
            write_report=write_report,
            use_outro=use_outro,
            overwrite_output=overwrite_output,
        )
        error = self._validate_job_paths(job)
        if error:
            raise ValueError(error)
        with self._lock:
            self._jobs.append(job)
            self.store.save_jobs(self._jobs)
        return job

    def enqueue_merge(
        self,
        *,
        first_video: Path,
        second_video: Path,
        output_name: str,
        output_directory: Path,
        write_report: bool = True,
        overwrite_output: bool = False,
        dependency_job_ids: tuple[str, ...] = (),
    ) -> JobRecord:
        job = JobRecord.new_merge(
            first_video=first_video,
            second_video=second_video,
            output_name=output_name,
            output_directory=output_directory,
            write_report=write_report,
            overwrite_output=overwrite_output,
            dependency_job_ids=dependency_job_ids,
        )
        with self._lock:
            _ready, dependency_error = self.dependency_state(job)
            if dependency_error:
                raise ValueError(dependency_error)
            error = self._validate_job_paths(
                job, allow_missing_inputs=bool(job.dependency_job_ids)
            )
            if error:
                raise ValueError(error)
            self._jobs.append(job)
            self.store.save_jobs(self._jobs)
        return job

    def _globals_ready(self, job: JobRecord) -> bool:
        logo_ready = self._settings.logo is None or self._settings.logo.is_file()
        outro_ready = (
            not job.use_outro
            or self._settings.outro is None
            or self._settings.outro.is_file()
        )
        return logo_ready and outro_ready

    def _dependency_group_globals_ready(self, job: JobRecord) -> bool:
        """Keep both renders of a dependent merge together in the queue."""
        jobs_by_id = {candidate.id: candidate for candidate in self._jobs}
        for dependent in self._jobs:
            if (
                dependent.kind is not JobKind.MERGE
                or dependent.status is not JobStatus.WAITING
                or job.id not in dependent.dependency_job_ids
            ):
                continue
            for dependency_id in dependent.dependency_job_ids:
                dependency = jobs_by_id.get(dependency_id)
                if dependency is None:
                    return False
                if (
                    dependency.kind is JobKind.SLIDE
                    and not self._globals_ready(dependency)
                ):
                    return False
        return True

    def claim_next(self) -> JobRecord | None:
        with self._lock:
            if any(job.status is JobStatus.RUNNING for job in self._jobs):
                return None
            changed = False
            for index, job in enumerate(self._jobs):
                if job.status is not JobStatus.WAITING:
                    continue
                dependencies_ready, dependency_error = self.dependency_state(job)
                if dependency_error:
                    self._jobs[index] = replace(
                        job,
                        status=JobStatus.FAILED,
                        error=dependency_error,
                        stage=None,
                        finished_at=utc_now(),
                    )
                    changed = True
                    continue
                if not dependencies_ready:
                    continue
                if job.kind is JobKind.SLIDE:
                    if not self._globals_ready(job):
                        continue
                    if not self._dependency_group_globals_ready(job):
                        continue
                error = self._validate_job_paths(job)
                if error:
                    self._jobs[index] = replace(
                        job,
                        status=JobStatus.FAILED,
                        error=error,
                        stage=None,
                        finished_at=utc_now(),
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
            updated = replace(
                job, status=status, stage=None, error=error, finished_at=utc_now()
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

            dependent = next(
                (
                    candidate
                    for candidate in self._jobs
                    if candidate.kind is JobKind.MERGE
                    and (
                        candidate.id == job_id
                        or job_id in candidate.dependency_job_ids
                    )
                    and candidate.dependency_job_ids
                ),
                None,
            )
            if dependent is None:
                retried = self._waiting_copy(job)
                error = self._validate_job_paths(retried)
                if error:
                    raise ValueError(error)
                self._jobs.pop(index)
                self._jobs.append(retried)
                self.store.save_jobs(self._jobs)
                return retried

            jobs_by_id = {candidate.id: candidate for candidate in self._jobs}
            dependencies = [
                jobs_by_id[dependency_id]
                for dependency_id in dependent.dependency_job_ids
                if dependency_id in jobs_by_id
            ]
            if len(dependencies) != len(dependent.dependency_job_ids):
                missing = next(
                    dependency_id
                    for dependency_id in dependent.dependency_job_ids
                    if dependency_id not in jobs_by_id
                )
                raise QueueStateError(f"missing dependency: {missing}")
            group = [*dependencies, dependent]
            if any(candidate.status is JobStatus.RUNNING for candidate in group):
                raise QueueStateError(
                    "wait for the related batch part to finish before retrying"
                )

            reset_group = [self._waiting_copy(candidate) for candidate in group]
            for candidate in reset_group[:-1]:
                error = self._validate_job_paths(candidate)
                if error:
                    raise ValueError(error)
            merge_error = self._validate_job_paths(
                reset_group[-1], allow_missing_inputs=True
            )
            if merge_error:
                raise ValueError(merge_error)

            group_ids = {candidate.id for candidate in group}
            self._jobs = [
                candidate for candidate in self._jobs if candidate.id not in group_ids
            ]
            self._jobs.extend(reset_group)
            self.store.save_jobs(self._jobs)
            return next(candidate for candidate in reset_group if candidate.id == job_id)

    @staticmethod
    def _waiting_copy(job: JobRecord) -> JobRecord:
        return replace(
            job,
            status=JobStatus.WAITING,
            stage=None,
            error=None,
            started_at=None,
            finished_at=None,
            created_at=utc_now(),
        )

    def remove(self, job_id: str) -> None:
        with self._lock:
            index = self._index(job_id)
            if self._jobs[index].status is not JobStatus.WAITING:
                raise QueueStateError("only waiting jobs can be removed")
            dependent = next(
                (
                    job
                    for job in self._jobs
                    if job.status is JobStatus.WAITING
                    and job_id in job.dependency_job_ids
                ),
                None,
            )
            if dependent is not None:
                raise QueueStateError(
                    f"remove dependent job {dependent.output_name} first"
                )
            self._jobs.pop(index)
            self.store.save_jobs(self._jobs)

    def restore_available_path_jobs(self) -> tuple[JobRecord, ...]:
        """Requeue jobs that failed only because a required path disappeared."""
        with self._lock:
            restorable_ids: list[str] = []
            for job in tuple(self._jobs):
                path_error = bool(
                    job.error
                    and (
                        " file does not exist:" in job.error
                        or job.error.startswith("output directory does not exist:")
                    )
                )
                if (
                    job.status is JobStatus.FAILED
                    and path_error
                    and self._validate_job_paths(job) is None
                ):
                    restorable_ids.append(job.id)

            restored: list[JobRecord] = []
            for job_id in restorable_ids:
                current = self._jobs[self._index(job_id)]
                if current.status is JobStatus.FAILED:
                    restored.append(self.retry(job_id))
            return tuple(restored)

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
