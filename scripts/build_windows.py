from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def pyinstaller_command(repo_root: Path, python: Path) -> list[str]:
    repo_root = Path(repo_root).resolve()
    return [
        str(Path(python)),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        "PyramidVideoWorkflow",
        "--paths",
        str(repo_root),
        "--collect-submodules",
        "win32com",
        "--collect-submodules",
        "win32comext",
        "--hidden-import",
        "pythoncom",
        "--hidden-import",
        "win32timezone",
        "--distpath",
        str(repo_root / "dist"),
        "--workpath",
        str(repo_root / "build" / "pyinstaller"),
        "--specpath",
        str(repo_root / "build"),
        str(repo_root / "windows_launcher.py"),
    ]


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    if sys.platform != "win32":
        print("Windows build must be run on Windows.", file=sys.stderr)
        return 1
    python = repo_root / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        print("Virtual environment not found. Run setup.bat first.", file=sys.stderr)
        return 1

    (repo_root / "build").mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [str(python), "-m", "pip", "install", "-e", f"{repo_root}[build]"],
            cwd=repo_root,
            check=True,
        )
        subprocess.run(pyinstaller_command(repo_root, python), cwd=repo_root, check=True)
    except subprocess.CalledProcessError as exc:
        return exc.returncode or 1

    executable = repo_root / "dist" / "PyramidVideoWorkflow" / "PyramidVideoWorkflow.exe"
    if not executable.is_file():
        print(f"Build did not create {executable}", file=sys.stderr)
        return 1
    print(f"Windows build ready: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
