from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from video_workflow.job_models import JobRecord, JobStage, JobStatus
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


def _controller(
    tmp_path: Path, *, globals_ready: bool = True
) -> tuple[QueueController, dict[str, Path]]:
    files = _files(tmp_path)
    controller = QueueController(JsonStore(tmp_path / "data"))
    if globals_ready:
        controller.set_global_assets(files["logo"], files["outro"])
    return controller, files


def _enqueue(
    controller: QueueController, files: dict[str, Path], tmp_path: Path, name: str
):
    return controller.enqueue(
        source_media=files["source_media"],
        pptx=files["pptx"],
        timeline=files["timeline"],
        output_name=name,
        output_directory=tmp_path / "out",
    )


def _enqueue_dependent_merge(
    controller: QueueController,
    files: dict[str, Path],
    tmp_path: Path,
    *,
    prefix: str = "lesson",
):
    part1 = controller.enqueue(
        source_media=files["source_media"],
        pptx=files["pptx"],
        timeline=files["timeline"],
        output_name=f"{prefix}_1",
        output_directory=tmp_path / "out",
        use_outro=False,
        overwrite_output=True,
    )
    part2 = controller.enqueue(
        source_media=files["source_media"],
        pptx=files["pptx"],
        timeline=files["timeline"],
        output_name=f"{prefix}_2",
        output_directory=tmp_path / "out",
        overwrite_output=True,
    )
    merged = controller.enqueue_merge(
        first_video=part1.output_path,
        second_video=part2.output_path,
        output_name=prefix,
        output_directory=tmp_path / "out",
        write_report=False,
        overwrite_output=True,
        dependency_job_ids=(part1.id, part2.id),
    )
    return part1, part2, merged


def test_recovers_running_jobs_and_claims_only_one_in_creation_order(
    tmp_path: Path,
) -> None:
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


def test_absent_global_assets_do_not_pause_waiting_job(tmp_path: Path) -> None:
    controller, files = _controller(tmp_path, globals_ready=False)
    job = _enqueue(controller, files, tmp_path, "lesson")
    assert controller.claim_next().id == job.id


def test_global_assets_are_independently_optional_and_validate_supplied_paths(
    tmp_path: Path,
) -> None:
    controller, files = _controller(tmp_path, globals_ready=False)

    assert controller.set_global_assets(files["logo"], None).logo == files["logo"]
    assert controller.set_global_assets(None, files["outro"]).outro == files["outro"]
    settings = controller.set_global_assets(None, None)
    assert settings.logo is None
    assert settings.outro is None
    with pytest.raises(ValueError, match="logo"):
        controller.set_global_assets(tmp_path / "missing.png", None)


def test_missing_global_outro_blocks_part_2_but_not_part_1(tmp_path: Path) -> None:
    controller, files = _controller(tmp_path)
    files["outro"].unlink()
    controller.enqueue(
        source_media=files["source_media"],
        pptx=files["pptx"],
        timeline=files["timeline"],
        output_name="lesson_2",
        output_directory=tmp_path / "out",
        use_outro=True,
    )
    part1 = controller.enqueue(
        source_media=files["source_media"],
        pptx=files["pptx"],
        timeline=files["timeline"],
        output_name="another_lesson_1",
        output_directory=tmp_path / "out",
        use_outro=False,
    )

    assert controller.claim_next().id == part1.id
    controller.mark_completed(part1.id)
    assert controller.claim_next() is None


def test_output_created_after_enqueue_fails_candidate_and_claims_next(
    tmp_path: Path,
) -> None:
    controller, files = _controller(tmp_path)
    first = _enqueue(controller, files, tmp_path, "one")
    second = _enqueue(controller, files, tmp_path, "two")
    first.output_path.write_bytes(b"do-not-overwrite")

    claimed = controller.claim_next()

    assert claimed and claimed.id == second.id
    assert controller.jobs()[0].status is JobStatus.FAILED
    assert first.output_path.read_bytes() == b"do-not-overwrite"


def test_overwrite_jobs_with_the_same_existing_output_are_claimed_in_order(
    tmp_path: Path,
) -> None:
    controller, files = _controller(tmp_path)
    output = tmp_path / "out" / "lesson.mp4"
    output.write_bytes(b"previous-video")
    jobs = [
        controller.enqueue(
            source_media=files["source_media"],
            pptx=files["pptx"],
            timeline=files["timeline"],
            output_name="lesson",
            output_directory=tmp_path / "out",
            overwrite_output=True,
        )
        for _ in range(2)
    ]

    assert controller.claim_next().id == jobs[0].id
    controller.mark_completed(jobs[0].id)
    assert controller.claim_next().id == jobs[1].id
    assert output.read_bytes() == b"previous-video"


def test_dependent_merge_waits_through_part_failure_and_runs_after_retry(
    tmp_path: Path,
) -> None:
    controller, files = _controller(tmp_path)
    part1, part2, merged = _enqueue_dependent_merge(controller, files, tmp_path)
    unrelated = _enqueue(controller, files, tmp_path, "unrelated")

    assert controller.claim_next().id == part1.id
    controller.mark_failed(part1.id, "render failed")
    assert controller.claim_next().id == part2.id
    part2.output_path.write_bytes(b"part-2")
    controller.mark_completed(part2.id)
    assert controller.claim_next().id == unrelated.id
    controller.mark_completed(unrelated.id)
    controller.retry(part1.id)
    assert controller.claim_next().id == part1.id
    part1.output_path.write_bytes(b"part-1")
    controller.mark_completed(part1.id)
    assert controller.claim_next().id == part2.id
    part2.output_path.write_bytes(b"part-2-retry")
    controller.mark_completed(part2.id)

    assert controller.claim_next().id == merged.id


def test_dependent_merge_groups_with_same_paths_keep_distinct_job_ids(
    tmp_path: Path,
) -> None:
    controller, files = _controller(tmp_path)
    first_group = _enqueue_dependent_merge(controller, files, tmp_path)
    second_group = _enqueue_dependent_merge(controller, files, tmp_path)

    assert first_group[2].dependency_job_ids == (
        first_group[0].id,
        first_group[1].id,
    )
    assert second_group[2].dependency_job_ids == (
        second_group[0].id,
        second_group[1].id,
    )
    assert set(first_group[2].dependency_job_ids).isdisjoint(
        second_group[2].dependency_job_ids
    )


def test_persisted_merge_with_missing_dependency_fails_clearly(
    tmp_path: Path,
) -> None:
    controller, _files = _controller(tmp_path)
    job = JobRecord.new_merge(
        first_video=tmp_path / "out" / "lesson_1.mp4",
        second_video=tmp_path / "out" / "lesson_2.mp4",
        output_name="lesson",
        output_directory=tmp_path / "out",
        dependency_job_ids=("missing-job",),
    )
    controller.store.save_jobs([job])
    controller.reload()

    assert controller.claim_next() is None
    failed = controller.jobs()[0]
    assert failed.status is JobStatus.FAILED
    assert failed.error == "missing dependency: missing-job"


def test_enqueue_merge_rejects_unknown_dependency_id(tmp_path: Path) -> None:
    controller, _files = _controller(tmp_path)

    with pytest.raises(ValueError, match="missing dependency"):
        controller.enqueue_merge(
            first_video=tmp_path / "out" / "lesson_1.mp4",
            second_video=tmp_path / "out" / "lesson_2.mp4",
            output_name="lesson",
            output_directory=tmp_path / "out",
            dependency_job_ids=("missing-job",),
        )


def test_enqueue_merge_rejects_unknown_dependency_after_waiting_one(
    tmp_path: Path,
) -> None:
    controller, files = _controller(tmp_path)
    waiting = _enqueue(controller, files, tmp_path, "waiting")

    with pytest.raises(ValueError, match="missing dependency: missing-job"):
        controller.enqueue_merge(
            first_video=waiting.output_path,
            second_video=tmp_path / "out" / "missing.mp4",
            output_name="lesson",
            output_directory=tmp_path / "out",
            dependency_job_ids=(waiting.id, "missing-job"),
        )


def test_retrying_failed_part_requeues_its_whole_pair_after_later_scan(
    tmp_path: Path,
) -> None:
    controller, files = _controller(tmp_path)
    first_group = _enqueue_dependent_merge(controller, files, tmp_path)
    second_group = _enqueue_dependent_merge(controller, files, tmp_path)
    first_part1, first_part2, first_merge = first_group
    second_part1, second_part2, second_merge = second_group

    assert controller.claim_next().id == first_part1.id
    first_part1.output_path.write_bytes(b"first-scan-part-1")
    controller.mark_completed(first_part1.id)
    assert controller.claim_next().id == first_part2.id
    controller.mark_failed(first_part2.id, "render failed")

    for part, content in (
        (second_part1, b"second-scan-part-1"),
        (second_part2, b"second-scan-part-2"),
    ):
        assert controller.claim_next().id == part.id
        part.output_path.write_bytes(content)
        controller.mark_completed(part.id)
    assert controller.claim_next().id == second_merge.id
    controller.mark_completed(second_merge.id)

    controller.retry(first_part2.id)

    assert [job.id for job in controller.jobs()][-3:] == [
        first_part1.id,
        first_part2.id,
        first_merge.id,
    ]
    assert controller.claim_next().id == first_part1.id
    first_part1.output_path.write_bytes(b"first-scan-part-1-retry")
    controller.mark_completed(first_part1.id)
    assert controller.claim_next().id == first_part2.id
    first_part2.output_path.write_bytes(b"first-scan-part-2-retry")
    controller.mark_completed(first_part2.id)
    claimed_merge = controller.claim_next()

    assert claimed_merge.id == first_merge.id
    assert claimed_merge.source_media.read_bytes() == b"first-scan-part-1-retry"
    assert claimed_merge.secondary_media.read_bytes() == b"first-scan-part-2-retry"


def test_dependent_pair_waits_as_a_group_when_part_2_outro_is_missing(
    tmp_path: Path,
) -> None:
    controller, files = _controller(tmp_path)
    part1, _part2, _merge = _enqueue_dependent_merge(controller, files, tmp_path)
    files["outro"].unlink()

    assert controller.claim_next() is None
    assert next(job for job in controller.jobs() if job.id == part1.id).status is JobStatus.WAITING


def test_remove_rejects_part_referenced_by_waiting_merge(tmp_path: Path) -> None:
    controller, files = _controller(tmp_path)
    part1, _part2, merged = _enqueue_dependent_merge(controller, files, tmp_path)

    with pytest.raises(QueueStateError, match="remove dependent.*first"):
        controller.remove(part1.id)

    controller.remove(merged.id)
    controller.remove(part1.id)


def test_recovery_keeps_merge_waiting_until_interrupted_part_is_retried(
    tmp_path: Path,
) -> None:
    controller, files = _controller(tmp_path)
    part1, part2, merged = _enqueue_dependent_merge(controller, files, tmp_path)
    assert controller.claim_next().id == part1.id

    reloaded = QueueController(JsonStore(tmp_path / "data"))
    reloaded.recover_startup()
    assert [job.status for job in reloaded.jobs()] == [
        JobStatus.INTERRUPTED,
        JobStatus.WAITING,
        JobStatus.WAITING,
    ]
    reloaded.retry(part1.id)
    assert reloaded.claim_next().id == part1.id
    part1.output_path.write_bytes(b"part-1")
    reloaded.mark_completed(part1.id)
    assert reloaded.claim_next().id == part2.id
    part2.output_path.write_bytes(b"part-2")
    reloaded.mark_completed(part2.id)

    assert reloaded.claim_next().id == merged.id


def test_completed_dependency_with_deleted_output_fails_merge_validation(
    tmp_path: Path,
) -> None:
    controller, files = _controller(tmp_path)
    part1, part2, merged = _enqueue_dependent_merge(controller, files, tmp_path)
    for part, content in ((part1, b"part-1"), (part2, b"part-2")):
        assert controller.claim_next().id == part.id
        part.output_path.write_bytes(content)
        controller.mark_completed(part.id)
    part2.output_path.unlink()

    assert controller.claim_next() is None
    failed = next(job for job in controller.jobs() if job.id == merged.id)
    assert failed.status is JobStatus.FAILED
    assert "second_video file does not exist" in (failed.error or "")


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
    _enqueue(controller, files, tmp_path, "one")
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


def test_enqueue_rejects_missing_input_existing_output_and_bad_directory(
    tmp_path: Path,
) -> None:
    controller, files = _controller(tmp_path)
    with pytest.raises(ValueError, match="source_media"):
        controller.enqueue(
            source_media=tmp_path / "missing.mp3",
            pptx=files["pptx"],
            timeline=files["timeline"],
            output_name="x",
            output_directory=tmp_path / "out",
        )
    (tmp_path / "out" / "exists.mp4").write_bytes(b"x")
    with pytest.raises(ValueError, match="already exists"):
        _enqueue(controller, files, tmp_path, "exists")
    with pytest.raises(ValueError, match="output directory"):
        controller.enqueue(
            source_media=files["source_media"],
            pptx=files["pptx"],
            timeline=files["timeline"],
            output_name="x",
            output_directory=tmp_path / "missing-dir",
        )


def test_merge_job_ignores_stale_slide_assets(tmp_path: Path) -> None:
    controller, files = _controller(tmp_path)
    files["logo"].unlink()
    job = controller.enqueue_merge(
        first_video=files["source_media"],
        second_video=files["outro"],
        output_name="merged",
        output_directory=tmp_path / "out",
    )

    claimed = controller.claim_next()

    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.kind.value == "merge"


def test_slide_and_merge_jobs_keep_fifo_order(tmp_path: Path) -> None:
    controller, files = _controller(tmp_path)
    slide = _enqueue(controller, files, tmp_path, "slide")
    merged = controller.enqueue_merge(
        first_video=files["source_media"],
        second_video=files["outro"],
        output_name="merged",
        output_directory=tmp_path / "out",
    )

    assert controller.claim_next().id == slide.id
    controller.mark_completed(slide.id)
    assert controller.claim_next().id == merged.id


def test_enqueue_merge_rejects_missing_second_video(tmp_path: Path) -> None:
    controller, files = _controller(tmp_path)

    with pytest.raises(ValueError, match="second_video"):
        controller.enqueue_merge(
            first_video=files["source_media"],
            second_video=tmp_path / "missing.mp4",
            output_name="merged",
            output_directory=tmp_path / "out",
        )
