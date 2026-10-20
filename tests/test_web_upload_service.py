from __future__ import annotations

import copy
import json
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from video_workflow.job_models import JobStatus
from video_workflow.web.config import WebLayout
from video_workflow.web.output_location import output_paths, parked_paths
from video_workflow.web.production_api import ProductionApiError
from video_workflow.web.r2_upload import UploadOutcome, UploadProgress
from video_workflow.web.state import UploadRunRecord, WebJob, WebStore
from video_workflow.web.upload_identity import UploadCheck, UploadIdentity
from video_workflow.web.upload_service import (
    AdminTokenRequiredError,
    OverwriteConfirmationRequired,
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


def _identity(
    relpath: str,
    *,
    issue: str | None = None,
    lesson_slug: str = "19-bai-19-x",
    lesson_title: str = "Bài 19: X",
    section_no: int = 48,
    part_no: int = 1,
    video_slug: str = "48-y",
    video_title: str = "Tiết 48 - Y",
) -> UploadCheck:
    triple = "/".join(relpath.split("/")[-3:])
    return UploadCheck(
        relpath=relpath,
        identity=UploadIdentity(
            triple=triple,
            course_id=601,
            course_slug="601-toan-hoc-lop-6",
            lesson_slug=lesson_slug,
            lesson_order=19,
            lesson_title=lesson_title,
            section_no=section_no,
            part_no=part_no,
            video_slug=video_slug,
            video_title=video_title,
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


def _production_lesson(
    check: UploadCheck,
    *,
    lesson_id: str = "lesson-19",
    order: int = 29,
    duration: int = 5,
    description: str | None = None,
    videos: list[dict] | None = None,
) -> dict:
    assert check.identity is not None
    identity = check.identity
    return {
        "id": lesson_id,
        "courseId": identity.course_id,
        "title": identity.lesson_title,
        "slug": identity.lesson_slug,
        "order": order,
        "description": description,
        "durationMinutes": duration,
        "periods": [
            {
                "id": f"period-{section}",
                "order": section,
                "title": title,
            }
            for section, title in [(identity.section_no, identity.video_title)]
        ],
        "videos": videos or [],
    }


class _FakeProductionApi:
    def __init__(self, snapshots: list[list[dict]], *, token: str = "tok") -> None:
        self.snapshots = [copy.deepcopy(value) for value in snapshots]
        self.last_snapshot = copy.deepcopy(snapshots[-1])
        self.token = token
        self.calls: list[str] = []
        self.cc_result = {"inserted": 1, "updated": 0, "skipped": 0, "total": 1}
        self.cc_error: ProductionApiError | None = None

    def list_lessons(self, course_id: int) -> list[dict]:
        self.calls.append(f"list:{course_id}")
        if self.snapshots:
            self.last_snapshot = self.snapshots.pop(0)
        return copy.deepcopy(self.last_snapshot)

    def import_video_manifest(self, path: Path) -> str:
        self.calls.append("import_video")
        self.video_import = json.loads(path.read_text(encoding="utf-8"))
        return "job-production-1"

    def import_cc_manifest(self, path: Path) -> dict:
        self.calls.append("import_cc")
        self.cc_import = json.loads(path.read_text(encoding="utf-8"))
        if self.cc_error is not None:
            raise self.cc_error
        return dict(self.cc_result)


def _manifest_runner(
    lesson: dict,
    *,
    duration: int = 7,
    status: str = "succeeded",
    captured_items: list | None = None,
):
    def run(_repo, export_dir, items, **kwargs):
        if captured_items is not None:
            captured_items.extend(items)
        export_dir = Path(export_dir)
        video_manifest = export_dir / "source-video.json"
        cc_manifest = export_dir / "source-cc.json"
        identity_triple = items[0].triple if items else "Toan6/Bài 19_X/T48_Y"
        video_manifest.write_text(
            json.dumps(
                {
                    "videos": [
                        {
                            "relativePath": f"{identity_triple}.mp4",
                            "courseId": 601,
                            "lessonOrder": 19,
                            "lessonSlug": "19-bai-19-x",
                            "lessonTitle": "Bài 19: X",
                            "sectionNo": 48,
                            "partNo": 1,
                            "videoSlug": "48-y",
                            "videoTitle": "Tiết 48 - Y",
                            "durationMinutes": duration,
                            "videoKey": "videos/keep/master.m3u8",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        cc_manifest.write_text(
            json.dumps(
                {
                    "subtitles": [
                        {
                            "relativePath": f"{identity_triple}/vi.vtt",
                            "courseId": 601,
                            "lessonSlug": "19-bai-19-x",
                            "sectionNo": 48,
                            "partNo": 1,
                            "r2Key": "videos/keep/subtitles/vi.vtt",
                            "linkUrl": "https://cdn/keep/vi.vtt",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        Path(kwargs["log_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["log_path"]).write_text("upload local fake\n", encoding="utf-8")
        return UploadOutcome(
            status,
            len(items),
            len(items),
            len(items),
            len(items),
            video_manifest,
            cc_manifest,
            [],
            kwargs["log_path"],
        )

    return run


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

    service.start("batch-1", dry_run=True)
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


def _external_completed_job(
    output_root: Path, relpath: str
) -> tuple[WebJob, Path, Path]:
    video, vtt = output_paths(output_root, relpath)
    video.parent.mkdir(parents=True, exist_ok=True)
    vtt.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"video")
    vtt.write_text("WEBVTT\n", encoding="utf-8")
    job = replace(
        WebJob.new(
            batch_id="batch-1",
            leaf_relpath=relpath,
            profile_id="abc12345",
            script_chars=10,
            output_root=str(output_root),
        ),
        status=JobStatus.COMPLETED,
        video_path=str(video),
        vtt_path=str(vtt),
    )
    return job, video, vtt


def _job_updater(store: WebStore, jobs: list[WebJob]):
    def update(job_id: str, video_path: str, vtt_path: str) -> WebJob:
        index = next(index for index, job in enumerate(jobs) if job.id == job_id)
        jobs[index] = replace(
            jobs[index], video_path=video_path, vtt_path=vtt_path
        )
        store.save_jobs(jobs)
        return jobs[index]

    return update


def test_real_start_requires_admin_token_before_upload(tmp_path: Path) -> None:
    relpath = "root/Toan6/Bài 19_X/T48_Y"
    layout = WebLayout(tmp_path / "data").ensure()
    _output(layout, relpath)
    service = UploadService(
        WebStore(layout.state),
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: [_job(relpath, JobStatus.COMPLETED)],
        check=lambda _repo, _paths: {relpath: _identity(relpath)},
        runner=lambda *_args, **_kwargs: None,
    )

    with pytest.raises(
        AdminTokenRequiredError, match="^Cần token admin để upload lên R2$"
    ):
        service.start("batch-1", dry_run=False)


def test_sync_classifies_and_parks_only_missing_production_lesson(
    tmp_path: Path,
) -> None:
    layout = WebLayout(tmp_path / "data").ensure()
    output_root = tmp_path / "results"
    ready_path = "root/Toan6/Bài 19_X/T48_Y"
    missing_path = "root/Toan6/Bài 20_X/T49_Z"
    ready_check = _identity(ready_path)
    missing_check = _identity(
        missing_path,
        lesson_slug="20-bai-20-x",
        lesson_title="Bài 20: X",
        section_no=49,
        video_slug="49-z",
        video_title="Tiết 49 - Z",
    )
    ready_job, ready_video, _ready_vtt = _external_completed_job(
        output_root, ready_path
    )
    missing_job, missing_video, _missing_vtt = _external_completed_job(
        output_root, missing_path
    )
    jobs = [ready_job, missing_job]
    store = WebStore(layout.state)
    store.save_jobs(jobs)
    api = _FakeProductionApi([[_production_lesson(ready_check)]])
    uploaded: list = []

    def failed_runner(_repo, _export, items, **kwargs):
        uploaded.extend(items)
        return UploadOutcome(
            "failed", len(items), 0, 0, 0, None, None, [], kwargs["log_path"]
        )

    service = UploadService(
        store,
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: jobs,
        check=lambda _repo, _paths: {
            ready_path: ready_check,
            missing_path: missing_check,
        },
        runner=failed_runner,
        production_api_factory=lambda _url, _token: api,
        update_output_paths=_job_updater(store, jobs),
    )

    started = service.start("batch-1", dry_run=False, admin_token="tok")
    record = _wait_for(service, "failed")

    parked_video, parked_vtt = parked_paths(output_root, missing_path)
    assert started.production_checked is True
    assert [item.relpath for item in uploaded] == [ready_path]
    assert ready_video.is_file()
    assert not missing_video.exists()
    assert parked_video.is_file() and parked_vtt.is_file()
    assert record.skipped_not_on_production == [
        {"relpath": missing_path, "moved_to": str(parked_video)}
    ]
    assert WebStore(layout.state).load_jobs()[1].video_path == str(parked_video)


def test_parking_updates_job_from_requested_batch_when_paths_overlap(
    tmp_path: Path,
) -> None:
    layout = WebLayout(tmp_path / "data").ensure()
    output_root = tmp_path / "results"
    relpath = "root/Toan6/Bài 20_X/T49_Z"
    check = _identity(
        relpath,
        lesson_slug="20-bai-20-x",
        lesson_title="Bài 20: X",
        section_no=49,
        video_slug="49-z",
    )
    current, video, _vtt = _external_completed_job(output_root, relpath)
    older = replace(current, id="older-job", batch_id="batch-0")
    jobs = [older, current]
    store = WebStore(layout.state)
    store.save_jobs(jobs)
    api = _FakeProductionApi([[]])
    service = UploadService(
        store,
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: jobs,
        check=lambda _repo, _paths: {relpath: check},
        runner=lambda *_args, **_kwargs: pytest.fail("missing lesson must not upload"),
        production_api_factory=lambda _url, _token: api,
        update_output_paths=_job_updater(store, jobs),
    )

    record = service.start("batch-1", dry_run=False, admin_token="tok")

    parked_video, _parked_vtt = parked_paths(output_root, relpath)
    assert record.status == "succeeded"
    assert not video.exists() and parked_video.is_file()
    assert jobs[0].video_path == str(video)
    assert jobs[1].video_path == str(parked_video)


def test_dry_run_with_token_does_not_move_or_import(tmp_path: Path) -> None:
    layout = WebLayout(tmp_path / "data").ensure()
    output_root = tmp_path / "results"
    relpath = "root/Toan6/Bài 20_X/T49_Z"
    check = _identity(
        relpath,
        lesson_slug="20-bai-20-x",
        lesson_title="Bài 20: X",
        section_no=49,
        video_slug="49-z",
    )
    job, video, _vtt = _external_completed_job(output_root, relpath)
    api = _FakeProductionApi([[]])
    service = UploadService(
        WebStore(layout.state),
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: [job],
        check=lambda _repo, _paths: {relpath: check},
        runner=_manifest_runner({}, status="succeeded"),
        production_api_factory=lambda _url, _token: api,
    )

    service.start("batch-1", dry_run=True, admin_token="tok")
    record = _wait_for(service, "succeeded")

    assert video.is_file()
    assert record.skipped_not_on_production == [
        {"relpath": relpath, "moved_to": str(video)}
    ]
    assert "import_video" not in api.calls
    assert "import_cc" not in api.calls


def test_overwrite_requires_explicit_confirmation(tmp_path: Path) -> None:
    relpath = "root/Toan6/Bài 19_X/T48_Y"
    layout = WebLayout(tmp_path / "data").ensure()
    _output(layout, relpath)
    check = _identity(relpath)
    identity = check.identity
    assert identity is not None
    existing = {
        "id": "video-48",
        "sectionNo": 48,
        "partNo": 1,
        "slug": identity.video_slug,
        "durationMinutes": 5,
    }
    api = _FakeProductionApi(
        [[_production_lesson(check, videos=[existing])]]
    )
    service = UploadService(
        WebStore(layout.state),
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: [_job(relpath, JobStatus.COMPLETED)],
        check=lambda _repo, _paths: {relpath: check},
        runner=lambda *_args, **_kwargs: None,
        production_api_factory=lambda _url, _token: api,
    )

    with pytest.raises(OverwriteConfirmationRequired) as caught:
        service.start("batch-1", dry_run=False, admin_token="tok")

    assert caught.value.overwrite == [relpath]


def test_complete_import_flow_writes_four_files_and_calls_api_in_order(
    tmp_path: Path,
) -> None:
    relpath = "root/Toan6/Bài 19_X/T48_Y"
    layout = WebLayout(tmp_path / "data").ensure()
    _output(layout, relpath)
    check = _identity(relpath)
    before = _production_lesson(check)
    after = copy.deepcopy(before)
    after["durationMinutes"] = 7
    after["videos"] = [
        {
            "id": "video-48",
            "sectionNo": 48,
            "partNo": 1,
            "slug": "48-y",
            "durationMinutes": 7,
        }
    ]
    api = _FakeProductionApi([[before], [after], [after]])
    service = UploadService(
        WebStore(layout.state),
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: [_job(relpath, JobStatus.COMPLETED)],
        check=lambda _repo, _paths: {relpath: check},
        runner=_manifest_runner(before),
        production_api_factory=lambda _url, _token: api,
        min_wait_seconds=0,
        max_wait_seconds=1,
        poll_seconds=0.01,
    )

    service.start("batch-1", dry_run=False, admin_token="tok")
    record = _wait_for(service, "succeeded")

    export_dir = next((layout.root / "exports" / "batch-1").iterdir())
    expected = [
        export_dir / "production-before.json",
        export_dir / "import" / "bulk-upload-import.json",
        export_dir / "import" / "cc-upload-import.json",
        export_dir / "production-after.json",
    ]
    assert all(path.is_file() for path in expected)
    assert api.calls == [
        "list:601",
        "import_video",
        "list:601",
        "import_cc",
        "list:601",
    ]
    assert api.video_import["videos"][0]["lessonOrder"] == 29
    assert record.import_job_id == "job-production-1"
    assert record.cc_import_result == api.cc_result
    assert record.import_manifest == str(expected[1])
    assert record.backup_path == str(expected[0])


def test_production_run_stays_running_while_import_is_being_prepared(
    tmp_path: Path,
) -> None:
    relpath = "root/Toan6/Bài 19_X/T48_Y"
    layout = WebLayout(tmp_path / "data").ensure()
    _output(layout, relpath)
    check = _identity(relpath)
    before = _production_lesson(check)
    after = copy.deepcopy(before)
    after["durationMinutes"] = 7
    after["videos"] = [
        {
            "id": "video-48",
            "sectionNo": 48,
            "partNo": 1,
            "slug": "48-y",
            "durationMinutes": 7,
        }
    ]
    api = _FakeProductionApi([[before], [after], [after]])
    store = WebStore(layout.state)
    service = UploadService(
        store,
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: [_job(relpath, JobStatus.COMPLETED)],
        check=lambda _repo, _paths: {relpath: check},
        runner=_manifest_runner(before),
        production_api_factory=lambda _url, _token: api,
        min_wait_seconds=0,
        max_wait_seconds=1,
        poll_seconds=0.01,
    )
    writing_backup = threading.Event()
    release = threading.Event()
    original_write_json = service._write_json

    def blocking_write(path: Path, value) -> None:
        if path.name == "production-before.json":
            writing_backup.set()
            assert release.wait(2)
        original_write_json(path, value)

    service._write_json = blocking_write
    service.start("batch-1", dry_run=False, admin_token="tok")
    assert writing_backup.wait(1)
    try:
        checkpoint = store.load_uploads()[0]
        assert checkpoint.status == "running"
        assert checkpoint.phase == "import_video"
        assert checkpoint.finished_at is None
    finally:
        release.set()

    assert _wait_for(service, "succeeded").import_job_id == "job-production-1"


def test_worker_timeout_fails_without_importing_subtitles(tmp_path: Path) -> None:
    relpath = "root/Toan6/Bài 19_X/T48_Y"
    layout = WebLayout(tmp_path / "data").ensure()
    _output(layout, relpath)
    check = _identity(relpath)
    before = _production_lesson(check)
    api = _FakeProductionApi([[before]])

    class Clock:
        now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = Clock()
    service = UploadService(
        WebStore(layout.state),
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: [_job(relpath, JobStatus.COMPLETED)],
        check=lambda _repo, _paths: {relpath: check},
        runner=_manifest_runner(before),
        production_api_factory=lambda _url, _token: api,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        min_wait_seconds=0,
        max_wait_seconds=0.2,
        poll_seconds=0.1,
    )

    service.start("batch-1", dry_run=False, admin_token="tok")
    record = _wait_for(service, "failed")

    assert "Worker chưa xử lý xong manifest video sau 10 phút" in record.errors
    assert "import_video" in api.calls
    assert "import_cc" not in api.calls


def test_unexpected_production_change_finishes_needs_review(tmp_path: Path) -> None:
    relpath = "root/Toan6/Bài 19_X/T48_Y"
    layout = WebLayout(tmp_path / "data").ensure()
    _output(layout, relpath)
    check = _identity(relpath)
    before = _production_lesson(check)
    other = {
        **_production_lesson(check, lesson_id="other", order=30),
        "title": "Bài khác",
        "slug": "bai-khac",
    }
    after = copy.deepcopy(before)
    after["durationMinutes"] = 7
    after["videos"] = [
        {
            "id": "video-48",
            "sectionNo": 48,
            "partNo": 1,
            "slug": "48-y",
            "durationMinutes": 7,
        }
    ]
    changed_other = copy.deepcopy(other)
    changed_other["title"] = "Bài khác bị đổi"
    api = _FakeProductionApi(
        [[before, other], [after, changed_other], [after, changed_other]]
    )
    service = UploadService(
        WebStore(layout.state),
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: [_job(relpath, JobStatus.COMPLETED)],
        check=lambda _repo, _paths: {relpath: check},
        runner=_manifest_runner(before),
        production_api_factory=lambda _url, _token: api,
        min_wait_seconds=0,
        max_wait_seconds=1,
        poll_seconds=0.01,
    )

    service.start("batch-1", dry_run=False, admin_token="tok")
    record = _wait_for(service, "needs_review")

    assert any("Bài khác bị đổi" in value for value in record.verify_differences)


def test_token_is_redacted_from_state_and_log_on_import_error(tmp_path: Path) -> None:
    token = "tok-SECRET"
    relpath = "root/Toan6/Bài 19_X/T48_Y"
    layout = WebLayout(tmp_path / "data").ensure()
    _output(layout, relpath)
    check = _identity(relpath)
    before = _production_lesson(check)
    after = copy.deepcopy(before)
    after["durationMinutes"] = 7
    after["videos"] = [
        {
            "id": "video-48",
            "sectionNo": 48,
            "partNo": 1,
            "slug": "48-y",
            "durationMinutes": 7,
        }
    ]
    api = _FakeProductionApi([[before], [after]], token=token)
    api.cc_error = ProductionApiError(
        f"Backend trả HTTP 400: Bearer {token}",
        detail={"message": f"Bearer {token}", "missingTargets": ["x"]},
    )
    service = UploadService(
        WebStore(layout.state),
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: [_job(relpath, JobStatus.COMPLETED)],
        check=lambda _repo, _paths: {relpath: check},
        runner=_manifest_runner(before),
        production_api_factory=lambda _url, _token: api,
        min_wait_seconds=0,
        max_wait_seconds=1,
        poll_seconds=0.01,
    )

    service.start("batch-1", dry_run=False, admin_token=token)
    record = _wait_for(service, "failed")

    assert token not in (layout.state / "uploads.json").read_text(encoding="utf-8")
    assert token not in Path(record.log_path).read_text(encoding="utf-8")
    assert all(token not in error for error in record.errors)
    assert any("***" in error for error in record.errors)


def test_parked_ready_lesson_moves_back_before_upload(tmp_path: Path) -> None:
    layout = WebLayout(tmp_path / "data").ensure()
    output_root = tmp_path / "results"
    relpath = "root/Toan6/Bài 19_X/T48_Y"
    check = _identity(relpath)
    parked_video, parked_vtt = parked_paths(output_root, relpath)
    parked_video.parent.mkdir(parents=True, exist_ok=True)
    parked_vtt.parent.mkdir(parents=True, exist_ok=True)
    parked_video.write_bytes(b"video")
    parked_vtt.write_text("WEBVTT\n", encoding="utf-8")
    job = replace(
        WebJob.new(
            batch_id="batch-1",
            leaf_relpath=relpath,
            profile_id="abc12345",
            script_chars=10,
            output_root=str(output_root),
        ),
        status=JobStatus.COMPLETED,
        video_path=str(parked_video),
        vtt_path=str(parked_vtt),
    )
    jobs = [job]
    store = WebStore(layout.state)
    store.save_jobs(jobs)
    api = _FakeProductionApi([[_production_lesson(check)]])
    captured: list = []

    def failed_runner(_repo, _export, items, **kwargs):
        captured.extend(items)
        return UploadOutcome(
            "failed", len(items), 0, 0, 0, None, None, [], kwargs["log_path"]
        )

    service = UploadService(
        store,
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: jobs,
        check=lambda _repo, _paths: {relpath: check},
        runner=failed_runner,
        production_api_factory=lambda _url, _token: api,
        update_output_paths=_job_updater(store, jobs),
    )

    service.start("batch-1", dry_run=False, admin_token="tok")
    _wait_for(service, "failed")

    video, vtt = output_paths(output_root, relpath)
    assert video.is_file() and vtt.is_file()
    assert not parked_video.exists() and not parked_vtt.exists()
    assert captured[0].video == video
    assert WebStore(layout.state).load_jobs()[0].video_path == str(video)


def test_stop_during_worker_wait_interrupts_without_importing_cc(
    tmp_path: Path,
) -> None:
    relpath = "root/Toan6/Bài 19_X/T48_Y"
    layout = WebLayout(tmp_path / "data").ensure()
    _output(layout, relpath)
    check = _identity(relpath)
    before = _production_lesson(check)
    api = _FakeProductionApi([[before]])
    service = UploadService(
        WebStore(layout.state),
        layout,
        repo=lambda: tmp_path / "repo",
        jobs=lambda: [_job(relpath, JobStatus.COMPLETED)],
        check=lambda _repo, _paths: {relpath: check},
        runner=_manifest_runner(before),
        production_api_factory=lambda _url, _token: api,
        min_wait_seconds=60,
        max_wait_seconds=600,
        poll_seconds=0.01,
    )

    service.start("batch-1", dry_run=False, admin_token="tok")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and service.runs()[0].phase != "wait_worker":
        time.sleep(0.01)
    assert service.runs()[0].phase == "wait_worker"

    service.stop(timeout=1)
    record = _wait_for(service, "interrupted")

    assert record.status == "interrupted"
    assert "import_cc" not in api.calls
