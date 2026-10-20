from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from ..job_models import GlobalSettings
from ..leaf_output import publish_leaf_output
from ..leaf_scan import inspect_leaf
from ..narration import NarrationError
from ..pipeline import (
    NarratedBuildReport,
    NarratedBuildRequest,
    WorkflowCancelled,
    build_narrated_video,
)
from ..voicestudio_client import VoiceStudioClient, VoiceStudioOutOfMemory
from ..vtt import render_vtt_from_report
from .gpu import GatedVoiceStudioClient, GpuArbiter, GpuWaitCancelled
from .output_location import output_paths, publish_output_files
from .state import WebJob


Update = Callable[[str | None, int | None, int | None], None]


@dataclass(frozen=True, slots=True)
class RunResult:
    video_path: Path
    tts_seconds: float
    vtt_path: Path | None = None


@dataclass
class RunnerContext:
    uploads_dir: Path
    work_dir: Path
    cache_dir: Path
    logs_dir: Path
    settings: Callable[[], GlobalSettings]
    client: Callable[[], VoiceStudioClient]
    arbiter: GpuArbiter
    export_lock: threading.Lock
    build: Callable[..., NarratedBuildReport] = build_narrated_video


def run_web_job(
    job: WebJob,
    ctx: RunnerContext,
    cancel_event: threading.Event,
    update: Update,
) -> RunResult:
    leaf_path = Path(ctx.uploads_dir) / job.batch_id / job.leaf_relpath
    leaf = inspect_leaf(leaf_path, job.leaf_relpath)
    if not leaf.valid:
        raise ValueError(leaf.error)
    assert leaf.pptx is not None
    assert leaf.script is not None

    settings = ctx.settings()
    if settings.logo is None or settings.outro is None:
        raise ValueError("Chưa cấu hình logo hoặc outro")
    profile_config = replace(settings.voice_config(), profile_id=job.profile_id)
    request = NarratedBuildRequest(
        pptx=leaf.pptx,
        script=leaf.script,
        logo=settings.logo,
        outro=settings.outro,
        artifact_directory=Path(ctx.work_dir) / job.id,
        profile_config=profile_config,
    )

    @contextmanager
    def synthesis_guard() -> Iterator[None]:
        update("waiting_gpu", None, None)
        with ctx.arbiter.job_turn(cancel_event=cancel_event):
            yield

    @contextmanager
    def export_guard() -> Iterator[None]:
        update("waiting_export", None, None)
        with ctx.export_lock:
            yield

    Path(ctx.logs_dir).mkdir(parents=True, exist_ok=True)
    report = ctx.build(
        request,
        client=GatedVoiceStudioClient(
            ctx.client(),
            ctx.arbiter,
            interactive=False,
        ),
        cache_root=ctx.cache_dir,
        on_stage=lambda stage: update(stage, None, None),
        on_progress=lambda current, total: update(None, current, total),
        cancel_event=cancel_event,
        log_path=Path(ctx.logs_dir) / f"{job.id}.log",
        synthesis_guard=synthesis_guard,
        export_guard=export_guard,
    )

    update("writing_subtitles", None, None)
    vtt_text = render_vtt_from_report(report.slides)
    update("publishing_output", None, None)
    if job.output_root is not None:
        video_path, vtt_path = output_paths(
            Path(job.output_root),
            job.leaf_relpath,
        )
        publish_output_files(
            Path(report.output),
            vtt_text,
            video_path,
            vtt_path,
        )
    else:
        video_path = publish_leaf_output(
            leaf_path,
            Path(report.output),
            leaf_path.name,
            vtt_text,
        )
        vtt_path = video_path.with_name("vi.vtt")
    tts_seconds = sum(
        float(slide.get("generation", {}).get("generation_seconds") or 0)
        for slide in report.slides
    )
    return RunResult(
        video_path=video_path,
        tts_seconds=tts_seconds,
        vtt_path=vtt_path,
    )


def _causes(exc: BaseException):
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def describe_error(exc: BaseException) -> str:
    chain = list(_causes(exc))
    if any(isinstance(item, VoiceStudioOutOfMemory) for item in chain):
        return "VoiceStudio hết bộ nhớ GPU"
    if any(isinstance(item, (WorkflowCancelled, GpuWaitCancelled)) for item in chain):
        return "Đã huỷ"
    for item in chain:
        if isinstance(item, NarrationError):
            match = re.match(r"slide\s+(\d+)\s*:?[ ]*(.*)", str(item), re.IGNORECASE)
            if match:
                detail = match.group(2).strip()
                return f"Lỗi ở slide {match.group(1)}: {detail}"
    return f"Lỗi: {exc}"
