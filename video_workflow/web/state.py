from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..job_models import JobStatus, utc_now
from ..pipeline import _atomic_json


@dataclass(frozen=True, slots=True)
class WebJob:
    id: str
    batch_id: str
    leaf_relpath: str
    profile_id: str
    status: JobStatus
    stage: str | None
    progress_current: int
    progress_total: int
    error: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    video_path: str | None
    script_chars: int
    tts_seconds: float | None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WebJob:
        values = dict(data)
        values["status"] = JobStatus(values["status"])
        return cls(**values)

    @classmethod
    def new(
        cls,
        *,
        batch_id: str,
        leaf_relpath: str,
        profile_id: str,
        script_chars: int,
    ) -> WebJob:
        return cls(
            id=uuid4().hex,
            batch_id=batch_id,
            leaf_relpath=leaf_relpath,
            profile_id=profile_id,
            status=JobStatus.WAITING,
            stage=None,
            progress_current=0,
            progress_total=0,
            error=None,
            created_at=utc_now(),
            started_at=None,
            finished_at=None,
            video_path=None,
            script_chars=script_chars,
            tts_seconds=None,
        )


@dataclass(frozen=True, slots=True)
class BatchRecord:
    id: str
    root_name: str
    created_at: str


@dataclass(frozen=True, slots=True)
class UploadRunRecord:
    id: str
    batch_id: str
    dry_run: bool
    status: str
    started_at: str
    finished_at: str | None
    phase: str | None
    current: int
    total: int
    video_total: int
    video_ok: int
    cc_total: int
    cc_ok: int
    video_manifest: str | None
    cc_manifest: str | None
    log_path: str | None
    errors: list[str]


class WebStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self._lock = threading.RLock()

    def _preserve_invalid(self, path: Path) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        try:
            path.replace(path.with_name(f"{path.name}.corrupt-{stamp}"))
        except OSError:
            pass

    def _read(self, filename: str) -> dict[str, Any] | None:
        path = self.state_dir / filename
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema_version") != 1:
                raise ValueError("unsupported schema")
            return payload
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self._preserve_invalid(path)
            return None

    def _write(self, filename: str, payload: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        _atomic_json(self.state_dir / filename, payload)

    def load_jobs(self) -> list[WebJob]:
        with self._lock:
            payload = self._read("jobs.json")
            if payload is None:
                return []
            try:
                jobs = payload["jobs"]
                if not isinstance(jobs, list):
                    raise ValueError("jobs must be a list")
                return [WebJob.from_dict(item) for item in jobs]
            except (KeyError, TypeError, ValueError):
                self._preserve_invalid(self.state_dir / "jobs.json")
                return []

    def save_jobs(self, jobs: Sequence[WebJob]) -> None:
        with self._lock:
            self._write(
                "jobs.json",
                {"schema_version": 1, "jobs": [job.to_dict() for job in jobs]},
            )

    def load_batches(self) -> list[BatchRecord]:
        with self._lock:
            payload = self._read("batches.json")
            if payload is None:
                return []
            try:
                batches = payload["batches"]
                if not isinstance(batches, list):
                    raise ValueError("batches must be a list")
                return [BatchRecord(**item) for item in batches]
            except (KeyError, TypeError, ValueError):
                self._preserve_invalid(self.state_dir / "batches.json")
                return []

    def add_batch(self, batch: BatchRecord) -> None:
        with self._lock:
            batches = self.load_batches()
            batches.append(batch)
            self._write(
                "batches.json",
                {
                    "schema_version": 1,
                    "batches": [asdict(item) for item in batches],
                },
            )

    def load_uploads(self) -> list[UploadRunRecord]:
        with self._lock:
            payload = self._read("uploads.json")
            if payload is None:
                return []
            try:
                runs = payload["runs"]
                if not isinstance(runs, list):
                    raise ValueError("runs must be a list")
                return [UploadRunRecord(**item) for item in runs]
            except (KeyError, TypeError, ValueError):
                self._preserve_invalid(self.state_dir / "uploads.json")
                return []

    def save_uploads(self, runs: Sequence[UploadRunRecord]) -> None:
        with self._lock:
            self._write(
                "uploads.json",
                {"schema_version": 1, "runs": [asdict(run) for run in runs]},
            )

    def default_voice(self) -> str:
        with self._lock:
            payload = self._read("web.json")
            if payload is None:
                return "599fc048"
            value = payload.get("default_voice")
            return value if isinstance(value, str) and value else "599fc048"

    def set_default_voice(self, profile_id: str) -> None:
        with self._lock:
            self._write(
                "web.json",
                {"schema_version": 1, "default_voice": profile_id},
            )
