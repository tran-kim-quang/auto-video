from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .narration_script import SlideScriptSegment
from .probe import MediaInfo, MediaProbeError, probe_media
from .voicestudio_client import (
    GenerationMetadata,
    VoiceStudioClient,
    VoiceStudioConfig,
    VoiceStudioError,
    VoiceStudioOutOfMemory,
)


class NarrationError(RuntimeError):
    """Raised when narration artifacts cannot be built or validated."""


class NarrationCancelled(NarrationError):
    """Raised after a safe cancellation boundary between slide requests."""


@dataclass(frozen=True, slots=True)
class AudioArtifact:
    slide_id: int
    text: str
    text_sha256: str
    path: Path
    start_ms: int
    end_ms: int
    duration_ms: int
    size: int
    sample_rate: int
    channels: int
    content_sha256: str
    generation_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class NarrationResult:
    slides: tuple[AudioArtifact, ...]
    timeline_path: Path
    combined_audio_path: Path
    manifest_path: Path
    total_duration_ms: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
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


def _load_manifest(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return None
    if not isinstance(payload.get("slides"), list):
        return None
    return payload


def _config_identity(config: VoiceStudioConfig) -> dict[str, Any]:
    return asdict(config)


def _validate_wav(path: Path) -> MediaInfo:
    try:
        with path.open("rb") as handle:
            header = handle.read(12)
        if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise NarrationError(f"invalid WAV header: {path}")
        info = probe_media(path)
    except (OSError, MediaProbeError) as exc:
        raise NarrationError(f"invalid WAV {path}: {exc}") from exc
    if (
        not info.has_audio
        or info.duration_ms <= 0
        or info.sample_rate is None
        or info.sample_rate <= 0
        or info.channels is None
        or info.channels <= 0
    ):
        raise NarrationError(f"WAV has incomplete audio metadata: {path}")
    return info


def _entry_matches(
    entry: dict[str, Any], segment: SlideScriptSegment, path: Path
) -> MediaInfo | None:
    if (
        entry.get("slide_id") != segment.slide_id
        or entry.get("text") != segment.text
        or entry.get("text_sha256") != segment.text_sha256
        or not path.is_file()
    ):
        return None
    try:
        info = _validate_wav(path)
        stat = path.stat()
        if (
            entry.get("size") != stat.st_size
            or entry.get("duration_ms") != info.duration_ms
            or entry.get("sample_rate") != info.sample_rate
            or entry.get("channels") != info.channels
            or entry.get("content_sha256") != _sha256(path)
        ):
            return None
        return info
    except (OSError, NarrationError):
        return None


def _manifest_entry(
    segment: SlideScriptSegment,
    path: Path,
    info: MediaInfo,
    generation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "slide_id": segment.slide_id,
        "text": segment.text,
        "text_sha256": segment.text_sha256,
        "wav": f"audio/slide_{segment.slide_id:03d}.wav",
        "size": path.stat().st_size,
        "sample_rate": info.sample_rate,
        "channels": info.channels,
        "duration_ms": info.duration_ms,
        "content_sha256": _sha256(path),
        "generation": generation,
    }


def _generate_slide(
    client: VoiceStudioClient,
    segment: SlideScriptSegment,
    partial: Path,
    config: VoiceStudioConfig,
) -> GenerationMetadata:
    partial.unlink(missing_ok=True)
    try:
        return client.generate(segment.text, partial, config)
    except VoiceStudioOutOfMemory:
        client.flush_memory()
        partial.unlink(missing_ok=True)
        try:
            return client.generate(segment.text, partial, config)
        except VoiceStudioOutOfMemory as exc:
            partial.unlink(missing_ok=True)
            raise NarrationError(
                f"slide {segment.slide_id} failed after GPU memory flush and one retry"
            ) from exc
    except VoiceStudioError as exc:
        partial.unlink(missing_ok=True)
        raise NarrationError(f"slide {segment.slide_id} synthesis failed: {exc}") from exc


def _materialize(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.partial")
    try:
        shutil.copyfile(source, partial)
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


def _concat_quote(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "'\\''")


def _combine_audio(
    sources: Sequence[Path],
    destination: Path,
    *,
    log_path: Path | None,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise NarrationError("ffmpeg is not available on PATH")
    concat = destination.parent / ".audio-concat.txt"
    partial = destination.parent / ".combined.partial.wav"
    concat.write_text(
        "".join(f"file '{_concat_quote(path)}'\n" for path in sources),
        encoding="utf-8",
    )
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat),
        "-vn",
        "-c:a",
        "pcm_s16le",
        str(partial),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, check=False)
        if completed.returncode:
            detail = completed.stderr.decode("utf-8", errors="replace")
            if log_path is not None:
                Path(log_path).parent.mkdir(parents=True, exist_ok=True)
                with Path(log_path).open("a", encoding="utf-8") as handle:
                    handle.write(f"\n[combine narration]\n{detail}\n")
            raise NarrationError(f"cannot combine narration WAV files: {detail.strip()}")
        os.replace(partial, destination)
    except OSError as exc:
        raise NarrationError(f"cannot combine narration WAV files: {exc}") from exc
    finally:
        concat.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)


def build_narration(
    segments: Sequence[SlideScriptSegment],
    client: VoiceStudioClient,
    artifact_directory: Path,
    cache_directory: Path,
    config: VoiceStudioConfig,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
    log_path: Path | None = None,
) -> NarrationResult:
    if not segments:
        raise NarrationError("cannot build narration without slide segments")
    artifact_directory = Path(artifact_directory)
    cache_directory = Path(cache_directory)
    artifact_audio = artifact_directory / "audio"
    cache_audio = cache_directory / "audio"
    artifact_audio.mkdir(parents=True, exist_ok=True)
    cache_audio.mkdir(parents=True, exist_ok=True)
    cache_manifest_path = cache_directory / "audio-manifest.json"
    identity = _config_identity(config)
    prior_manifest = _load_manifest(cache_manifest_path)
    prior_entries: dict[int, dict[str, Any]] = {}
    if prior_manifest is not None and prior_manifest.get("config") == identity:
        prior_entries = {
            int(entry["slide_id"]): entry
            for entry in prior_manifest["slides"]
            if isinstance(entry, dict) and isinstance(entry.get("slide_id"), int)
        }

    completed_entries: list[dict[str, Any]] = []
    cache_paths: list[Path] = []
    total = len(segments)
    for index, segment in enumerate(segments, start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise NarrationCancelled("narration generation cancelled")
        cached = cache_audio / f"slide_{segment.slide_id:03d}.wav"
        entry = prior_entries.get(segment.slide_id)
        info = _entry_matches(entry, segment, cached) if entry is not None else None
        if info is None:
            partial = cache_audio / f"slide_{segment.slide_id:03d}.partial"
            metadata = _generate_slide(client, segment, partial, config)
            try:
                info = _validate_wav(partial)
                os.replace(partial, cached)
            except (OSError, NarrationError) as exc:
                partial.unlink(missing_ok=True)
                raise NarrationError(
                    f"cannot publish narration for slide {segment.slide_id}: {exc}"
                ) from exc
            entry = _manifest_entry(segment, cached, info, asdict(metadata))
        else:
            entry = _manifest_entry(
                segment,
                cached,
                info,
                entry.get("generation", {}) if entry is not None else {},
            )
        completed_entries.append(entry)
        cache_paths.append(cached)
        try:
            _atomic_json(
                cache_manifest_path,
                {"schema_version": 1, "config": identity, "slides": completed_entries},
            )
        except OSError as exc:
            raise NarrationError(f"cannot write narration cache manifest: {exc}") from exc
        if on_progress is not None:
            on_progress(index, total)
        if cancel_event is not None and cancel_event.is_set():
            raise NarrationCancelled("narration generation cancelled")

    staged_paths: list[Path] = []
    for segment, cached in zip(segments, cache_paths, strict=True):
        staged = artifact_audio / f"slide_{segment.slide_id:03d}.wav"
        try:
            _materialize(cached, staged)
        except OSError as exc:
            raise NarrationError(f"cannot stage narration WAV {staged}: {exc}") from exc
        staged_paths.append(staged)

    timeline: list[dict[str, int]] = []
    artifacts: list[AudioArtifact] = []
    cursor = 0
    for segment, staged, entry in zip(
        segments, staged_paths, completed_entries, strict=True
    ):
        duration = int(entry["duration_ms"])
        end = cursor + duration
        timeline.append(
            {
                "slide_id": segment.slide_id,
                "start_ms": cursor,
                "end_ms": end,
                "duration_ms": duration,
            }
        )
        artifacts.append(
            AudioArtifact(
                slide_id=segment.slide_id,
                text=segment.text,
                text_sha256=segment.text_sha256,
                path=staged,
                start_ms=cursor,
                end_ms=end,
                duration_ms=duration,
                size=int(entry["size"]),
                sample_rate=int(entry["sample_rate"]),
                channels=int(entry["channels"]),
                content_sha256=str(entry["content_sha256"]),
                generation_metadata=dict(entry.get("generation", {})),
            )
        )
        cursor = end

    timeline_path = artifact_directory / "timeline.json"
    manifest_path = artifact_directory / "audio-manifest.json"
    try:
        _atomic_json(timeline_path, timeline)
        _atomic_json(
            manifest_path,
            {"schema_version": 1, "config": identity, "slides": completed_entries},
        )
    except OSError as exc:
        raise NarrationError(f"cannot write narration artifacts: {exc}") from exc

    combined = artifact_directory / "combined.wav"
    _combine_audio(staged_paths, combined, log_path=log_path)
    combined_info = _validate_wav(combined)
    if abs(combined_info.duration_ms - cursor) > 20 * len(segments):
        raise NarrationError(
            "combined narration duration differs from the measured slide WAV total"
        )
    return NarrationResult(
        slides=tuple(artifacts),
        timeline_path=timeline_path,
        combined_audio_path=combined,
        manifest_path=manifest_path,
        total_duration_ms=cursor,
    )
