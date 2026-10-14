from __future__ import annotations

import os
from pathlib import Path

import pytest

from video_workflow.voicestudio_client import (
    VoiceProfile,
    VoiceStudioConfig,
    VoiceStudioError,
)
from video_workflow.voicestudio_supervisor import (
    VoiceStudioStartupError,
    VoiceStudioSupervisor,
)


class _Client:
    def __init__(self, results) -> None:
        self.results = list(results)
        self.calls = 0

    def list_profiles(self):
        self.calls += 1
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return tuple(result)


class _Process:
    def __init__(self, polls=None) -> None:
        self.polls = list(polls or [None])
        self.terminated = 0
        self.killed = 0
        self.waited = 0

    def poll(self):
        return self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]

    def terminate(self):
        self.terminated += 1

    def kill(self):
        self.killed += 1

    def wait(self, timeout=None):
        self.waited += 1
        return 0


def _profile() -> VoiceProfile:
    return VoiceProfile("599fc048", "Teacher_male", "clone")


def _backend(tmp_path: Path) -> Path:
    project = tmp_path / "runtime" / "project"
    backend = project / "backend" / "main.py"
    backend.parent.mkdir(parents=True)
    backend.write_text("# backend", encoding="utf-8")
    python = project / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    python.chmod(0o755)
    return backend


def test_returns_existing_ready_api_without_starting_or_owning_process(
    tmp_path: Path,
) -> None:
    client = _Client([[_profile()]])
    starts = []
    supervisor = VoiceStudioSupervisor(
        VoiceStudioConfig(),
        backend_override=None,
        log_directory=tmp_path,
        client_factory=lambda _url: client,
        popen_factory=lambda *args, **kwargs: starts.append((args, kwargs)),
    )

    assert supervisor.ensure_ready() is client
    supervisor.close()
    assert starts == []


def test_starts_cuda_backend_waits_then_validates_exact_profile(
    tmp_path: Path,
) -> None:
    backend = _backend(tmp_path)
    client = _Client(
        [VoiceStudioError("connection refused"), VoiceStudioError("warming"), [_profile()]]
    )
    process = _Process()
    launches = []

    def popen(command, **kwargs):
        launches.append((command, kwargs))
        return process

    supervisor = VoiceStudioSupervisor(
        VoiceStudioConfig(),
        backend_override=backend,
        log_directory=tmp_path / "logs",
        startup_timeout_seconds=5,
        client_factory=lambda _url: client,
        popen_factory=popen,
        sleep=lambda _seconds: None,
        monotonic=iter([0.0, 0.1, 0.2, 0.3]).__next__,
    )

    assert supervisor.ensure_ready() is client
    command, kwargs = launches[0]
    assert command == [str(backend.parents[1] / ".venv/bin/python"), str(backend)]
    assert kwargs["cwd"] == backend.parents[1]
    assert kwargs["env"]["OMNIVOICE_DEVICE"] == "cuda"
    assert kwargs["env"]["TORCH_COMPILE_DISABLE"] == "1"
    assert kwargs["env"]["PYTORCH_CUDA_ALLOC_CONF"] == "expandable_segments:True"
    assert kwargs["env"]["PATH"] == os.environ["PATH"]


def test_missing_profile_fails_without_searching_by_display_name(tmp_path: Path) -> None:
    client = _Client([[VoiceProfile("different", "Teacher_male", "clone")]])
    starts = []
    supervisor = VoiceStudioSupervisor(
        VoiceStudioConfig(),
        backend_override=None,
        log_directory=tmp_path,
        client_factory=lambda _url: client,
        popen_factory=lambda *args, **kwargs: starts.append((args, kwargs)),
    )

    with pytest.raises(VoiceStudioStartupError, match="599fc048.*not found"):
        supervisor.ensure_ready()
    assert starts == []


def test_close_terminates_only_the_process_started_by_this_supervisor(
    tmp_path: Path,
) -> None:
    external_client = _Client([[_profile()]])
    external = VoiceStudioSupervisor(
        VoiceStudioConfig(),
        backend_override=None,
        log_directory=tmp_path,
        client_factory=lambda _url: external_client,
    )
    external.ensure_ready()
    external.close()

    process = _Process()
    managed_client = _Client([VoiceStudioError("down"), [_profile()]])
    managed = VoiceStudioSupervisor(
        VoiceStudioConfig(),
        backend_override=_backend(tmp_path),
        log_directory=tmp_path,
        client_factory=lambda _url: managed_client,
        popen_factory=lambda *args, **kwargs: process,
        sleep=lambda _seconds: None,
        monotonic=iter([0.0, 0.1, 0.2]).__next__,
    )
    managed.ensure_ready()
    managed.close()

    assert process.terminated == 1
    assert process.killed == 0


def test_early_process_exit_reports_backend_log_path(tmp_path: Path) -> None:
    process = _Process([7])
    supervisor = VoiceStudioSupervisor(
        VoiceStudioConfig(),
        backend_override=_backend(tmp_path),
        log_directory=tmp_path / "logs",
        client_factory=lambda _url: _Client([VoiceStudioError("down")]),
        popen_factory=lambda *args, **kwargs: process,
        monotonic=iter([0.0, 0.1]).__next__,
    )

    with pytest.raises(VoiceStudioStartupError) as caught:
        supervisor.ensure_ready()
    assert "exited with code 7" in str(caught.value)
    assert str(tmp_path / "logs") in str(caught.value)


def test_startup_timeout_terminates_owned_process_and_reports_timeout(
    tmp_path: Path,
) -> None:
    process = _Process()
    times = iter([0.0, 0.0, 2.0])
    supervisor = VoiceStudioSupervisor(
        VoiceStudioConfig(),
        backend_override=_backend(tmp_path),
        log_directory=tmp_path / "logs",
        startup_timeout_seconds=1,
        client_factory=lambda _url: _Client([VoiceStudioError("down")]),
        popen_factory=lambda *args, **kwargs: process,
        sleep=lambda _seconds: None,
        monotonic=times.__next__,
    )

    with pytest.raises(VoiceStudioStartupError, match="did not become ready within 1 seconds"):
        supervisor.ensure_ready()
    assert process.terminated == 1


def test_missing_install_lists_checked_locations_and_override(tmp_path: Path) -> None:
    missing_override = tmp_path / "missing-backend.py"
    candidates = (tmp_path / "missing-app", tmp_path / "also-missing")
    supervisor = VoiceStudioSupervisor(
        VoiceStudioConfig(),
        backend_override=missing_override,
        log_directory=tmp_path,
        install_candidates=candidates,
        client_factory=lambda _url: _Client([VoiceStudioError("down")]),
    )

    with pytest.raises(VoiceStudioStartupError) as caught:
        supervisor.ensure_ready()
    message = str(caught.value)
    assert str(missing_override) in message
    assert all(str(path) in message for path in candidates)
