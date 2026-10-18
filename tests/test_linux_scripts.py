from __future__ import annotations

import os
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("name", ["setup.sh", "run-ui.sh"])
def test_linux_script_exists_is_executable_and_has_valid_syntax(name: str) -> None:
    script = ROOT / name
    assert script.is_file()
    assert script.stat().st_mode & stat.S_IXUSR
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_setup_check_only_never_calls_sudo(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sentinel = tmp_path / "sudo-called"
    sudo = fake_bin / "sudo"
    sudo.write_text(f"#!/bin/sh\ntouch '{sentinel}'\nexit 99\n", encoding="utf-8")
    sudo.chmod(0o755)
    env = os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}"}
    subprocess.run(
        ["bash", str(ROOT / "setup.sh"), "--check-only"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert not sentinel.exists()


def test_run_ui_without_venv_has_clear_error(tmp_path: Path) -> None:
    script = tmp_path / "run-ui.sh"
    script.write_bytes((ROOT / "run-ui.sh").read_bytes())
    script.chmod(0o755)
    result = subprocess.run([str(script)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "Run ./setup.sh first" in result.stderr


def test_pyproject_exposes_desktop_and_web_workflow_entrypoints() -> None:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert payload["project"]["scripts"] == {
        "video-workflow-ui": "video_workflow.ui:main",
        "video-workflow-web": "video_workflow.web.__main__:main",
    }


def test_linux_setup_checks_voicestudio_without_installing_it(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    backend = fake_home / ".config/VoiceStudio/runtime/project/backend/main.py"
    backend.parent.mkdir(parents=True)
    backend.write_text("# installed", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sentinel = tmp_path / "installer-called"
    for name in ("sudo", "apt-get", "curl", "wget"):
        command = fake_bin / name
        command.write_text(
            f"#!/bin/sh\ntouch '{sentinel}'\nexit 99\n", encoding="utf-8"
        )
        command.chmod(0o755)
    env = os.environ | {
        "HOME": str(fake_home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        ["bash", str(ROOT / "setup.sh"), "--check-only"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert "VoiceStudio" in result.stdout
    assert str(backend) in result.stdout
    assert not sentinel.exists()
