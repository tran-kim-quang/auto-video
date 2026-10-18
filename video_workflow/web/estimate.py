from __future__ import annotations

from collections.abc import Iterable

from ..job_models import JobStatus
from .state import WebJob


DEFAULT_SECONDS_PER_CHAR = 0.032
FIXED_SECONDS_PER_JOB = 120


def seconds_per_char(jobs: Iterable[WebJob]) -> float:
    eligible = [
        job
        for job in jobs
        if job.status is JobStatus.COMPLETED
        and job.tts_seconds is not None
        and job.script_chars > 0
    ]
    total_chars = sum(job.script_chars for job in eligible)
    if total_chars == 0:
        return DEFAULT_SECONDS_PER_CHAR
    return sum(job.tts_seconds or 0 for job in eligible) / total_chars


def estimate_job_seconds(script_chars: int, rate: float) -> int:
    return round(script_chars * rate) + FIXED_SECONDS_PER_JOB


def remaining_seconds(job: WebJob, rate: float) -> int | None:
    if job.stage != "synthesizing_audio" or job.progress_total <= 0:
        return None
    remaining_ratio = 1 - job.progress_current / job.progress_total
    return round(remaining_ratio * job.script_chars * rate) + FIXED_SECONDS_PER_JOB
