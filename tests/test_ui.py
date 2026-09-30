from __future__ import annotations

import queue
from pathlib import Path

import pytest

from video_workflow.job_models import GlobalSettings
from video_workflow.ui import JobFormData, WorkflowApp
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

    def enqueue(self, **kwargs):
        self.enqueued.append(kwargs)

    def set_global_assets(self, logo, outro):
        self.globals.append((Path(logo), Path(outro)))
        self.settings = GlobalSettings(Path(logo), Path(outro))

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


def _app(tmp_path: Path) -> WorkflowApp:
    app = WorkflowApp.__new__(WorkflowApp)
    app.controller = _Controller()
    app.worker = _Worker()
    app.source_var = _Var(str(tmp_path / "audio.mp3"))
    app.pptx_var = _Var(str(tmp_path / "slides.pptx"))
    app.timeline_var = _Var(str(tmp_path / "timeline.txt"))
    app.output_name_var = _Var("lesson")
    app.output_directory_var = _Var(str(tmp_path))
    app.logo_var = _Var()
    app.outro_var = _Var()
    app.status_var = _Var()
    app._closing = False
    app.refresh_jobs = lambda: None
    app._errors = []
    app.show_error = lambda message: app._errors.append(message)
    return app


def test_job_form_requires_all_fields_and_maps_paths(tmp_path: Path) -> None:
    data = JobFormData.from_strings(" a.mp3 ", "b.pptx", "c.txt", " lesson ", str(tmp_path))
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
