from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from http.client import IncompleteRead
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class VoiceStudioError(RuntimeError):
    """Raised when VoiceStudio cannot satisfy a request safely."""


class VoiceStudioOutOfMemory(VoiceStudioError):
    """Raised when VoiceStudio reports CUDA/GPU memory exhaustion."""


@dataclass(frozen=True, slots=True)
class VoiceStudioConfig:
    api_url: str = "http://127.0.0.1:3900"
    profile_id: str = "599fc048"
    engine: str = "omnivoice"
    language: str = "Vietnamese"
    steps: int = 32
    seed: int = 42
    max_chunk_chars: int = 150
    postprocess: bool = True
    crossfade_ms: int = 50
    config_version: str = "tts-v1"

    def __post_init__(self) -> None:
        if not self.api_url.strip() or not self.profile_id.strip():
            raise ValueError("VoiceStudio API URL and profile ID are required")
        if self.steps <= 0 or self.max_chunk_chars <= 0:
            raise ValueError("steps and max_chunk_chars must be positive")
        if not 0 <= self.crossfade_ms <= 1000:
            raise ValueError("crossfade_ms must be between 0 and 1000")


@dataclass(frozen=True, slots=True)
class VoiceProfile:
    id: str
    name: str
    kind: str | None = None


@dataclass(frozen=True, slots=True)
class GenerationMetadata:
    audio_id: str | None
    generation_seconds: float | None
    audio_path: str | None
    seed: int | None
    audio_duration_seconds: float | None
    routing_headers: dict[str, str]


def _multipart(fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = f"----auto-video-{uuid.uuid4().hex}"
    pieces: list[bytes] = []
    for name, value in fields.items():
        pieces.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    pieces.append(f"--{boundary}--\r\n".encode())
    return b"".join(pieces), f"multipart/form-data; boundary={boundary}"


def _optional_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _optional_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


class VoiceStudioClient:
    _MAX_JSON_BYTES = 1024 * 1024

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 30.0,
        generation_timeout_seconds: float = 600.0,
        max_error_bytes: int = 8192,
    ) -> None:
        if (
            timeout_seconds <= 0
            or generation_timeout_seconds <= 0
            or max_error_bytes <= 0
        ):
            raise ValueError("timeouts and response limits must be positive")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.generation_timeout_seconds = generation_timeout_seconds
        self.max_error_bytes = max_error_bytes

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _bounded_read(self, response, limit: int, label: str) -> bytes:
        payload = response.read(limit + 1)
        if len(payload) > limit:
            raise VoiceStudioError(f"VoiceStudio {label} response is too large")
        return payload

    def _json_request(
        self, path: str, *, method: str = "GET"
    ) -> Any:
        request = Request(self._url(path), method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = self._bounded_read(response, self._MAX_JSON_BYTES, "JSON")
        except HTTPError as exc:
            detail = exc.read(self.max_error_bytes).decode("utf-8", errors="replace")
            raise VoiceStudioError(
                f"VoiceStudio {method} {path} failed with HTTP {exc.code}: {detail}"
            ) from exc
        except (OSError, URLError) as exc:
            raise VoiceStudioError(f"VoiceStudio {method} {path} failed: {exc}") from exc
        try:
            return json.loads(payload)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise VoiceStudioError(f"VoiceStudio {path} returned invalid JSON") from exc

    def list_profiles(self) -> tuple[VoiceProfile, ...]:
        payload = self._json_request("/profiles")
        if not isinstance(payload, list):
            raise VoiceStudioError("VoiceStudio /profiles did not return a list")
        profiles: list[VoiceProfile] = []
        for item in payload:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise VoiceStudioError("VoiceStudio /profiles returned an invalid profile")
            profiles.append(
                VoiceProfile(
                    id=item["id"],
                    name=str(item.get("name", "")),
                    kind=str(item["kind"]) if item.get("kind") is not None else None,
                )
            )
        return tuple(profiles)

    def require_profile(self, profile_id: str) -> VoiceProfile:
        for profile in self.list_profiles():
            if profile.id == profile_id:
                return profile
        raise VoiceStudioError(f"VoiceStudio profile ID {profile_id} was not found")

    def flush_memory(self) -> dict[str, Any]:
        payload = self._json_request(
            f"/system/flush-memory?{urlencode({'unload_model': 'false'})}",
            method="POST",
        )
        if not isinstance(payload, dict):
            raise VoiceStudioError("VoiceStudio flush-memory returned invalid JSON")
        return payload

    def _raise_generate_http_error(self, exc: HTTPError) -> None:
        detail_bytes = exc.read(self.max_error_bytes)
        detail = detail_bytes.decode("utf-8", errors="replace")
        message = f"VoiceStudio generation failed with HTTP {exc.code}: {detail}"
        lowered = detail.casefold()
        if "out of memory" in lowered or "cuda oom" in lowered:
            raise VoiceStudioOutOfMemory(message) from exc
        raise VoiceStudioError(message) from exc

    def generate(
        self,
        text: str,
        destination: Path,
        config: VoiceStudioConfig,
    ) -> GenerationMetadata:
        if not text.strip():
            raise VoiceStudioError("cannot synthesize empty narration")
        fields = {
            "text": text,
            "profile_id": config.profile_id,
            "engine": config.engine,
            "language": config.language,
            "num_step": str(config.steps),
            "seed": str(config.seed),
            "max_chunk_chars": str(config.max_chunk_chars),
            "postprocess_output": str(config.postprocess).lower(),
            "crossfade_ms": str(config.crossfade_ms),
        }
        body, content_type = _multipart(fields)
        request = Request(
            self._url("/generate"),
            data=body,
            method="POST",
            headers={"Content-Type": content_type, "Content-Length": str(len(body))},
        )
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        response = None
        try:
            response = urlopen(request, timeout=self.generation_timeout_seconds)
            response_type = response.headers.get_content_type().casefold()
            if response_type not in {"audio/wav", "audio/x-wav", "audio/wave"}:
                raise VoiceStudioError(
                    f"VoiceStudio generation did not return WAV audio ({response_type})"
                )
            expected_length = _optional_int(response.headers.get("Content-Length"))
            received_length = 0
            with destination.open("wb") as handle:
                while True:
                    try:
                        chunk = response.read(64 * 1024)
                    except IncompleteRead as exc:
                        if exc.partial:
                            handle.write(exc.partial)
                        raise VoiceStudioError(
                            "VoiceStudio audio download was interrupted"
                        ) from exc
                    if not chunk:
                        break
                    handle.write(chunk)
                    received_length += len(chunk)
            if expected_length is not None and received_length != expected_length:
                raise VoiceStudioError("VoiceStudio audio download was interrupted")
            header = destination.read_bytes()[:12]
            if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                destination.unlink(missing_ok=True)
                raise VoiceStudioError("VoiceStudio generation returned an invalid WAV")
            headers = response.headers
            routing_headers = {
                name: value
                for name, value in headers.items()
                if name.casefold().startswith("x-omnivoice-")
            }
            return GenerationMetadata(
                audio_id=headers.get("X-Audio-Id"),
                generation_seconds=_optional_float(headers.get("X-Gen-Time")),
                audio_path=headers.get("X-Audio-Path"),
                seed=_optional_int(headers.get("X-Seed")),
                audio_duration_seconds=_optional_float(headers.get("X-Audio-Duration")),
                routing_headers=routing_headers,
            )
        except HTTPError as exc:
            self._raise_generate_http_error(exc)
            raise AssertionError("unreachable")
        except VoiceStudioError:
            if destination.exists() and response is not None:
                response_type = response.headers.get_content_type().casefold()
                if response_type not in {"audio/wav", "audio/x-wav", "audio/wave"}:
                    destination.unlink(missing_ok=True)
            raise
        except (OSError, URLError) as exc:
            raise VoiceStudioError(f"VoiceStudio generation request failed: {exc}") from exc
        finally:
            if response is not None:
                response.close()
