from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..job_models import JobStatus, utc_now
from .config import WebLayout
from .output_location import is_parked, move_output_files, output_paths, parked_paths
from .production_api import DEFAULT_BACKEND_API, ProductionApi, redact_token
from .production_sync import (
    ReadyTarget,
    SyncPlan,
    SyncTarget,
    import_ready_cc_manifest,
    import_ready_video_manifest,
    plan_production_sync,
    unconfirmed_targets,
    verify_changes,
    worker_observed,
)
from .r2_upload import UploadOutcome, UploadProgress, redact_r2_values, run_r2_upload
from .state import UploadRunRecord, WebJob, WebStore
from .upload_export import ExportItem, find_output_files, new_export_dir
from .upload_identity import UploadCheck, check_upload_identities


class UploadBusyError(RuntimeError):
    pass


class UploadNotReadyError(ValueError):
    pass


class AdminTokenRequiredError(ValueError):
    def __init__(self) -> None:
        super().__init__("Cần token admin để upload lên R2")


class OverwriteConfirmationRequired(RuntimeError):
    def __init__(self, overwrite: list[str]) -> None:
        super().__init__(f"{len(overwrite)} bài sẽ GHI ĐÈ video đang có trên web")
        self.overwrite = overwrite


def _default_backend_api() -> str:
    return DEFAULT_BACKEND_API


class UploadService:
    def __init__(
        self,
        store: WebStore,
        layout: WebLayout,
        repo: Callable[[], Path | None],
        jobs: Callable[[], Iterable[WebJob]],
        check=check_upload_identities,
        runner=run_r2_upload,
        production_api_factory: Callable[[str, str], ProductionApi] = ProductionApi,
        backend_api: Callable[[], str] = _default_backend_api,
        sleep=time.sleep,
        monotonic=time.monotonic,
        min_wait_seconds: float = 60,
        max_wait_seconds: float = 600,
        poll_seconds: float = 5,
        update_output_paths: Callable[[str, str, str], WebJob] | None = None,
    ) -> None:
        self.store = store
        self.layout = layout
        self._repo = repo
        self._jobs = jobs
        self._check = check
        self._runner = runner
        self._production_api_factory = production_api_factory
        self._backend_api = backend_api
        self._sleep = sleep
        self._monotonic = monotonic
        self._min_wait_seconds = min_wait_seconds
        self._max_wait_seconds = max_wait_seconds
        self._poll_seconds = poll_seconds
        self._update_output_paths_callback = update_output_paths
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
                run = replace(run, status="interrupted", finished_at=utc_now())
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
            if job.status is not JobStatus.COMPLETED or job.leaf_relpath in selected:
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

    def _update_record(self, run_id: str, **updates: Any) -> UploadRunRecord:
        with self._lock:
            updated = replace(self._record(run_id), **updates)
            self._set_record(updated)
            return updated

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
            self._set_record(replace(current, **updates))

    def _process_changed(self, process) -> None:
        with self._lock:
            self._process = process

    def _load_lessons(
        self, api: ProductionApi, course_ids: Iterable[int]
    ) -> dict[int, list[dict[str, Any]]]:
        return {
            course_id: api.list_lessons(course_id)
            for course_id in sorted(set(course_ids))
        }

    def _write_json(self, path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _job_for_item(self, batch_id: str, item: ExportItem) -> WebJob | None:
        jobs = [
            job
            for job in self._jobs()
            if job.batch_id == batch_id and job.leaf_relpath == item.relpath
        ]
        return next(
            (
                job
                for job in jobs
                if job.video_path is not None and Path(job.video_path) == item.video
            ),
            jobs[0] if jobs else None,
        )

    def _persist_output_paths(
        self, job: WebJob, video_path: Path, vtt_path: Path
    ) -> None:
        if self._update_output_paths_callback is not None:
            self._update_output_paths_callback(job.id, str(video_path), str(vtt_path))
            return
        jobs = self.store.load_jobs()
        for index, stored in enumerate(jobs):
            if stored.id == job.id:
                jobs[index] = replace(
                    stored, video_path=str(video_path), vtt_path=str(vtt_path)
                )
                self.store.save_jobs(jobs)
                return

    def _redacted_errors(
        self, current: list[str], exc: BaseException, token: str
    ) -> list[str]:
        errors = [*current, redact_token(redact_r2_values(str(exc)), token)]
        detail = getattr(exc, "detail", None)
        if detail is not None:
            try:
                rendered = json.dumps(detail, ensure_ascii=False)
            except (TypeError, ValueError):
                rendered = str(detail)
            rendered = redact_token(redact_r2_values(rendered), token)
            if rendered not in errors:
                errors.append(rendered)
        return errors

    def _finish_interrupted(self, run_id: str) -> None:
        self._update_record(
            run_id,
            status="interrupted",
            finished_at=utc_now(),
            phase="done",
        )

    def _run(
        self,
        record: UploadRunRecord,
        repo: Path,
        export_dir: Path,
        items: list[ExportItem],
        log_path: Path,
        api: ProductionApi | None,
        plan: SyncPlan | None,
        before: Mapping[int, list[dict[str, Any]]] | None,
        admin_token: str,
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
            if interrupted:
                self._finish_interrupted(record.id)
                return

            should_import = (
                not record.dry_run
                and api is not None
                and plan is not None
                and before is not None
                and outcome.video_manifest is not None
                and outcome.video_ok > 0
            )
            self._update_record(
                record.id,
                status="running" if should_import else outcome.status,
                finished_at=None if should_import else utc_now(),
                phase="import_video" if should_import else "done",
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
                errors=[
                    redact_token(redact_r2_values(value), admin_token)
                    for value in outcome.errors
                ],
            )
            if not should_import:
                return

            assert api is not None
            assert plan is not None
            assert before is not None
            assert outcome.video_manifest is not None

            backup_path = export_dir / "production-before.json"
            self._write_json(backup_path, before)
            source_video_manifest = json.loads(
                Path(outcome.video_manifest).read_text(encoding="utf-8")
            )
            import_video = import_ready_video_manifest(source_video_manifest, plan)
            import_manifest_path = export_dir / "import" / "bulk-upload-import.json"
            self._write_json(import_manifest_path, import_video)
            self._update_record(
                record.id,
                status="running",
                finished_at=None,
                phase="import_video",
                import_manifest=str(import_manifest_path),
                backup_path=str(backup_path),
            )
            import_job_id = api.import_video_manifest(import_manifest_path)
            self._update_record(
                record.id, phase="wait_worker", import_job_id=import_job_id
            )

            started_wait = self._monotonic()
            course_ids = before.keys()
            while True:
                with self._lock:
                    if self._stopping:
                        self._finish_interrupted(record.id)
                        return
                observed = self._load_lessons(api, course_ids)
                elapsed = self._monotonic() - started_wait
                if elapsed >= self._min_wait_seconds and worker_observed(
                    observed, plan, import_video
                ):
                    break
                if elapsed >= self._max_wait_seconds:
                    self._update_record(
                        record.id,
                        status="failed",
                        finished_at=utc_now(),
                        phase="done",
                        errors=[
                            *self._record(record.id).errors,
                            "Worker chưa xử lý xong manifest video sau 10 phút",
                        ],
                    )
                    return
                self._sleep(self._poll_seconds)

            with self._lock:
                if self._stopping:
                    self._finish_interrupted(record.id)
                    return
            if outcome.cc_manifest is None:
                raise RuntimeError("Không có manifest phụ đề sau khi upload R2")
            source_cc_manifest = json.loads(
                Path(outcome.cc_manifest).read_text(encoding="utf-8")
            )
            uploaded_triples = {
                str(value["relativePath"]).removesuffix(".mp4")
                for value in import_video.get("videos", [])
                if isinstance(value, dict) and "relativePath" in value
            }
            import_cc = import_ready_cc_manifest(
                source_cc_manifest, plan, uploaded_triples
            )
            cc_manifest_path = export_dir / "import" / "cc-upload-import.json"
            self._write_json(cc_manifest_path, import_cc)
            self._update_record(record.id, phase="import_cc")
            cc_result = api.import_cc_manifest(cc_manifest_path)

            self._update_record(record.id, phase="verify")
            after = self._load_lessons(api, course_ids)
            after_path = export_dir / "production-after.json"
            self._write_json(after_path, after)
            unconfirmed = unconfirmed_targets(before, after, plan, import_video)
            differences = verify_changes(before, after, plan, import_video)
            self._update_record(
                record.id,
                status="needs_review" if differences else outcome.status,
                finished_at=utc_now(),
                phase="done",
                cc_import_result=cc_result,
                unconfirmed=unconfirmed,
                verify_differences=differences,
            )
        except Exception as exc:
            current = self._record(record.id)
            with self._lock:
                interrupted = self._stopping
            self._update_record(
                record.id,
                status=(
                    "interrupted"
                    if interrupted
                    else "partial"
                    if current.video_ok and current.import_job_id is None
                    else "failed"
                ),
                finished_at=utc_now(),
                phase="done",
                errors=self._redacted_errors(current.errors, exc, admin_token),
                log_path=str(log_path),
            )
        finally:
            with self._lock:
                self._process = None

    def start(
        self,
        batch_id: str,
        *,
        dry_run: bool,
        admin_token: str | None = None,
        confirm_overwrite: bool = False,
    ) -> UploadRunRecord:
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
            items, checks = self.candidates(batch_id)
            token = admin_token or ""
            if not dry_run and not token:
                raise AdminTokenRequiredError()

            api: ProductionApi | None = None
            plan: SyncPlan | None = None
            before: dict[int, list[dict[str, Any]]] | None = None
            skipped: list[dict] = []
            blocked: list[dict] = []
            overwrite: list[str] = []
            warnings: list[str] = []
            selected_items = list(items)
            production_checked = bool(token)
            if token:
                api = self._production_api_factory(self._backend_api(), token)
                targets = [
                    SyncTarget(
                        relpath=item.relpath,
                        identity=checks[item.relpath].identity,
                        item=item,
                    )
                    for item in items
                    if checks[item.relpath].identity is not None
                ]
                before = self._load_lessons(
                    api, (target.identity.course_id for target in targets)
                )
                plan = plan_production_sync(targets, before)
                overwrite = [
                    ready.target.relpath for ready in plan.ready if ready.overwrite
                ]
                if not dry_run and overwrite and not confirm_overwrite:
                    raise OverwriteConfirmationRequired(overwrite)
                blocked = [
                    {"relpath": target.relpath, "reason": reason}
                    for target, reason in plan.blocked
                ]
                warnings = [
                    warning for ready in plan.ready for warning in ready.warnings
                ]

                ready_targets: list[ReadyTarget] = []
                for ready in plan.ready:
                    target = ready.target
                    item = target.item
                    job = self._job_for_item(batch_id, item)
                    if (
                        not dry_run
                        and job is not None
                        and job.output_root is not None
                        and is_parked(Path(job.output_root), item.video)
                    ):
                        destination = output_paths(
                            Path(job.output_root), job.leaf_relpath
                        )
                        move_output_files(item.video, item.vtt, *destination)
                        self._persist_output_paths(job, *destination)
                        item = replace(item, video=destination[0], vtt=destination[1])
                        target = replace(target, item=item)
                    ready_targets.append(replace(ready, target=target))

                for target in plan.not_on_production:
                    item = target.item
                    job = self._job_for_item(batch_id, item)
                    moved_to = str(item.video)
                    if not dry_run and job is not None:
                        if job.output_root is None:
                            moved_to = (
                                "Bài chưa có trên production "
                                "(không di chuyển vì job cũ)"
                            )
                        elif not is_parked(Path(job.output_root), item.video):
                            destination = parked_paths(
                                Path(job.output_root), job.leaf_relpath
                            )
                            move_output_files(item.video, item.vtt, *destination)
                            self._persist_output_paths(job, *destination)
                            moved_to = str(destination[0])
                    skipped.append(
                        {"relpath": target.relpath, "moved_to": moved_to}
                    )
                plan = replace(plan, ready=ready_targets)
                selected_items = [ready.target.item for ready in plan.ready]
            elif dry_run:
                warnings.append("Chưa tra production (không có token admin)")

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
                total=len(selected_items),
                video_total=len(selected_items),
                video_ok=0,
                cc_total=0,
                cc_ok=0,
                video_manifest=None,
                cc_manifest=None,
                log_path=str(log_path),
                errors=[],
                production_checked=production_checked,
                skipped_not_on_production=skipped,
                blocked=blocked,
                overwrite=overwrite,
                warnings=warnings,
            )
            self._runs.append(record)
            self._save()
            self._stopping = False
            if not selected_items:
                finished = replace(
                    record,
                    status="succeeded",
                    finished_at=utc_now(),
                    phase="done",
                )
                self._set_record(finished)
                return finished
            thread = threading.Thread(
                target=self._run,
                args=(
                    record,
                    Path(repo),
                    export_dir,
                    selected_items,
                    log_path,
                    api,
                    plan,
                    before,
                    token,
                ),
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
                            run, status="interrupted", finished_at=utc_now()
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
