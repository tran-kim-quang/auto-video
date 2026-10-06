import importlib.util
from pathlib import Path
import subprocess
import threading

import pytest

import video_workflow.libreoffice as libreoffice
from video_workflow.libreoffice import export_slides
from video_workflow.powerpoint import PowerPointExportError


def test_libreoffice_backend_exists() -> None:
    assert importlib.util.find_spec("video_workflow.libreoffice") is not None


def test_libreoffice_uses_literal_paths_and_isolated_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pptx = tmp_path / "Bài giảng $(không chạy).pptx"
    pptx.write_bytes(b"pptx")
    calls: list[tuple[list[str], str]] = []

    def fake_run(argv, action, cancel_event):
        calls.append((argv, action))
        if action == "LibreOffice conversion":
            Path(argv[argv.index("--outdir") + 1], "generated.pdf").write_bytes(b"pdf")
        else:
            Path(f"{argv[-1]}.png").write_bytes(b"png")
        return "", ""

    monkeypatch.setattr(libreoffice.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(libreoffice, "_run_process", fake_run)

    result = export_slides(pptx, [2, 1, 2], tmp_path / "ảnh")

    office_argv = calls[0][0]
    assert str(pptx.resolve()) in office_argv
    assert any(item.startswith("-env:UserInstallation=file:") for item in office_argv)
    assert list(result) == [1, 2]
    poppler_calls = [argv for argv, action in calls if action == "Poppler slide rasterization"]
    assert [argv[argv.index("-f") + 1] for argv in poppler_calls] == ["1", "2"]
    assert all(argv[argv.index("-scale-to-x") + 1] == "1280" for argv in poppler_calls)
    assert all(argv[argv.index("-scale-to-y") + 1] == "720" for argv in poppler_calls)


def test_libreoffice_accepts_the_single_generated_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pptx = tmp_path / "deck.with.dots.pptx"
    pptx.write_bytes(b"pptx")

    def fake_run(argv, action, cancel_event):
        if action == "LibreOffice conversion":
            Path(argv[argv.index("--outdir") + 1], "title-from-metadata.pdf").write_bytes(b"pdf")
        else:
            Path(f"{argv[-1]}.png").write_bytes(b"png")
        return "", ""

    monkeypatch.setattr(libreoffice.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(libreoffice, "_run_process", fake_run)

    assert export_slides(pptx, [1], tmp_path / "images")[1].name == "slide_001.png"


@pytest.mark.parametrize(
    ("missing", "message"),
    [("office", "libreoffice-impress-nogui"), ("pdftocairo", "poppler-utils")],
)
def test_missing_tool_names_the_ubuntu_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
    message: str,
) -> None:
    pptx = tmp_path / "slides.pptx"
    pptx.write_bytes(b"pptx")

    def fake_which(name: str) -> str | None:
        if missing == "office" and name in {"libreoffice", "soffice"}:
            return None
        if name == missing:
            return None
        return f"/usr/bin/{name}"

    monkeypatch.setattr(libreoffice.shutil, "which", fake_which)

    with pytest.raises(PowerPointExportError, match=message):
        export_slides(pptx, [1], tmp_path / "images")


class FakeProcess:
    def __init__(
        self,
        *,
        returncode: int | None,
        stdout: str = "",
        stderr: str = "",
        timeout_after_terminate: bool = False,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timeout_after_terminate = timeout_after_terminate
        self.terminated = False
        self.killed = False

    def communicate(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.stdout, self.stderr

    def terminate(self) -> None:
        self.terminated = True
        if not self.timeout_after_terminate:
            self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def test_process_failure_includes_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess(returncode=7, stderr="filter failed")
    monkeypatch.setattr(libreoffice.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(PowerPointExportError, match="filter failed"):
        libreoffice._run_process(["soffice"], "LibreOffice conversion", None)


def test_process_uses_argv_without_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = []

    def fake_popen(argv, **kwargs):
        captured.append((argv, kwargs))
        return FakeProcess(returncode=0, stdout="converted")

    monkeypatch.setattr(libreoffice.subprocess, "Popen", fake_popen)

    assert libreoffice._run_process(["soffice", "a b.pptx"], "convert", None) == ("converted", "")
    assert captured == [(["soffice", "a b.pptx"], {
        "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True, "shell": False,
    })]


def test_cancelled_process_is_killed_after_terminate_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    event = threading.Event()
    event.set()
    process = FakeProcess(returncode=None, timeout_after_terminate=True)
    monkeypatch.setattr(libreoffice.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(PowerPointExportError, match="cancelled"):
        libreoffice._run_process(["soffice"], "LibreOffice conversion", event)

    assert process.terminated is True
    assert process.killed is True
