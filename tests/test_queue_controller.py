from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from video_workflow.job_models import JobStage, JobStatus
from video_workflow.json_store import JsonStore
from video_workflow.queue_controller import QueueController, QueueStateError


def _files(tmp_path: Path) -> dict[str, Path]:
    values = {
        "pptx": tmp_path / "lesson.pptx",
        "script": tmp_path / "lesson.txt",
        "logo": tmp_path / "logo.png",
        "outro": tmp_path / "outro.mp4",
    }
    values["pptx"].write_bytes(b"pptx")
    values["script"].write_text("Một.", encoding="utf-8")
    values["logo"].write_bytes(b"logo")
    values["outro"].write_bytes(b"outro")
    return values


def _controller(tmp_path: Path, *, assets: bool = True):
    files = _files(tmp_path)
    controller = QueueController(JsonStore(tmp_path / "data"))
    if assets:
        controller.set_global_assets(files["logo"], files["outro"])
    return controller, files


def _enqueue(controller, files, tmp_path, name="lesson"):
    return controller.enqueue(
        pptx=files["pptx"],
        script=files["script"],
        artifact_directory=tmp_path / "output" / name,
        output_name=f"{name}.mp4",
    )


def test_enqueue_requires_saved_existing_logo_and_outro_and_captures_paths(
    tmp_path: Path,
) -> None:
    controller, files = _controller(tmp_path, assets=False)
    with pytest.raises(ValueError, match="logo and outro are required"):
        _enqueue(controller, files, tmp_path)
    with pytest.raises(ValueError, match="logo and outro are required"):
        controller.set_global_assets(files["logo"], None)

    controller.set_global_assets(files["logo"], files["outro"])
    job = _enqueue(controller, files, tmp_path)

    assert job.logo == files["logo"]
    assert job.outro == files["outro"]
    assert job.profile_id == "599fc048"
    assert job.input_signature["inputs"]["pptx"]["sha256"]


def test_claim_next_is_strictly_serial_and_progress_is_persisted(
    tmp_path: Path,
) -> None:
    controller, files = _controller(tmp_path)
    first = _enqueue(controller, files, tmp_path, "one")
    second = _enqueue(controller, files, tmp_path, "two")

    assert controller.claim_next().id == first.id
    assert controller.claim_next() is None
    controller.set_stage(first.id, JobStage.SYNTHESIZING_AUDIO)
    controller.set_progress(first.id, 1, 3)

    reloaded = QueueController(JsonStore(tmp_path / "data"))
    assert reloaded.jobs()[0].progress_current == 1
    assert reloaded.jobs()[0].progress_total == 3
    reloaded.recover_startup()
    assert reloaded.jobs()[0].status is JobStatus.INTERRUPTED
    assert reloaded.claim_next().id == second.id


def test_retry_preserves_job_inputs_and_returns_to_waiting(tmp_path: Path) -> None:
    controller, files = _controller(tmp_path)
    first = _enqueue(controller, files, tmp_path, "one")
    second = _enqueue(controller, files, tmp_path, "two")
    controller.claim_next()
    controller.mark_failed(first.id, "failed")

    retried = controller.retry(first.id)

    assert retried.status is JobStatus.WAITING
    assert retried.input_signature == first.input_signature
    assert retried.logo == first.logo and retried.outro == first.outro
    assert [job.id for job in controller.jobs()] == [second.id, first.id]


def test_invalid_paths_fail_candidate_without_blocking_next(tmp_path: Path) -> None:
    controller, files = _controller(tmp_path)
    first = _enqueue(controller, files, tmp_path, "one")
    second = _enqueue(controller, files, tmp_path, "two")
    first.script.unlink()
    alternate = tmp_path / "alternate.txt"
    alternate.write_text("Một.", encoding="utf-8")
    jobs = list(controller.jobs())
    jobs[1] = replace(second, script=alternate)
    controller.store.save_jobs(jobs)
    controller.reload()

    assert controller.claim_next().id == second.id
    assert controller.jobs()[0].status is JobStatus.FAILED


def test_stage_and_progress_only_change_running_job(tmp_path: Path) -> None:
    controller, files = _controller(tmp_path)
    job = _enqueue(controller, files, tmp_path)
    with pytest.raises(QueueStateError):
        controller.set_stage(job.id, JobStage.RENDERING_LECTURE)
    with pytest.raises(QueueStateError):
        controller.set_progress(job.id, 1, 2)


def test_remove_only_waiting_and_complete_transitions(tmp_path: Path) -> None:
    controller, files = _controller(tmp_path)
    first = _enqueue(controller, files, tmp_path, "one")
    second = _enqueue(controller, files, tmp_path, "two")
    controller.claim_next()
    with pytest.raises(QueueStateError):
        controller.remove(first.id)
    controller.mark_completed(first.id)
    controller.remove(second.id)
    assert [job.status for job in controller.jobs()] == [JobStatus.COMPLETED]
