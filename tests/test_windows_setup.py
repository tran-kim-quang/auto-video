from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
REPAIR_SCRIPT = ROOT / "scripts" / "repair_windows_tkinter.py"


def _executable(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_tkinter_repair_installs_tcltk_and_verifies_python_launcher(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    winget_log = tmp_path / "winget.log"
    py_log = tmp_path / "py.log"
    _executable(
        fake_bin / "winget",
        f"#!/bin/sh\nprintf '%s\\n' \"$@\" > '{winget_log}'\nexit 0\n",
    )
    _executable(
        fake_bin / "py",
        f"#!/bin/sh\nprintf '%s\\n' \"$@\" > '{py_log}'\nexit 0\n",
    )
    env = os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}"}

    result = subprocess.run(
        [sys.executable, str(REPAIR_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    winget_arguments = winget_log.read_text(encoding="utf-8").splitlines()
    assert winget_arguments[:4] == [
        "install",
        "--id",
        "Python.Python.3.12",
        "--exact",
    ]
    assert "--force" in winget_arguments
    assert any("Include_tcltk=1" in argument for argument in winget_arguments)
    assert py_log.read_text(encoding="utf-8").splitlines() == [
        "-3.12",
        "-c",
        "import tkinter; print(tkinter.TkVersion)",
    ]


def test_tkinter_repair_reports_missing_winget(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(REPAIR_SCRIPT)],
        env=os.environ | {"PATH": str(tmp_path)},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "winget" in result.stderr


def test_tkinter_repair_finds_launcher_installed_outside_current_path(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_app_data = tmp_path / "LocalAppData"
    launcher = local_app_data / "Programs" / "Python" / "Launcher" / "py.exe"
    _executable(
        fake_bin / "winget",
        "#!/bin/sh\n"
        f"mkdir -p '{launcher.parent}'\n"
        f"printf '#!/bin/sh\\nexit 0\\n' > '{launcher}'\n"
        f"chmod +x '{launcher}'\n"
        "exit 0\n",
    )
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "LOCALAPPDATA": str(local_app_data),
    }

    result = subprocess.run(
        [sys.executable, str(REPAIR_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
