from __future__ import annotations

import subprocess
from pathlib import Path

from scripts import repair_windows_tkinter


def test_tkinter_repair_installs_tcltk_and_verifies_python_launcher(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return {"winget": "C:/Windows/winget.exe", "py": "C:/Windows/py.exe"}.get(
            name
        )

    def fake_run(command: list[str], *, check: bool):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(repair_windows_tkinter.shutil, "which", fake_which)
    monkeypatch.setattr(repair_windows_tkinter.subprocess, "run", fake_run)

    assert repair_windows_tkinter.main() == 0
    assert calls[0][:5] == [
        "C:/Windows/winget.exe",
        "install",
        "--id",
        "Python.Python.3.12",
        "--exact",
    ]
    assert "--force" in calls[0]
    assert any("Include_tcltk=1" in argument for argument in calls[0])
    assert calls[1] == [
        "C:/Windows/py.exe",
        "-3.12",
        "-c",
        "import tkinter; print(tkinter.TkVersion)",
    ]


def test_tkinter_repair_reports_missing_winget(monkeypatch, capsys) -> None:
    monkeypatch.setattr(repair_windows_tkinter.shutil, "which", lambda _name: None)

    assert repair_windows_tkinter.main() == 1
    assert "winget" in capsys.readouterr().err


def test_tkinter_repair_finds_launcher_installed_outside_current_path(
    tmp_path: Path, monkeypatch
) -> None:
    local_app_data = tmp_path / "LocalAppData"
    launcher = local_app_data / "Programs" / "Python" / "Launcher" / "py.exe"
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(b"launcher")
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "C:/Windows/winget.exe" if name == "winget" else None

    def fake_run(command: list[str], *, check: bool):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.delenv("WINDIR", raising=False)
    monkeypatch.setattr(repair_windows_tkinter.shutil, "which", fake_which)
    monkeypatch.setattr(repair_windows_tkinter.subprocess, "run", fake_run)

    assert repair_windows_tkinter.main() == 0
    assert calls[-1][0] == str(launcher)
