from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from .voicestudio_client import VoiceStudioConfig


class JobStatus(StrEnum):
    WAITING = "waiting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class JobStage(StrEnum):
    VALIDATING = "validating"
    STARTING_VOICESTUDIO = "starting_voicestudio"
    VALIDATING_PROFILE = "validating_profile"
    SYNTHESIZING_AUDIO = "synthesizing_audio"
    ASSEMBLING_NARRATION = "assembling_narration"
    EXPORTING_SLIDES = "exporting_slides"
    RENDERING_LECTURE = "rendering_lecture"
    PREPARING_OUTRO = "preparing_outro"
    JOINING = "joining"
    VERIFYING = "verifying"
    PUBLISHING = "publishing"


_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_INVALID_NAME_CHARS = set('<>:"/\\|?*')


def validate_output_name(name: str) -> str:
    if not isinstance(name, str) or not name or name != name.rstrip(" ."):
        raise ValueError("output name must not be empty or end with a dot or space")
    if name in {".", ".."} or any(
        char in _INVALID_NAME_CHARS or ord(char) < 32 for char in name
    ):
        raise ValueError("output name contains characters that Windows does not allow")
    result = name if name.lower().endswith(".mp4") else f"{name}.mp4"
    if Path(result).stem.upper() in _RESERVED_NAMES:
        raise ValueError("output name is reserved by Windows")
    return result


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class GlobalSettings:
    logo: Path | None = None
    outro: Path | None = None
    voicestudio_api_url: str = "http://127.0.0.1:3900"
    profile_id: str = "599fc048"
    backend_override: Path | None = None
    inference_config_version: str = "tts-v1"

    def voice_config(self) -> VoiceStudioConfig:
        return VoiceStudioConfig(
            api_url=self.voicestudio_api_url,
            profile_id=self.profile_id,
            config_version=self.inference_config_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "logo": str(self.logo) if self.logo is not None else None,
            "outro": str(self.outro) if self.outro is not None else None,
            "voicestudio_api_url": self.voicestudio_api_url,
            "profile_id": self.profile_id,
            "backend_override": (
                str(self.backend_override) if self.backend_override is not None else None
            ),
            "inference_config_version": self.inference_config_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GlobalSettings:
        if data.get("schema_version") != 2:
            raise ValueError("unsupported settings schema_version")
        return cls(
            logo=Path(data["logo"]) if data.get("logo") else None,
            outro=Path(data["outro"]) if data.get("outro") else None,
            voicestudio_api_url=str(
                data.get("voicestudio_api_url", "http://127.0.0.1:3900")
            ),
            profile_id=str(data.get("profile_id", "599fc048")),
            backend_override=(
                Path(data["backend_override"]) if data.get("backend_override") else None
            ),
            inference_config_version=str(
                data.get("inference_config_version", "tts-v1")
            ),
        )


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: str
    created_at: str
    pptx: Path
    script: Path
    artifact_directory: Path
    output_name: str
    logo: Path
    outro: Path
    voicestudio_api_url: str
    profile_id: str
    backend_override: Path | None
    inference_config_version: str
    input_signature: dict[str, Any]
    status: JobStatus = JobStatus.WAITING
    stage: JobStage | None = None
    progress_current: int = 0
    progress_total: int = 0
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    @classmethod
    def new(
        cls,
        *,
        pptx: Path,
        script: Path,
        artifact_directory: Path,
        output_name: str,
        logo: Path,
        outro: Path,
        voicestudio_api_url: str,
        profile_id: str,
        backend_override: Path | None,
        inference_config_version: str,
        input_signature: dict[str, Any],
        status: JobStatus = JobStatus.WAITING,
    ) -> JobRecord:
        return cls(
            id=str(uuid4()),
            created_at=utc_now(),
            pptx=Path(pptx),
            script=Path(script),
            artifact_directory=Path(artifact_directory),
            output_name=validate_output_name(output_name),
            logo=Path(logo),
            outro=Path(outro),
            voicestudio_api_url=voicestudio_api_url,
            profile_id=profile_id,
            backend_override=(
                Path(backend_override) if backend_override is not None else None
            ),
            inference_config_version=inference_config_version,
            input_signature=input_signature,
            status=status,
        )

    @property
    def output_path(self) -> Path:
        return self.artifact_directory / self.output_name

    def voice_config(self) -> VoiceStudioConfig:
        return VoiceStudioConfig(
            api_url=self.voicestudio_api_url,
            profile_id=self.profile_id,
            config_version=self.inference_config_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "pptx": str(self.pptx),
            "script": str(self.script),
            "artifact_directory": str(self.artifact_directory),
            "output_name": self.output_name,
            "logo": str(self.logo),
            "outro": str(self.outro),
            "voicestudio_api_url": self.voicestudio_api_url,
            "profile_id": self.profile_id,
            "backend_override": (
                str(self.backend_override) if self.backend_override is not None else None
            ),
            "inference_config_version": self.inference_config_version,
            "input_signature": self.input_signature,
            "status": self.status.value,
            "stage": self.stage.value if self.stage is not None else None,
            "progress_current": self.progress_current,
            "progress_total": self.progress_total,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobRecord:
        return cls(
            id=str(data["id"]),
            created_at=str(data["created_at"]),
            pptx=Path(data["pptx"]),
            script=Path(data["script"]),
            artifact_directory=Path(data["artifact_directory"]),
            output_name=validate_output_name(str(data["output_name"])),
            logo=Path(data["logo"]),
            outro=Path(data["outro"]),
            voicestudio_api_url=str(data["voicestudio_api_url"]),
            profile_id=str(data["profile_id"]),
            backend_override=(
                Path(data["backend_override"]) if data.get("backend_override") else None
            ),
            inference_config_version=str(data["inference_config_version"]),
            input_signature=dict(data["input_signature"]),
            status=JobStatus(data["status"]),
            stage=JobStage(data["stage"]) if data.get("stage") else None,
            progress_current=int(data.get("progress_current", 0)),
            progress_total=int(data.get("progress_total", 0)),
            error=str(data["error"]) if data.get("error") is not None else None,
            started_at=(
                str(data["started_at"]) if data.get("started_at") is not None else None
            ),
            finished_at=(
                str(data["finished_at"])
                if data.get("finished_at") is not None
                else None
            ),
        )
