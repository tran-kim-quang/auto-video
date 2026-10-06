from __future__ import annotations

import queue
from pathlib import Path

import pytest

from video_workflow.job_models import GlobalSettings, JobRecord
from video_workflow.json_store import JsonStore
from video_workflow.queue_controller import QueueController
from video_workflow import ui as ui_module
from video_workflow.ui import JobFormData, WorkflowApp, _open_directory
from video_workflow.worker import WorkerEvent


class _Var:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class _Controller:
    settings = GlobalSettings()

    def __init__(self) -> None:
        self.enqueued = []
        self.globals = []
        self.merged = []

    def enqueue(self, **kwargs):
        self.enqueued.append(kwargs)

    def enqueue_merge(self, **kwargs):
        self.merged.append(kwargs)

    def set_global_assets(self, logo, outro):
        logo = Path(logo) if logo is not None else None
        outro = Path(outro) if outro is not None else None
        self.globals.append((logo, outro))
        self.settings = GlobalSettings(logo, outro)

    def jobs(self):
        return ()


class _Worker:
    def __init__(self) -> None:
        self.events = queue.Queue()
        self.wakes = 0
        self.stops = 0

    def wake(self):
        self.wakes += 1

    def stop(self, timeout=10):
        self.stops += 1

    def is_alive(self):
        return False


def test_open_output_folder_uses_xdg_open_on_linux(tmp_path: Path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr("video_workflow.ui.sys.platform", "linux")
    monkeypatch.setattr(
        "video_workflow.ui.subprocess.Popen", lambda argv: calls.append(argv)
    )
    _open_directory(tmp_path)
    assert calls == [["xdg-open", str(tmp_path)]]


def test_open_output_folder_uses_startfile_on_windows(
    tmp_path: Path, monkeypatch
) -> None:
    calls = []
    monkeypatch.setattr("video_workflow.ui.sys.platform", "win32")
    monkeypatch.setattr(
        "video_workflow.ui.os.startfile", lambda path: calls.append(path), raising=False
    )
    _open_directory(tmp_path)
    assert calls == [tmp_path]


def _app(tmp_path: Path) -> WorkflowApp:
    app = WorkflowApp.__new__(WorkflowApp)
    app.controller = _Controller()
    app.worker = _Worker()
    app.source_var = _Var(str(tmp_path / "audio.mp3"))
    app.pptx_var = _Var(str(tmp_path / "slides.pptx"))
    app.timeline_var = _Var(str(tmp_path / "timeline.txt"))
    app.output_name_var = _Var("lesson")
    app.output_directory_var = _Var(str(tmp_path))
    app.first_video_var = _Var(str(tmp_path / "first.mp4"))
    app.second_video_var = _Var(str(tmp_path / "second.mp4"))
    app.merge_output_name_var = _Var("merged")
    app.merge_output_directory_var = _Var(str(tmp_path))
    app.logo_var = _Var()
    app.outro_var = _Var()
    app.status_var = _Var()
    app._closing = False
    app.refresh_jobs = lambda: None
    app._errors = []
    app.show_error = lambda message: app._errors.append(message)
    return app


def test_job_form_requires_all_fields_and_maps_paths(tmp_path: Path) -> None:
    data = JobFormData.from_strings(
        " a.mp3 ", "b.pptx", "c.txt", " lesson ", str(tmp_path)
    )
    assert data.source_media == Path("a.mp3")
    assert data.output_name == "lesson"
    with pytest.raises(ValueError, match="source media"):
        JobFormData.from_strings("", "b", "c", "name", str(tmp_path))


def test_submit_enqueues_and_wakes_worker(tmp_path: Path) -> None:
    app = _app(tmp_path)
    app.submit_job()
    assert app.controller.enqueued[0]["output_name"] == "lesson"
    assert app.controller.enqueued[0]["source_media"] == tmp_path / "audio.mp3"
    assert app.worker.wakes == 1


def test_submit_validation_error_does_not_enqueue(tmp_path: Path) -> None:
    app = _app(tmp_path)
    app.source_var.set("")
    app.submit_job()
    assert app.controller.enqueued == []
    assert app._errors


def test_saving_complete_global_paths_wakes_worker(tmp_path: Path) -> None:
    app = _app(tmp_path)
    logo, outro = tmp_path / "logo.png", tmp_path / "outro.mp4"
    logo.write_bytes(b"x")
    outro.write_bytes(b"x")
    app.logo_var.set(str(logo))
    app.outro_var.set(str(outro))
    app.save_global_assets()
    assert app.controller.globals == [(logo, outro)]
    assert app.worker.wakes == 1


def test_saving_blank_global_assets_is_allowed_and_wakes_worker(tmp_path: Path) -> None:
    app = _app(tmp_path)

    app.save_global_assets()

    assert app.controller.globals == [(None, None)]
    assert app._errors == []
    assert app.worker.wakes == 1


def test_poll_events_refreshes_rows_and_close_stops_worker(tmp_path: Path) -> None:
    app = _app(tmp_path)
    refreshed = []
    app.refresh_jobs = lambda: refreshed.append(True)
    app.worker.events.put(WorkerEvent("stage", "id", "joining"))
    app.process_worker_events()
    assert refreshed == [True]
    assert app.status_var.get() == "joining"

    class Root:
        destroyed = False

        def destroy(self):
            self.destroyed = True

    app.root = Root()
    app.close()
    assert app.worker.stops == 1
    assert app.root.destroyed is True


def test_close_waits_for_worker_cleanup_before_destroying_root(tmp_path: Path) -> None:
    app = _app(tmp_path)
    app.worker.alive = True
    app.worker.is_alive = lambda: app.worker.alive

    class Root:
        destroyed = False
        callbacks = []

        def after(self, _delay, callback):
            self.callbacks.append(callback)

        def destroy(self):
            self.destroyed = True

    app.root = Root()
    app.close()
    assert app.root.destroyed is False
    assert len(app.root.callbacks) == 1
    app.worker.alive = False
    app.root.callbacks.pop()()
    assert app.root.destroyed is True


def test_merge_form_requires_both_videos_and_maps_paths(tmp_path: Path) -> None:
    data = ui_module.MergeFormData.from_strings(
        " first.mp4 ", "second.mp4", " merged ", str(tmp_path)
    )

    assert data.first_video == Path("first.mp4")
    assert data.second_video == Path("second.mp4")
    assert data.output_name == "merged"
    with pytest.raises(ValueError, match="first video"):
        ui_module.MergeFormData.from_strings("", "second.mp4", "merged", str(tmp_path))


def test_submit_merge_enqueues_and_wakes_worker(tmp_path: Path) -> None:
    app = _app(tmp_path)

    app.submit_merge_job()

    assert app.controller.merged == [
        {
            "first_video": tmp_path / "first.mp4",
            "second_video": tmp_path / "second.mp4",
            "output_name": "merged",
            "output_directory": tmp_path,
        }
    ]
    assert app.merge_output_name_var.get() == ""
    assert app.worker.wakes == 1


def test_submit_batch_folder_queues_pdf_style_jobs_without_reports(
    tmp_path: Path,
) -> None:
    leaf = tmp_path / "Toan8" / "Bài 1_Đơn thức"
    leaf.mkdir(parents=True)
    for name in (
        "TOAN7_C4_B13_T38_1.pptx",
        "TOAN7_C4_B13_T38_1.mp4",
        "timeline_slide_TOAN7_C4_B13_T38_1.txt",
    ):
        (leaf / name).write_bytes(b"input")
    app = WorkflowApp.__new__(WorkflowApp)
    app.controller = QueueController(JsonStore(tmp_path / "data"))
    app.worker = _Worker()
    app.batch_root_var = _Var(str(tmp_path / "Toan8"))
    app.status_var = _Var()
    app.refresh_jobs = lambda: None
    app._warnings = []
    app.show_warning = lambda message: app._warnings.append(message)
    app._errors = []
    app.show_error = lambda message: app._errors.append(message)

    app.submit_batch_folder()

    jobs = app.controller.jobs()
    assert len(jobs) == 1
    assert jobs[0].output_path == leaf / "output" / "TOAN7_C4_B13_T38_1.mp4"
    assert jobs[0].write_report is False
    assert app.worker.wakes == 1
    assert app.status_var.get() == "Batch: 1 added, 0 skipped, 0 issue(s)"


def test_part_1_file_status_does_not_require_global_outro(tmp_path: Path) -> None:
    app = _app(tmp_path)
    for name in ("source.mp4", "slides.pptx", "timeline.txt", "logo.png"):
        (tmp_path / name).write_bytes(b"input")
    app.controller.settings = GlobalSettings(
        logo=tmp_path / "logo.png", outro=tmp_path / "missing-outro.mp4"
    )
    job = JobRecord.new(
        source_media=tmp_path / "source.mp4",
        pptx=tmp_path / "slides.pptx",
        timeline=tmp_path / "timeline.txt",
        output_name="lesson_1",
        output_directory=tmp_path,
        use_outro=False,
    )

    assert "outro" not in app._missing_job_paths(job)


def test_waiting_batch_job_can_overwrite_an_existing_output(tmp_path: Path) -> None:
    app = _app(tmp_path)
    for name in ("source.mp4", "slides.pptx", "timeline.txt"):
        (tmp_path / name).write_bytes(b"input")
    job = JobRecord.new(
        source_media=tmp_path / "source.mp4",
        pptx=tmp_path / "slides.pptx",
        timeline=tmp_path / "timeline.txt",
        output_name="lesson_1",
        output_directory=tmp_path,
        overwrite_output=True,
    )
    job.output_path.write_bytes(b"previous-video")

    assert app._waiting_job_ready(job) is True
    assert app._job_files_summary(job) == "Will overwrite output"


def test_batch_tab_guide_contains_folder_and_file_naming_example() -> None:
    guide = ui_module.BATCH_FOLDER_GUIDE

    assert "Folder lá: tên tùy ý" in guide
    assert "demo_Test/" in guide
    assert "TOAN7_C4_B13_T38_1.pptx" in guide
    assert "TOAN7_C4_B13_T38_1.mp4" in guide
    assert "timeline_slide_TOAN7_C4_B13_T38_1.txt" in guide
    assert "TOAN7_C4_B13_T38_2.pptx" in guide
    assert "TOAN7_C4_B13_T38_2.txt" in guide
    assert "output/TOAN7_C4_B13_T38_2.mp4" in guide
    assert "Outro: chỉ part 2" in guide
    assert "Mỗi lần scan đều thêm job mới" in guide
