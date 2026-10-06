from __future__ import annotations

import sys
from pathlib import Path

from scripts import build_windows
from scripts.build_windows import pyinstaller_command
from video_workflow import ui


def test_frozen_app_stores_queue_beside_windows_executable(
    tmp_path: Path, monkeypatch
) -> None:
    executable = tmp_path / "PyramidVideoWorkflow" / "PyramidVideoWorkflow.exe"
    monkeypatch.setattr(ui.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ui.sys, "executable", str(executable))

    assert ui.application_data_root() == executable.parent / ".workflow_data"


def test_windows_build_command_creates_portable_windowed_distribution(
    tmp_path: Path,
) -> None:
    command = pyinstaller_command(tmp_path, Path(sys.executable))

    assert command[:3] == [str(Path(sys.executable)), "-m", "PyInstaller"]
    assert "--onedir" in command
    assert "--windowed" in command
    assert command[command.index("--name") + 1] == "PyramidVideoWorkflow"
    assert command[command.index("--distpath") + 1] == str(tmp_path / "dist")
    assert command[-1] == str(tmp_path / "windows_launcher.py")


def test_windows_build_prepares_spec_directory_before_running_pyinstaller(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    script = repo / "scripts" / "build_windows.py"
    python = repo / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    calls = []

    def fake_run(command, *, cwd, check):
        calls.append(command)
        if "PyInstaller" in command:
            executable = (
                repo
                / "dist"
                / "PyramidVideoWorkflow"
                / "PyramidVideoWorkflow.exe"
            )
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"exe")

    monkeypatch.setattr(build_windows, "__file__", str(script))
    monkeypatch.setattr(build_windows.sys, "platform", "win32")
    monkeypatch.setattr(build_windows.subprocess, "run", fake_run)

    assert build_windows.main() == 0
    assert (repo / "build").is_dir()
    assert len(calls) == 2
