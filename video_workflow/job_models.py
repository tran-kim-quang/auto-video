from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4


class JobStatus(StrEnum):
    WAITING = "waiting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class JobKind(StrEnum):
    SLIDE = "slide"
    MERGE = "merge"


class JobStage(StrEnum):
    VALIDATING = "validating"
    EXPORTING_SLIDES = "exporting_slides"
    RENDERING_LECTURE = "rendering_lecture"
    PREPARING_OUTRO = "preparing_outro"
    NORMALIZING_FIRST = "normalizing_first"
    NORMALIZING_SECOND = "normalizing_second"
    JOINING = "joining"
    VERIFYING = "verifying"


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class GlobalSettings:
    logo: Path | None = None
    outro: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "logo": str(self.logo) if self.logo is not None else None,
            "outro": str(self.outro) if self.outro is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GlobalSettings:
        if data.get("schema_version") != 1:
            raise ValueError("unsupported settings schema_version")
        logo = data.get("logo")
        outro = data.get("outro")
        return cls(Path(logo) if logo else None, Path(outro) if outro else None)


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: str
    created_at: str
    source_media: Path
    pptx: Path | None
    timeline: Path | None
    output_name: str
    output_directory: Path
    kind: JobKind = JobKind.SLIDE
    secondary_media: Path | None = None
    write_report: bool = True
    use_outro: bool = True
    status: JobStatus = JobStatus.WAITING
    stage: JobStage | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    @classmethod
    def new(
        cls,
        *,
        source_media: Path,
        pptx: Path,
        timeline: Path,
        output_name: str,
        output_directory: Path,
        write_report: bool = True,
        use_outro: bool = True,
        status: JobStatus = JobStatus.WAITING,
    ) -> JobRecord:
        return cls(
            id=str(uuid4()),
            created_at=_now(),
            source_media=Path(source_media),
            pptx=Path(pptx),
            timeline=Path(timeline),
            output_name=validate_output_name(output_name),
            output_directory=Path(output_directory),
            write_report=write_report,
            use_outro=use_outro,
            status=status,
        )

    @classmethod
    def new_merge(
        cls,
        *,
        first_video: Path,
        second_video: Path,
        output_name: str,
        output_directory: Path,
        status: JobStatus = JobStatus.WAITING,
    ) -> JobRecord:
        return cls(
            id=str(uuid4()),
            created_at=_now(),
            source_media=Path(first_video),
            pptx=None,
            timeline=None,
            output_name=validate_output_name(output_name),
            output_directory=Path(output_directory),
            kind=JobKind.MERGE,
            secondary_media=Path(second_video),
            status=status,
        )

    @property
    def output_path(self) -> Path:
        return self.output_directory / self.output_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "kind": self.kind.value,
            "source_media": str(self.source_media),
            "secondary_media": (
                str(self.secondary_media) if self.secondary_media is not None else None
            ),
            "pptx": str(self.pptx) if self.pptx is not None else None,
            "timeline": str(self.timeline) if self.timeline is not None else None,
            "output_name": self.output_name,
            "output_directory": str(self.output_directory),
            "write_report": self.write_report,
            "use_outro": self.use_outro,
            "status": self.status.value,
            "stage": self.stage.value if self.stage else None,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobRecord:
        output_name = validate_output_name(str(data["output_name"]))
        if "use_outro" in data:
            use_outro = bool(data["use_outro"])
        else:
            is_existing_batch_part1 = (
                data.get("write_report") is False
                and Path(output_name).stem.casefold().endswith("_1")
            )
            use_outro = not is_existing_batch_part1
        return cls(
            id=str(data["id"]),
            created_at=str(data["created_at"]),
            source_media=Path(data["source_media"]),
            pptx=Path(data["pptx"]) if data.get("pptx") else None,
            timeline=Path(data["timeline"]) if data.get("timeline") else None,
            output_name=output_name,
            output_directory=Path(data["output_directory"]),
            kind=JobKind(data.get("kind", JobKind.SLIDE)),
            secondary_media=(
                Path(data["secondary_media"]) if data.get("secondary_media") else None
            ),
            write_report=bool(data.get("write_report", True)),
            use_outro=use_outro,
            status=JobStatus(data["status"]),
            stage=JobStage(data["stage"]) if data.get("stage") else None,
            error=data.get("error"),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
        )


def utc_now() -> str:
    return _now()
