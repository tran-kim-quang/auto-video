from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .voicestudio_client import (
    VoiceProfile,
    VoiceStudioClient,
    VoiceStudioConfig,
    VoiceStudioError,
)


class VoiceStudioStartupError(VoiceStudioError):
    """Raised when the configured local VoiceStudio service cannot become ready."""


class VoiceStudioSupervisor:
    def __init__(
        self,
        config: VoiceStudioConfig,
        *,
        backend_override: Path | None,
        log_directory: Path,
        startup_timeout_seconds: float = 120.0,
        install_candidates: Sequence[Path] | None = None,
        client_factory: Callable[[str], VoiceStudioClient] = VoiceStudioClient,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if startup_timeout_seconds <= 0:
            raise ValueError("startup timeout must be positive")
        self.config = config
        self.backend_override = (
            Path(backend_override) if backend_override is not None else None
        )
        self.log_directory = Path(log_directory)
        self.startup_timeout_seconds = startup_timeout_seconds
        self._install_candidates = (
            tuple(Path(path) for path in install_candidates)
            if install_candidates is not None
            else self._default_candidates()
        )
        self._client_factory = client_factory
        self._popen_factory = popen_factory
        self._sleep = sleep
        self._monotonic = monotonic
        self._owned_process: Any | None = None
        self._log_handle: Any | None = None
        self._log_path: Path | None = None

    @staticmethod
    def _default_candidates() -> tuple[Path, ...]:
        home = Path.home()
        if sys.platform == "win32":
            appdata = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
            program_files = Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
            return (
                appdata / "VoiceStudio/runtime/project/backend/main.py",
                program_files / "VoiceStudio/VoiceStudio.exe",
            )
        return (
            home / ".config/VoiceStudio/runtime/project/backend/main.py",
            Path("/opt/VoiceStudio/voicestudio-electron"),
        )

    def _profiles(self, client: VoiceStudioClient) -> tuple[VoiceProfile, ...]:
        return tuple(client.list_profiles())

    def _validate_profile(
        self, profiles: Sequence[VoiceProfile], *, ready_api: bool
    ) -> None:
        if any(profile.id == self.config.profile_id for profile in profiles):
            return
        context = "responsive VoiceStudio API" if ready_api else "started VoiceStudio API"
        raise VoiceStudioStartupError(
            f"configured VoiceStudio profile ID {self.config.profile_id} was not found in {context}"
        )

    def _candidate_paths(self) -> tuple[Path, ...]:
        paths: list[Path] = []
        if self.backend_override is not None:
            paths.append(self.backend_override)
        paths.extend(self._install_candidates)
        result: list[Path] = []
        for path in paths:
            if path not in result:
                result.append(path)
        return tuple(result)

    @staticmethod
    def _resolve_candidate(candidate: Path) -> Path | None:
        if candidate.is_file():
            return candidate
        if candidate.is_dir():
            backend = candidate / "backend/main.py"
            if backend.is_file():
                return backend
            for executable in ("voicestudio-electron", "VoiceStudio.exe"):
                app = candidate / executable
                if app.is_file():
                    return app
        return None

    def _launch_command(self, target: Path) -> tuple[list[str], Path]:
        if target.suffix.casefold() == ".py":
            project = target.parents[1]
            if sys.platform == "win32":
                runtime_python = project / ".venv/Scripts/python.exe"
            else:
                runtime_python = project / ".venv/bin/python"
            python = runtime_python if runtime_python.is_file() else Path(sys.executable)
            return [str(python), str(target)], project
        return [str(target)], target.parent

    def _start(self) -> None:
        checked = self._candidate_paths()
        target = next(
            (resolved for path in checked if (resolved := self._resolve_candidate(path))),
            None,
        )
        if target is None:
            rendered = ", ".join(str(path) for path in checked)
            raise VoiceStudioStartupError(
                f"VoiceStudio installation was not found; checked: {rendered}"
            )

        self.log_directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._log_path = self.log_directory / f"voicestudio-{stamp}.log"
        self._log_handle = self._log_path.open("a", encoding="utf-8")
        command, cwd = self._launch_command(target)
        environment = os.environ.copy()
        environment.update(
            {
                "OMNIVOICE_DEVICE": "cuda",
                "TORCH_COMPILE_DISABLE": "1",
                "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
            }
        )
        try:
            self._owned_process = self._popen_factory(
                command,
                cwd=cwd,
                env=environment,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                shell=False,
            )
        except OSError as exc:
            self._close_log()
            raise VoiceStudioStartupError(
                f"cannot start VoiceStudio; log: {self._log_path}: {exc}"
            ) from exc

    def ensure_ready(self) -> VoiceStudioClient:
        client = self._client_factory(self.config.api_url)
        try:
            profiles = self._profiles(client)
        except VoiceStudioError:
            profiles = None
        if profiles is not None:
            self._validate_profile(profiles, ready_api=True)
            return client

        self._start()
        started_at = self._monotonic()
        while True:
            return_code = self._owned_process.poll()
            if return_code is not None:
                log_path = self._log_path
                self._owned_process = None
                self._close_log()
                raise VoiceStudioStartupError(
                    f"VoiceStudio exited with code {return_code} before readiness; log: {log_path}"
                )
            try:
                profiles = self._profiles(client)
            except VoiceStudioError:
                profiles = None
            if profiles is not None:
                self._validate_profile(profiles, ready_api=False)
                return client
            if self._monotonic() - started_at >= self.startup_timeout_seconds:
                log_path = self._log_path
                self.close()
                raise VoiceStudioStartupError(
                    "VoiceStudio did not become ready within "
                    f"{self.startup_timeout_seconds:g} seconds; log: {log_path}"
                )
            self._sleep(0.5)

    def _close_log(self) -> None:
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

    def close(self) -> None:
        process = self._owned_process
        self._owned_process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self._close_log()
