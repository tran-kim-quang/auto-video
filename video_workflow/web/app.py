from __future__ import annotations

import uuid
import zipfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..job_models import GlobalSettings, JobStatus, utc_now
from ..leaf_scan import LeafFolder, scan_leaf_folders
from .config import WebLayout
from .estimate import remaining_seconds, seconds_per_char
from .scheduler import DuplicateJobError, JobScheduler, JobStateError
from .state import BatchRecord, WebStore
from .uploads import UploadPathError, UploadTooLarge, store_batch
from .voices import VoiceInUseError, VoiceInputError, VoiceService


@dataclass
class WebServices:
    layout: WebLayout
    store: WebStore
    scheduler: JobScheduler
    voices: VoiceService
    settings: Callable[[], GlobalSettings]
    status: Callable[[], dict[str, Any]]


class _ZipStreamWriter:
    def __init__(self) -> None:
        self._chunks = bytearray()
        self._position = 0

    def write(self, data: bytes) -> int:
        self._chunks.extend(data)
        self._position += len(data)
        return len(data)

    def tell(self) -> int:
        return self._position

    def flush(self) -> None:
        return None

    def drain(self) -> bytes:
        chunk = bytes(self._chunks)
        self._chunks.clear()
        return chunk


def _zip_chunks(entries: Sequence[tuple[Path, str]]) -> Iterator[bytes]:
    writer = _ZipStreamWriter()
    with zipfile.ZipFile(
        writer,
        "w",
        compression=zipfile.ZIP_STORED,
        allowZip64=True,
    ) as archive:
        for video_path, archive_name in entries:
            with archive.open(archive_name, "w", force_zip64=True) as target:
                pending = writer.drain()
                if pending:
                    yield pending
                with video_path.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                        pending = writer.drain()
                        if pending:
                            yield pending
            pending = writer.drain()
            if pending:
                yield pending
    pending = writer.drain()
    if pending:
        yield pending


def _value(value: Any) -> Any:
    return asdict(value) if is_dataclass(value) else value


def _leaf_view(leaf: LeafFolder) -> dict[str, Any]:
    return {
        "relpath": leaf.relpath,
        "valid": leaf.valid,
        "error": leaf.error,
        "slide_count": leaf.slide_count,
        "script_chars": leaf.script_chars,
        "has_output": leaf.has_output,
        "pptx": leaf.pptx.name if leaf.pptx is not None else None,
        "script": leaf.script.name if leaf.script is not None else None,
    }


def create_app(services: WebServices) -> FastAPI:
    app = FastAPI(title="Pyramid Video Web")
    app.state.services = services
    static_dir = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    def batch_record(batch_id: str) -> BatchRecord:
        for batch in services.store.load_batches():
            if batch.id == batch_id:
                return batch
        raise HTTPException(404, "Không tìm thấy batch")

    def batch_view(batch: BatchRecord) -> dict[str, Any]:
        leaves = scan_leaf_folders(services.layout.uploads / batch.id)
        return {
            "id": batch.id,
            "root_name": batch.root_name,
            "created_at": batch.created_at,
            "leaves": [_leaf_view(leaf) for leaf in leaves],
            "estimate_seconds_per_char": seconds_per_char(services.scheduler.jobs()),
        }

    @app.get("/")
    def index():
        return FileResponse(static_dir / "index.html")

    @app.get("/api/status")
    def status():
        return services.status()

    @app.post("/api/batches")
    def upload_batch(
        files: list[UploadFile] = File(...),
        paths: list[str] = Form(...),
    ):
        if len(files) != len(paths):
            raise HTTPException(400, "Số file và đường dẫn không khớp")
        try:
            stored = store_batch(
                services.layout.uploads,
                ((path, upload.file) for path, upload in zip(paths, files)),
            )
        except (UploadPathError, UploadTooLarge) as exc:
            raise HTTPException(400, str(exc)) from exc
        batch = BatchRecord(stored.id, stored.root_name, utc_now())
        services.store.add_batch(batch)
        return batch_view(batch)

    @app.get("/api/batches")
    def batches():
        return [asdict(batch) for batch in reversed(services.store.load_batches())]

    @app.get("/api/batches/{batch_id}")
    def get_batch(batch_id: str):
        return batch_view(batch_record(batch_id))

    @app.post("/api/batches/{batch_id}/jobs")
    def create_jobs(batch_id: str, payload: dict[str, Any] = Body(...)):
        batch = batch_record(batch_id)
        valid = {
            leaf.relpath: leaf
            for leaf in scan_leaf_folders(services.layout.uploads / batch.id)
            if leaf.valid
        }
        requested = [str(item) for item in payload.get("leaves", [])]
        if any(relpath not in valid for relpath in requested):
            raise HTTPException(400, "Danh sách bài có folder không hợp lệ")
        try:
            jobs = services.scheduler.enqueue(
                batch_id,
                [(relpath, valid[relpath].script_chars) for relpath in requested],
                str(payload.get("profile_id", "")),
            )
        except DuplicateJobError as exc:
            raise HTTPException(409, str(exc)) from exc
        return [job.to_dict() for job in jobs]

    @app.get("/api/jobs")
    def jobs():
        all_jobs = services.scheduler.jobs()
        rate = seconds_per_char(all_jobs)
        return [
            {**job.to_dict(), "remaining_seconds": remaining_seconds(job, rate)}
            for job in all_jobs
        ]

    def mutate_job(job_id: str, method: str):
        try:
            job = getattr(services.scheduler, method)(job_id)
        except JobStateError as exc:
            raise HTTPException(409, str(exc)) from exc
        return job.to_dict()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        return mutate_job(job_id, "cancel")

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: str):
        return mutate_job(job_id, "retry")

    def find_job(job_id: str):
        for job in services.scheduler.jobs():
            if job.id == job_id:
                return job
        raise HTTPException(404, "Không tìm thấy job")

    @app.get("/api/jobs/{job_id}/video")
    def video(job_id: str, download: int = Query(0)):
        job = find_job(job_id)
        if not job.video_path or not Path(job.video_path).is_file():
            raise HTTPException(404, "Video chưa sẵn sàng")
        path = Path(job.video_path)
        return FileResponse(
            path,
            media_type="video/mp4",
            filename=path.name if download else None,
            content_disposition_type="attachment",
        )

    @app.get("/api/jobs/{job_id}/subtitles")
    def subtitles(job_id: str):
        job = find_job(job_id)
        if not job.video_path:
            raise HTTPException(404, "Phụ đề chưa sẵn sàng")
        path = Path(job.video_path).with_name("vi.vtt")
        if not path.is_file():
            raise HTTPException(404, "Phụ đề chưa sẵn sàng")
        return FileResponse(path, media_type="text/vtt")

    @app.get("/api/batches/{batch_id}/videos.zip")
    def batch_zip(batch_id: str):
        batch = batch_record(batch_id)
        completed = [
            job
            for job in services.scheduler.jobs()
            if job.batch_id == batch_id
            and job.status is JobStatus.COMPLETED
            and job.video_path
            and Path(job.video_path).is_file()
        ]
        if not completed:
            raise HTTPException(404, "Chưa có video hoàn thành")
        entries = [
            (
                Path(job.video_path),
                f"{job.leaf_relpath}/{Path(job.video_path).name}",
            )
            for job in completed
        ]
        headers = {
            "Content-Disposition": f'attachment; filename="{batch.root_name}_{batch.id}.zip"'
        }
        return StreamingResponse(
            _zip_chunks(entries),
            media_type="application/zip",
            headers=headers,
        )

    @app.get("/api/voices")
    def voices():
        return [_value(voice) for voice in services.voices.list_voices()]

    @app.get("/api/voices/{profile_id}/sample")
    def voice_sample(profile_id: str):
        audio, content_type = services.voices.sample_audio(profile_id)
        return Response(audio, media_type=content_type)

    @app.post("/api/voices")
    async def create_voice(
        name: str = Form(...),
        audio: UploadFile = File(...),
        ref_text: str = Form(""),
    ):
        try:
            voice = services.voices.create(
                name=name,
                audio=await audio.read(),
                filename=audio.filename or "audio.wav",
                ref_text=ref_text,
            )
        except VoiceInputError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _value(voice)

    @app.post("/api/voices/{profile_id}/preview")
    def preview_voice(profile_id: str, payload: dict[str, Any] = Body(...)):
        directory = services.layout.work / "previews"
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{uuid.uuid4().hex}.wav"
        services.voices.preview(profile_id, str(payload.get("text", "")), destination)
        return FileResponse(destination, media_type="audio/wav")

    @app.post("/api/voices/{profile_id}/default")
    def default_voice(profile_id: str):
        try:
            services.voices.set_default(profile_id)
        except VoiceInputError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    @app.delete("/api/voices/{profile_id}")
    def delete_voice(profile_id: str):
        try:
            services.voices.delete(profile_id)
        except VoiceInUseError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    return app
