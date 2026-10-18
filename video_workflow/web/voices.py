from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from ..job_models import JobStatus
from ..voicestudio_client import VoiceStudioClient, VoiceStudioConfig
from .gpu import GatedVoiceStudioClient, GpuArbiter
from .state import WebJob, WebStore


class VoiceInUseError(ValueError):
    pass


class VoiceInputError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VoiceView:
    id: str
    name: str
    kind: str | None
    is_default: bool


ALLOWED_AUDIO = {".wav", ".mp3", ".m4a"}
MAX_AUDIO_BYTES = 20 * 1024 * 1024
DEFAULT_PREVIEW_TEXT = "Cho tam giác ABC vuông tại A, tính độ dài cạnh BC."


class VoiceService:
    def __init__(
        self,
        client: Callable[[], VoiceStudioClient],
        arbiter: GpuArbiter,
        store: WebStore,
        config: Callable[[], VoiceStudioConfig],
        active_jobs: Callable[[], Iterable[WebJob]],
    ) -> None:
        self._client = client
        self._arbiter = arbiter
        self._store = store
        self._config = config
        self._active_jobs = active_jobs

    def list_voices(self) -> list[VoiceView]:
        default = self._store.default_voice()
        voices = [
            VoiceView(
                id=str(record["id"]),
                name=str(record.get("name", "")),
                kind=(
                    str(record["kind"])
                    if record.get("kind") is not None
                    else None
                ),
                is_default=str(record["id"]) == default,
            )
            for record in self._client().list_profile_records()
        ]
        voices.sort(key=lambda voice: (not voice.is_default, voice.name.casefold()))
        return voices

    def sample_audio(self, profile_id: str) -> tuple[bytes, str]:
        return self._client().fetch_profile_audio(profile_id)

    def create(
        self,
        *,
        name: str,
        audio: bytes,
        filename: str,
        ref_text: str,
    ) -> VoiceView:
        clean_name = name.strip()
        if not clean_name:
            raise VoiceInputError("Cần nhập tên giọng")
        if Path(filename).suffix.casefold() not in ALLOWED_AUDIO:
            raise VoiceInputError("Chỉ nhận file .wav, .mp3, .m4a")
        if len(audio) > MAX_AUDIO_BYTES:
            raise VoiceInputError("File ghi âm vượt quá 20 MB")

        client = self._client()
        clean_text = ref_text.strip()
        if clean_text:
            with self._arbiter.request(interactive=True):
                record = client.create_profile(
                    name=clean_name,
                    audio=audio,
                    filename=filename,
                    ref_text=clean_text,
                )
        else:
            with self._arbiter.job_turn(front=True):
                with self._arbiter.request(interactive=True):
                    record = client.create_profile(
                        name=clean_name,
                        audio=audio,
                        filename=filename,
                        ref_text="",
                    )
                    client.flush_memory()
        return VoiceView(
            id=str(record["id"]),
            name=str(record.get("name", clean_name)),
            kind=(str(record["kind"]) if record.get("kind") is not None else None),
            is_default=str(record["id"]) == self._store.default_voice(),
        )

    def preview(self, profile_id: str, text: str, destination: Path) -> Path:
        destination = Path(destination)
        GatedVoiceStudioClient(
            self._client(),
            self._arbiter,
            interactive=True,
        ).generate(
            text.strip() or DEFAULT_PREVIEW_TEXT,
            destination,
            replace(self._config(), profile_id=profile_id),
        )
        return destination

    def set_default(self, profile_id: str) -> None:
        if not any(voice.id == profile_id for voice in self.list_voices()):
            raise VoiceInputError("Không tìm thấy giọng")
        self._store.set_default_voice(profile_id)

    def delete(self, profile_id: str) -> None:
        if profile_id == self._store.default_voice():
            raise VoiceInUseError("Không xoá được giọng mặc định")
        in_use = sum(
            job.profile_id == profile_id
            and job.status in {JobStatus.WAITING, JobStatus.RUNNING}
            for job in self._active_jobs()
        )
        if in_use:
            raise VoiceInUseError(
                f"Giọng đang được dùng bởi {in_use} bài trong hàng đợi"
            )
        self._client().delete_profile(profile_id)
