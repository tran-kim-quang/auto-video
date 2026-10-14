from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .job_models import GlobalSettings, JobRecord


class JsonStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()
        self._warnings: list[str] = []

    @property
    def warnings(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._warnings)

    def _atomic_write(self, path: Path, payload: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.root, prefix=f".{path.name}.", suffix=".tmp", delete=False
            ) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def _read(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("top-level JSON value must be an object")
            return value
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._preserve_invalid(path, f"Cannot read {path.name}: {exc}")
            return None

    def _preserve_invalid(self, path: Path, message: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = path.with_name(f"{path.name}.corrupt-{stamp}")
        try:
            path.replace(backup)
        except OSError as backup_error:
            self._warnings.append(f"{message}; backup failed: {backup_error}")
        else:
            self._warnings.append(f"{message}; preserved as {backup.name}")

    def load_settings(self) -> GlobalSettings:
        with self._lock:
            payload = self._read(self.root / "settings.json")
            if payload is None:
                return GlobalSettings()
            try:
                if payload.get("schema_version") == 1:
                    settings = GlobalSettings(
                        logo=Path(payload["logo"]) if payload.get("logo") else None,
                        outro=Path(payload["outro"]) if payload.get("outro") else None,
                    )
                    self.save_settings(settings)
                    return settings
                return GlobalSettings.from_dict(payload)
            except (KeyError, TypeError, ValueError) as exc:
                self._preserve_invalid(self.root / "settings.json", f"Invalid settings.json: {exc}")
                return GlobalSettings()

    def save_settings(self, settings: GlobalSettings) -> None:
        with self._lock:
            self._atomic_write(self.root / "settings.json", settings.to_dict())

    def load_jobs(self) -> list[JobRecord]:
        with self._lock:
            payload = self._read(self.root / "jobs.json")
            if payload is None:
                return []
            try:
                if payload.get("schema_version") == 1:
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                    archive = self.root / f"jobs.legacy-v1-{stamp}.json"
                    self._atomic_write(
                        archive,
                        {
                            "migration_reason": (
                                "legacy source-media jobs are not narration jobs"
                            ),
                            "legacy_payload": payload,
                        },
                    )
                    self.save_jobs([])
                    self._warnings.append(
                        f"Archived legacy source-media jobs as {archive.name}"
                    )
                    return []
                if payload.get("schema_version") != 2 or not isinstance(payload.get("jobs"), list):
                    raise ValueError("unsupported jobs schema")
                return [JobRecord.from_dict(item) for item in payload["jobs"]]
            except (KeyError, TypeError, ValueError) as exc:
                self._preserve_invalid(self.root / "jobs.json", f"Invalid jobs.json: {exc}")
                return []

    def save_jobs(self, jobs: list[JobRecord] | tuple[JobRecord, ...]) -> None:
        with self._lock:
            self._atomic_write(
                self.root / "jobs.json",
                {"schema_version": 2, "jobs": [job.to_dict() for job in jobs]},
            )
