from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _python_launcher() -> str | None:
    launcher = shutil.which("py")
    if launcher is not None:
        return launcher
    candidates: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            Path(local_app_data) / "Programs" / "Python" / "Launcher" / "py.exe"
        )
    windows_directory = os.environ.get("WINDIR")
    if windows_directory:
        candidates.append(Path(windows_directory) / "py.exe")
    return next((str(path) for path in candidates if path.is_file()), None)


def main() -> int:
    winget = shutil.which("winget")
    if winget is None:
        print(
            "winget is unavailable; install Python 3.12 with Tcl/Tk enabled.",
            file=sys.stderr,
        )
        return 1

    installer_options = (
        "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 "
        "Include_pip=1 Include_tcltk=1 Include_test=0"
    )
    install = subprocess.run(
        [
            winget,
            "install",
            "--id",
            "Python.Python.3.12",
            "--exact",
            "--force",
            "--accept-source-agreements",
            "--accept-package-agreements",
            "--override",
            installer_options,
        ],
        check=False,
    )
    if install.returncode != 0:
        print("winget could not install Python with Tcl/Tk.", file=sys.stderr)
        return install.returncode or 1

    launcher = _python_launcher()
    if launcher is None:
        print(
            "Python launcher was not found after installing Tcl/Tk.",
            file=sys.stderr,
        )
        return 1
    probe = subprocess.run(
        [
            launcher,
            "-3.12",
            "-c",
            "import tkinter; print(tkinter.TkVersion)",
        ],
        check=False,
    )
    if probe.returncode != 0:
        print("Tkinter is still unavailable after Python repair.", file=sys.stderr)
        return probe.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
