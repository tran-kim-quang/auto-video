from __future__ import annotations

import queue
import runpy
from pathlib import Path

import pytest

from video_workflow import ui
from video_workflow.folder_batch import FolderQueueResult
from video_workflow.job_models import GlobalSettings, JobStage
from video_workflow.json_store import JsonStore
from video_workflow.queue_controller import QueueController
from video_workflow.ui import WorkflowApp, _open_directory, job_row
from video_workflow.worker import WorkerEvent


class _Var:
    def __init__(self, value="") -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Worker:
    def __init__(self) -> None:
        self.events = queue.Queue()
        self.wakes = 0
        self.stops = 0
        self.cancels = 0
        self.current_job_id = None
        self.alive = False

    def wake(self):
        self.wakes += 1

    def stop(self, timeout=10):
        self.stops += 1

    def cancel_current(self):
        self.cancels += 1

    def is_alive(self):
        return self.alive


def _files(tmp_path: Path):
    paths = {
        name: tmp_path / filename
        for name, filename in {
            "logo": "logo.png",
            "outro": "outro.mp4",
            "pptx": "lesson.pptx",
            "script": "lesson.txt",
        }.items()
    }
    for name, path in paths.items():
        path.write_text("Một." if name == "script" else name, encoding="utf-8")
    return paths


def _app(tmp_path: Path, *, save_assets: bool = True) -> WorkflowApp:
    files = _files(tmp_path)
    app = WorkflowApp.__new__(WorkflowApp)
    app.controller = QueueController(JsonStore(tmp_path / "data"))
    if save_assets:
        app.controller.set_global_assets(files["logo"], files["outro"])
    app.worker = _Worker()
    app.logo_var = _Var(str(files["logo"]) if save_assets else "")
    app.outro_var = _Var(str(files["outro"]) if save_assets else "")
    app.batch_root_var = _Var(str(tmp_path))
    app.status_var = _Var()
    app._closing = False
    app.refresh_jobs = lambda: None
    app._errors = []
    app._warnings = []
    app.show_error = app._errors.append
    app.show_warning = app._warnings.append
    return app


def test_save_assets_rejects_blank_or_missing_logo_and_outro(tmp_path: Path) -> None:
    app = _app(tmp_path, save_assets=False)

    app.save_global_assets()
    assert app._errors and "required" in app._errors[-1]

    app.logo_var.set(str(tmp_path / "missing.png"))
    app.outro_var.set(str(tmp_path / "missing.mp4"))
    app.save_global_assets()
    assert "does not exist" in app._errors[-1]
    assert app.worker.wakes == 0


def test_batch_submit_requires_assets_then_queues_pairs_and_wakes_worker(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path, save_assets=False)
    app.submit_batch_folder()
    assert "Save a valid logo and outro" in app._errors[-1]

    files = _files(tmp_path)
    app.controller.set_global_assets(files["logo"], files["outro"])
    app.submit_batch_folder()

    assert len(app.controller.jobs()) == 1
    assert app.controller.jobs()[0].pptx == files["pptx"]
    assert app.worker.wakes == 1
    assert app.status_var.get() == "Batch: 1 added, 0 issue(s)"


def test_fixed_profile_is_displayed_read_only() -> None:
    assert ui.FIXED_PROFILE_LABEL == "Teacher_male — 599fc048 (32 steps, GPU)"
    assert ui.PROFILE_ENTRY_STATE == "readonly"


def test_queue_row_shows_lesson_stage_progress_status_and_artifact_folder(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    files = _files(tmp_path)
    job = app.controller.enqueue(
        pptx=files["pptx"],
        script=files["script"],
        artifact_directory=tmp_path / "output" / "lesson",
        output_name="lesson.mp4",
    )
    app.controller.claim_next()
    app.controller.set_stage(job.id, JobStage.SYNTHESIZING_AUDIO)
    app.controller.set_progress(job.id, 1, 2)

    row = job_row(app.controller.jobs()[0])

    assert row[0] == "lesson"
    assert row[3] == "running"
    assert row[4] == "synthesizing_audio"
    assert row[5] == "1/2"
    assert row[6] == str(tmp_path / "output" / "lesson")


def test_retry_cancel_and_open_output_target_selected_job(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app(tmp_path)
    files = _files(tmp_path)
    job = app.controller.enqueue(
        pptx=files["pptx"],
        script=files["script"],
        artifact_directory=tmp_path / "output" / "lesson",
        output_name="lesson.mp4",
    )
    app.controller.claim_next()
    app.controller.mark_failed(job.id, "failed")
    app._selected_id = lambda: job.id

    app.retry_selected()
    assert app.controller.jobs()[0].status.value == "waiting"
    claimed = app.controller.claim_next()
    app.worker.current_job_id = claimed.id
    app.cancel_selected()
    assert app.worker.cancels == 1

    opened = []
    monkeypatch.setattr(ui, "_open_directory", lambda path: opened.append(path))
    app.open_selected_folder()
    assert opened == [job.artifact_directory.parent]


def test_process_events_and_close_wait_for_worker_cleanup(tmp_path: Path) -> None:
    app = _app(tmp_path)
    refreshed = []
    app.refresh_jobs = lambda: refreshed.append(True)
    app.worker.events.put(WorkerEvent("stage", "id", "synthesizing_audio"))
    app.process_worker_events()
    assert refreshed == [True]
    assert app.status_var.get() == "synthesizing_audio"

    class Root:
        destroyed = False
        callbacks = []

        def after(self, _delay, callback):
            self.callbacks.append(callback)

        def destroy(self):
            self.destroyed = True

    app.root = Root()
    app.worker.alive = True
    app.close()
    assert app.root.destroyed is False
    app.worker.alive = False
    app.root.callbacks.pop()()
    assert app.root.destroyed is True


def test_ui_has_no_single_build_or_merge_workflow_symbols() -> None:
    for name in (
        "submit_job",
        "submit_merge_job",
        "choose_source",
        "choose_timeline",
        "choose_first_video",
        "choose_second_video",
    ):
        assert not hasattr(WorkflowApp, name)
    assert "Build slide video" not in ui.BATCH_FOLDER_GUIDE
    assert "Merge 2 videos" not in ui.BATCH_FOLDER_GUIDE


def test_open_output_folder_uses_platform_launcher(tmp_path: Path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(ui.sys, "platform", "linux")
    monkeypatch.setattr(ui.subprocess, "Popen", lambda argv: calls.append(argv))
    _open_directory(tmp_path)
    assert calls == [["xdg-open", str(tmp_path)]]


def test_main_module_launches_ui_main(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(ui, "main", lambda: calls.append(True) or 0)

    with pytest.raises(SystemExit) as caught:
        runpy.run_module("video_workflow.__main__", run_name="__main__")

    assert caught.value.code == 0
    assert calls == [True]
