from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from video_workflow.job_models import JobStatus
from video_workflow.web.config import WebLayout
from video_workflow.web.r2_upload import UploadOutcome, UploadProgress
from video_workflow.web.state import UploadRunRecord, WebJob, WebStore
from video_workflow.web.upload_identity import UploadCheck, UploadIdentity
from video_workflow.web.upload_service import (
    UploadBusyError,
    UploadNotReadyError,
    UploadService,
)


def _job(relpath: str, status: JobStatus) -> WebJob:
    return replace(
        WebJob.new(
            batch_id="batch-1",
            leaf_relpath=relpath,
            profile_id="abc12345",
            script_chars=10,
        ),
        status=status,
    )


def _identity(relpath: str, *, issue: str | None = None) -> UploadCheck:
    triple = "/".join(relpath.split("/")[-3:])
    return UploadCheck(
        relpath=relpath,
        identity=UploadIdentity(
            triple=triple,
            course_id=601,
            course_slug="601-toan-hoc-lop-6",
            lesson_slug="19-bai-19-x",
            lesson_order=19,
            lesson_title="Bài 19: X",
            section_no=48,
            part_no=1,
            video_slug="48-y",
            video_title="Tiết 48 - Y",
        ),
        issue=issue,
        warning=None,
    )


def _output(layout: WebLayout, relpath: str) -> None:
    output = layout.uploads / "batch-1" / relpath / "output"
    output.mkdir(parents=True, exist_ok=True)
    (output / "legacy-name.mp4").write_bytes(b"video")
    (output / "vi.vtt").write_text("WEBVTT\n", encoding="utf-8")


def _service(tmp_path: Path, jobs, check, runner) -> UploadService:
    layout = WebLayout(tmp_path / "data").ensure()
    return UploadService(
        WebStore(layout.state),
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: jobs,
        check=check,
        runner=runner,
    )


def _wait_for(service: UploadService, status: str) -> UploadRunRecord:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        runs = service.runs()
        if runs and runs[0].status == status:
            return runs[0]
        time.sleep(0.01)
    raise AssertionError(f"upload run did not reach {status}")


def test_candidates_only_include_completed_valid_jobs_with_outputs(tmp_path: Path) -> None:
    layout = WebLayout(tmp_path / "data").ensure()
    valid = "root/Toan6/Bài 19_X/T48_Y"
    waiting = "root/Toan6/Bài 20_X/T49_Y"
    invalid = "root/Toan6/Bài 21_X/T50_Y"
    jobs = [
        _job(valid, JobStatus.COMPLETED),
        _job(waiting, JobStatus.WAITING),
        _job(invalid, JobStatus.COMPLETED),
    ]
    _output(layout, valid)
    _output(layout, waiting)
    _output(layout, invalid)
    checks = {
        valid: _identity(valid),
        waiting: _identity(waiting),
        invalid: _identity(invalid, issue="không hợp lệ"),
    }
    service = UploadService(
        WebStore(layout.state),
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: jobs,
        check=lambda _repo, relpaths: {path: checks[path] for path in relpaths},
        runner=lambda *_args, **_kwargs: None,
    )

    items, returned_checks = service.candidates("batch-1")

    assert [item.relpath for item in items] == [valid]
    assert returned_checks == checks


def test_candidates_deduplicate_historical_completed_jobs(tmp_path: Path) -> None:
    relpath = "root/Toan6/Bài 19_X/T48_Y"
    layout = WebLayout(tmp_path / "data").ensure()
    _output(layout, relpath)
    jobs = [
        _job(relpath, JobStatus.COMPLETED),
        _job(relpath, JobStatus.COMPLETED),
    ]
    service = UploadService(
        WebStore(layout.state),
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: jobs,
        check=lambda _repo, _paths: {relpath: _identity(relpath)},
        runner=lambda *_args, **_kwargs: None,
    )

    items, _checks = service.candidates("batch-1")

    assert [item.relpath for item in items] == [relpath]


def test_candidates_use_video_and_vtt_paths_stored_on_job(tmp_path: Path) -> None:
    relpath = "root/Toan6/Bài 19_X/T48_Y"
    layout = WebLayout(tmp_path / "data").ensure()
    video = tmp_path / "results" / "Toan6" / "Bài 19_X" / "T48_Y.mp4"
    vtt = tmp_path / "results" / "Toan6" / "Bài 19_X" / "T48_Y" / "vi.vtt"
    video.parent.mkdir(parents=True)
    vtt.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    vtt.write_text("WEBVTT\n", encoding="utf-8")
    job = replace(
        _job(relpath, JobStatus.COMPLETED),
        video_path=str(video),
        vtt_path=str(vtt),
    )
    service = UploadService(
        WebStore(layout.state),
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: [job],
        check=lambda _repo, _paths: {relpath: _identity(relpath)},
        runner=lambda *_args, **_kwargs: None,
    )

    items, _checks = service.candidates("batch-1")

    assert len(items) == 1
    assert items[0].video == video
    assert items[0].vtt == vtt


def test_second_start_while_runner_is_active_is_busy(tmp_path: Path) -> None:
    relpath = "root/Toan6/Bài 19_X/T48_Y"
    layout = WebLayout(tmp_path / "data").ensure()
    _output(layout, relpath)
    entered = threading.Event()
    release = threading.Event()

    def runner(_repo, _export, items, **kwargs):
        entered.set()
        assert release.wait(2)
        return UploadOutcome(
            "succeeded",
            len(items),
            len(items),
            len(items),
            len(items),
            tmp_path / "video.json",
            tmp_path / "cc.json",
            [],
            kwargs["log_path"],
        )

    service = UploadService(
        WebStore(layout.state),
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: [_job(relpath, JobStatus.COMPLETED)],
        check=lambda _repo, _paths: {relpath: _identity(relpath)},
        runner=runner,
    )

    service.start("batch-1", dry_run=True)
    assert entered.wait(1)
    with pytest.raises(UploadBusyError, match="Đang có một lượt upload khác chạy"):
        service.start("batch-1", dry_run=True)
    release.set()
    assert _wait_for(service, "succeeded").video_ok == 1


def test_restart_marks_running_record_interrupted(tmp_path: Path) -> None:
    layout = WebLayout(tmp_path / "data").ensure()
    store = WebStore(layout.state)
    running = UploadRunRecord(
        id="run-1",
        batch_id="batch-1",
        dry_run=True,
        status="running",
        started_at="2026-10-20T10:00:00+00:00",
        finished_at=None,
        phase="video",
        current=1,
        total=2,
        video_total=2,
        video_ok=0,
        cc_total=0,
        cc_ok=0,
        video_manifest=None,
        cc_manifest=None,
        log_path=None,
        errors=[],
    )
    store.save_uploads([running])

    service = UploadService(
        store,
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: [],
    )

    recovered = service.runs()[0]
    assert recovered.status == "interrupted"
    assert recovered.finished_at is not None
    assert store.load_uploads()[0].status == "interrupted"


def test_finished_record_persists_outcome_counts_and_manifests(tmp_path: Path) -> None:
    relpath = "root/Toan6/Bài 19_X/T48_Y"
    layout = WebLayout(tmp_path / "data").ensure()
    _output(layout, relpath)
    video_manifest = tmp_path / "video.json"
    cc_manifest = tmp_path / "cc.json"

    def runner(_repo, _export, _items, **kwargs):
        kwargs["on_progress"](
            type("Progress", (), {"phase": "video", "current": 1, "total": 1, "errors": []})()
        )
        return UploadOutcome(
            "partial",
            2,
            1,
            1,
            1,
            video_manifest,
            cc_manifest,
            ["một lỗi"],
            kwargs["log_path"],
        )

    service = UploadService(
        WebStore(layout.state),
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: [_job(relpath, JobStatus.COMPLETED)],
        check=lambda _repo, _paths: {relpath: _identity(relpath)},
        runner=runner,
    )

    service.start("batch-1", dry_run=False)
    record = _wait_for(service, "partial")

    assert (record.video_total, record.video_ok, record.cc_total, record.cc_ok) == (2, 1, 1, 1)
    assert record.video_manifest == str(video_manifest)
    assert record.cc_manifest == str(cc_manifest)
    assert record.errors == ["một lỗi"]
    assert WebStore(layout.state).load_uploads()[0] == record


def test_video_checkpoint_survives_restart_during_subtitles(tmp_path: Path) -> None:
    relpath = "root/Toan6/Bài 19_X/T48_Y"
    layout = WebLayout(tmp_path / "data").ensure()
    _output(layout, relpath)
    store = WebStore(layout.state)
    video_manifest = tmp_path / "video.json"
    checkpointed = threading.Event()
    release = threading.Event()

    def runner(_repo, _export, _items, **kwargs):
        kwargs["on_progress"](
            UploadProgress(
                "cc",
                0,
                1,
                [],
                video_total=1,
                video_ok=1,
                cc_total=1,
                video_manifest=video_manifest,
            )
        )
        checkpointed.set()
        assert release.wait(2)
        return UploadOutcome(
            "partial", 1, 1, 1, 0, video_manifest, None, [], kwargs["log_path"]
        )

    service = UploadService(
        store,
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: [_job(relpath, JobStatus.COMPLETED)],
        check=lambda _repo, _paths: {relpath: _identity(relpath)},
        runner=runner,
    )
    service.start("batch-1", dry_run=True)
    assert checkpointed.wait(1)

    recovered = UploadService(
        store,
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: [],
    ).runs()[0]

    assert recovered.status == "interrupted"
    assert (recovered.video_total, recovered.video_ok, recovered.cc_total) == (1, 1, 1)
    assert recovered.video_manifest == str(video_manifest)
    release.set()
    assert _wait_for(service, "partial")


def test_stop_terminates_child_and_marks_run_interrupted(tmp_path: Path) -> None:
    relpath = "root/Toan6/Bài 19_X/T48_Y"
    layout = WebLayout(tmp_path / "data").ensure()
    _output(layout, relpath)
    child_ready = threading.Event()
    child_stopped = threading.Event()

    class Child:
        def terminate(self):
            child_stopped.set()

        def kill(self):
            child_stopped.set()

    child = Child()

    def runner(_repo, _export, items, **kwargs):
        kwargs["on_process"](child)
        child_ready.set()
        assert child_stopped.wait(2)
        kwargs["on_process"](None)
        return UploadOutcome(
            "failed", len(items), 0, 0, 0, None, None, [], kwargs["log_path"]
        )

    service = UploadService(
        WebStore(layout.state),
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: [_job(relpath, JobStatus.COMPLETED)],
        check=lambda _repo, _paths: {relpath: _identity(relpath)},
        runner=runner,
    )
    service.start("batch-1", dry_run=True)
    assert child_ready.wait(1)

    service.stop(timeout=1)

    assert child_stopped.is_set()
    record = service.runs()[0]
    assert record.status == "interrupted"
    assert record.finished_at is not None


def test_no_candidates_has_exact_vietnamese_error(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        [],
        lambda _repo, _paths: {},
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(UploadNotReadyError, match="Batch chưa có bài nào upload được"):
        service.candidates("batch-1")
