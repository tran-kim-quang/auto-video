from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from video_workflow.job_models import JobStage, JobStatus
from video_workflow.json_store import JsonStore
from video_workflow.queue_controller import QueueController, QueueStateError


def _files(tmp_path: Path) -> dict[str, Path]:
    values = {
        "source_media": tmp_path / "nguồn.mp3",
        "pptx": tmp_path / "slides.pptx",
        "timeline": tmp_path / "timeline.txt",
        "logo": tmp_path / "logo.png",
        "outro": tmp_path / "outro.mp4",
    }
    for path in values.values():
        path.write_bytes(b"x")
    (tmp_path / "out").mkdir()
    return values


def _controller(tmp_path: Path, *, globals_ready: bool = True) -> tuple[QueueController, dict[str, Path]]:
    files = _files(tmp_path)
    controller = QueueController(JsonStore(tmp_path / "data"))
    if globals_ready:
        controller.set_global_assets(files["logo"], files["outro"])
    return controller, files


def _enqueue(controller: QueueController, files: dict[str, Path], tmp_path: Path, name: str):
    return controller.enqueue(
        source_media=files["source_media"],
        pptx=files["pptx"],
        timeline=files["timeline"],
        output_name=name,
        output_directory=tmp_path / "out",
    )


def test_recovers_running_jobs_and_claims_only_one_in_creation_order(tmp_path: Path) -> None:
    controller, files = _controller(tmp_path)
    first = _enqueue(controller, files, tmp_path, "one")
    second = _enqueue(controller, files, tmp_path, "two")
    claimed = controller.claim_next()
    assert claimed and claimed.id == first.id
    assert controller.claim_next() is None

    reloaded = QueueController(JsonStore(tmp_path / "data"))
    reloaded.recover_startup()
    jobs = reloaded.jobs()
    assert jobs[0].status is JobStatus.INTERRUPTED
    assert jobs[1].status is JobStatus.WAITING
    assert reloaded.claim_next().id == second.id


def test_missing_globals_pause_then_resume_same_waiting_job(tmp_path: Path) -> None:
    controller, files = _controller(tmp_path, globals_ready=False)
    job = _enqueue(controller, files, tmp_path, "lesson")
    assert controller.claim_next() is None
    assert controller.jobs()[0].status is JobStatus.WAITING

    controller.set_global_assets(files["logo"], files["outro"])
    assert controller.claim_next().id == job.id


def test_output_created_after_enqueue_fails_candidate_and_claims_next(tmp_path: Path) -> None:
    controller, files = _controller(tmp_path)
    first = _enqueue(controller, files, tmp_path, "one")
    second = _enqueue(controller, files, tmp_path, "two")
    first.output_path.write_bytes(b"do-not-overwrite")

    claimed = controller.claim_next()

    assert claimed and claimed.id == second.id
    assert controller.jobs()[0].status is JobStatus.FAILED
    assert first.output_path.read_bytes() == b"do-not-overwrite"


def test_transitions_retry_remove_and_stage_rules(tmp_path: Path) -> None:
    controller, files = _controller(tmp_path)
    first = _enqueue(controller, files, tmp_path, "one")
    second = _enqueue(controller, files, tmp_path, "two")
    with pytest.raises(QueueStateError):
        controller.set_stage(first.id, JobStage.VALIDATING)

    controller.claim_next()
    controller.set_stage(first.id, JobStage.RENDERING_LECTURE)
    assert controller.jobs()[0].stage is JobStage.RENDERING_LECTURE
    controller.mark_failed(first.id, "ffmpeg failed")
    failed = controller.jobs()[0]
    assert failed.status is JobStatus.FAILED and failed.finished_at and failed.error
    controller.retry(first.id)
    assert [job.id for job in controller.jobs()] == [second.id, first.id]
    assert controller.jobs()[-1].status is JobStatus.WAITING
    controller.remove(second.id)
    assert [job.id for job in controller.jobs()] == [first.id]


def test_completed_and_interrupted_transitions(tmp_path: Path) -> None:
    controller, files = _controller(tmp_path)
    first = _enqueue(controller, files, tmp_path, "one")
    controller.claim_next()
    controller.mark_completed(first.id)
    assert controller.jobs()[0].status is JobStatus.COMPLETED
    assert controller.jobs()[0].finished_at

    second = _enqueue(controller, files, tmp_path, "two")
    controller.claim_next()
    controller.mark_interrupted(second.id, "application closed")
    assert controller.jobs()[-1].status is JobStatus.INTERRUPTED


def test_moved_job_input_fails_without_blocking_next(tmp_path: Path) -> None:
    controller, files = _controller(tmp_path)
    first = _enqueue(controller, files, tmp_path, "one")
    second = _enqueue(controller, files, tmp_path, "two")
    files["source_media"].unlink()
    replacement = tmp_path / "replacement.mp3"
    replacement.write_bytes(b"x")
    jobs = list(controller.jobs())
    jobs[1] = replace(jobs[1], source_media=replacement)
    controller.store.save_jobs(jobs)
    controller.reload()

    assert controller.claim_next().id == second.id
    assert controller.jobs()[0].status is JobStatus.FAILED


def test_enqueue_rejects_missing_input_existing_output_and_bad_directory(tmp_path: Path) -> None:
    controller, files = _controller(tmp_path)
    with pytest.raises(ValueError, match="source_media"):
        controller.enqueue(
            source_media=tmp_path / "missing.mp3", pptx=files["pptx"], timeline=files["timeline"],
            output_name="x", output_directory=tmp_path / "out",
        )
    (tmp_path / "out" / "exists.mp4").write_bytes(b"x")
    with pytest.raises(ValueError, match="already exists"):
        _enqueue(controller, files, tmp_path, "exists")
    with pytest.raises(ValueError, match="output directory"):
        controller.enqueue(
            source_media=files["source_media"], pptx=files["pptx"], timeline=files["timeline"],
            output_name="x", output_directory=tmp_path / "missing-dir",
        )
