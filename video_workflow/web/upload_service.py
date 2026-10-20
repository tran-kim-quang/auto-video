from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ..job_models import JobStatus, utc_now
from .config import WebLayout
from .r2_upload import (
    UploadOutcome,
    UploadProgress,
    redact_r2_values,
    run_r2_upload,
)
from .state import UploadRunRecord, WebJob, WebStore
from .upload_export import ExportItem, find_output_files, new_export_dir
from .upload_identity import UploadCheck, check_upload_identities


class UploadBusyError(RuntimeError):
    pass


class UploadNotReadyError(ValueError):
    pass


class UploadService:
    def __init__(
        self,
        store: WebStore,
        layout: WebLayout,
        repo: Callable[[], Path | None],
        jobs: Callable[[], Iterable[WebJob]],
        check=check_upload_identities,
        runner=run_r2_upload,
    ) -> None:
        self.store = store
        self.layout = layout
        self._repo = repo
        self._jobs = jobs
        self._check = check
        self._runner = runner
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._process = None
        self._stopping = False
        self._runs = self.store.load_uploads()
        interrupted = False
        recovered: list[UploadRunRecord] = []
        for run in self._runs:
            if run.status == "running":
                interrupted = True
                run = replace(
                    run,
                    status="interrupted",
                    finished_at=utc_now(),
                )
            recovered.append(run)
        self._runs = recovered
        if interrupted:
            self.store.save_uploads(self._runs)

    def checks(self, leaf_relpaths: Iterable[str]) -> dict[str, UploadCheck]:
        relpaths = list(leaf_relpaths)
        repo = self._repo()
        if repo is None:
            issue = (
                "Không kiểm tra được với pyramid-upload-to-r2: "
                "Chưa cấu hình repo upload"
            )
            return {
                relpath: UploadCheck(relpath, None, issue, None)
                for relpath in relpaths
            }
        return self._check(Path(repo), relpaths)

    def candidates(
        self, batch_id: str
    ) -> tuple[list[ExportItem], dict[str, UploadCheck]]:
        jobs = [job for job in self._jobs() if job.batch_id == batch_id]
        relpaths = [job.leaf_relpath for job in jobs]
        checks = self.checks(relpaths)
        items: list[ExportItem] = []
        selected: set[str] = set()
        for job in jobs:
            if job.status is not JobStatus.COMPLETED:
                continue
            if job.leaf_relpath in selected:
                continue
            check = checks.get(job.leaf_relpath)
            if check is None or check.issue is not None or check.identity is None:
                continue
            if job.vtt_path is None:
                files = find_output_files(
                    self.layout.uploads / batch_id / job.leaf_relpath
                )
            else:
                video = Path(job.video_path) if job.video_path is not None else None
                vtt = Path(job.vtt_path)
                files = (
                    (video, vtt)
                    if video is not None and video.is_file() and vtt.is_file()
                    else None
                )
            if files is None:
                continue
            selected.add(job.leaf_relpath)
            video, vtt = files
            items.append(
                ExportItem(
                    relpath=job.leaf_relpath,
                    triple=check.identity.triple,
                    video=video,
                    vtt=vtt,
                )
            )
        if not items:
            raise UploadNotReadyError("Batch chưa có bài nào upload được")
        return items, checks

    def _save(self) -> None:
        self.store.save_uploads(self._runs)

    def _set_record(self, record: UploadRunRecord) -> None:
        with self._lock:
            self._runs = [record if run.id == record.id else run for run in self._runs]
            self._save()

    def _record(self, run_id: str) -> UploadRunRecord:
        return next(run for run in self._runs if run.id == run_id)

    def _progress(self, run_id: str, progress: UploadProgress) -> None:
        with self._lock:
            current = self._record(run_id)
            updates = {
                "phase": progress.phase,
                "current": progress.current,
                "total": progress.total,
                "errors": list(progress.errors),
            }
            for name in ("video_total", "video_ok", "cc_total", "cc_ok"):
                value = getattr(progress, name, None)
                if value is not None:
                    updates[name] = value
            for name in ("video_manifest", "cc_manifest"):
                value = getattr(progress, name, None)
                if value is not None:
                    updates[name] = str(value)
            self._set_record(
                replace(current, **updates)
            )

    def _process_changed(self, process) -> None:
        with self._lock:
            self._process = process

    def _run(
        self,
        record: UploadRunRecord,
        repo: Path,
        export_dir: Path,
        items: list[ExportItem],
        log_path: Path,
    ) -> None:
        try:
            outcome: UploadOutcome = self._runner(
                repo,
                export_dir,
                items,
                dry_run=record.dry_run,
                log_path=log_path,
                on_progress=lambda progress: self._progress(record.id, progress),
                on_process=self._process_changed,
            )
            with self._lock:
                interrupted = self._stopping
            finished = replace(
                self._record(record.id),
                status="interrupted" if interrupted else outcome.status,
                finished_at=utc_now(),
                phase="done",
                current=outcome.cc_ok,
                total=outcome.cc_total,
                video_total=outcome.video_total,
                video_ok=outcome.video_ok,
                cc_total=outcome.cc_total,
                cc_ok=outcome.cc_ok,
                video_manifest=(
                    str(outcome.video_manifest)
                    if outcome.video_manifest is not None
                    else None
                ),
                cc_manifest=(
                    str(outcome.cc_manifest)
                    if outcome.cc_manifest is not None
                    else None
                ),
                log_path=str(outcome.log_path),
                errors=list(outcome.errors),
            )
        except Exception as exc:
            current = self._record(record.id)
            finished = replace(
                current,
                status=(
                    "interrupted"
                    if self._stopping
                    else "partial" if current.video_ok else "failed"
                ),
                finished_at=utc_now(),
                phase="done",
                errors=[*current.errors, redact_r2_values(str(exc))],
                log_path=str(log_path),
            )
        with self._lock:
            self._set_record(finished)
            self._process = None

    def start(self, batch_id: str, *, dry_run: bool) -> UploadRunRecord:
        with self._lock:
            if (
                any(run.status == "running" for run in self._runs)
                or self._worker is not None
                and self._worker.is_alive()
            ):
                raise UploadBusyError("Đang có một lượt upload khác chạy")
            repo = self._repo()
            if repo is None:
                raise UploadNotReadyError("Chưa cấu hình repo upload")
            items, _checks = self.candidates(batch_id)
            now = datetime.now()
            timestamp = now.strftime("%Y%m%d-%H%M%S")
            export_dir = new_export_dir(
                self.layout.root / "exports", batch_id, now=now
            )
            log_path = self.layout.logs / f"upload-{batch_id}-{timestamp}.log"
            record = UploadRunRecord(
                id=uuid4().hex,
                batch_id=batch_id,
                dry_run=dry_run,
                status="running",
                started_at=utc_now(),
                finished_at=None,
                phase="video",
                current=0,
                total=len(items),
                video_total=len(items),
                video_ok=0,
                cc_total=0,
                cc_ok=0,
                video_manifest=None,
                cc_manifest=None,
                log_path=str(log_path),
                errors=[],
            )
            self._runs.append(record)
            self._save()
            self._stopping = False
            thread = threading.Thread(
                target=self._run,
                args=(record, Path(repo), export_dir, items, log_path),
                daemon=True,
                name=f"r2-upload-{record.id[:8]}",
            )
            self._worker = thread
            thread.start()
            return record

    def stop(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        with self._lock:
            self._stopping = True
            process = self._process
            worker = self._worker
        if process is not None:
            try:
                process.terminate()
            except (AttributeError, OSError):
                pass
        if worker is not None:
            worker.join(max(0.0, deadline - time.monotonic()))
        if worker is not None and worker.is_alive():
            with self._lock:
                process = self._process
            if process is not None:
                try:
                    process.kill()
                except (AttributeError, OSError):
                    pass
            worker.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            if worker is None or not worker.is_alive():
                self._worker = None
                for index, run in enumerate(self._runs):
                    if run.status == "running":
                        self._runs[index] = replace(
                            run,
                            status="interrupted",
                            finished_at=utc_now(),
                        )
                        self._save()

    def runs(self, batch_id: str | None = None) -> list[UploadRunRecord]:
        with self._lock:
            selected = (
                self._runs
                if batch_id is None
                else [run for run in self._runs if run.batch_id == batch_id]
            )
            return sorted(selected, key=lambda run: run.started_at, reverse=True)
