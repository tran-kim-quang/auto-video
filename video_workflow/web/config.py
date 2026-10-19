from __future__ import annotations

import argparse
import shutil
import socket
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..job_models import GlobalSettings
from ..json_store import JsonStore
from .voices import DEFAULT_MAX_REFERENCE_SECONDS


@dataclass(frozen=True, slots=True)
class WebLayout:
    root: Path

    @property
    def uploads(self) -> Path:
        return Path(self.root) / "uploads"

    @property
    def work(self) -> Path:
        return Path(self.root) / "work"

    @property
    def tts_cache(self) -> Path:
        return Path(self.root) / "tts-cache"

    @property
    def state(self) -> Path:
        return Path(self.root) / "state"

    @property
    def logs(self) -> Path:
        return Path(self.root) / "logs"

    def ensure(self) -> WebLayout:
        for path in (self.uploads, self.work, self.tts_cache, self.state, self.logs):
            path.mkdir(parents=True, exist_ok=True)
        return self


def default_root() -> Path:
    return Path.home() / "video_and_cc"


def init_settings(
    layout: WebLayout,
    *,
    logo: Path | None,
    outro: Path | None,
    legacy_root: Path | None,
) -> GlobalSettings:
    store = JsonStore(layout.state)
    settings_path = layout.state / "settings.json"
    if settings_path.is_file():
        settings = store.load_settings()
    elif legacy_root is not None and (Path(legacy_root) / "settings.json").is_file():
        settings = JsonStore(Path(legacy_root)).load_settings()
    else:
        settings = GlobalSettings()
    if logo is not None:
        settings = replace(settings, logo=Path(logo))
    if outro is not None:
        settings = replace(settings, outro=Path(outro))
    if not settings_path.is_file() or logo is not None or outro is not None:
        store.save_settings(settings)
    return settings


def lan_urls(port: int) -> list[str]:
    addresses: set[str] = set()
    try:
        for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = row[4][0]
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    return [f"http://{address}:{port}" for address in sorted(addresses)]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Giao diện web tạo video bài giảng")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--logo", type=Path)
    parser.add_argument("--outro", type=Path)
    parser.add_argument("--max-active", type=int, default=3)
    parser.add_argument("--gpu-slots", type=int, default=1)
    parser.add_argument(
        "--max-reference-seconds",
        type=float,
        default=DEFAULT_MAX_REFERENCE_SECONDS,
    )
    return parser.parse_args(argv)


def build_status(
    settings: GlobalSettings,
    voicestudio_ok: Callable[[], bool],
    port: int,
    *,
    max_reference_seconds: float = DEFAULT_MAX_REFERENCE_SECONDS,
) -> dict[str, Any]:
    ffmpeg = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
    slide_export = True if sys.platform == "win32" else bool(
        shutil.which("soffice") and shutil.which("pdftoppm")
    )
    voice_ok = bool(voicestudio_ok())
    logo_ok = settings.logo is not None and Path(settings.logo).is_file()
    outro_ok = settings.outro is not None and Path(settings.outro).is_file()
    return {
        "voicestudio": voice_ok,
        "ffmpeg": ffmpeg,
        "slide_export": slide_export,
        "logo": Path(settings.logo).name if settings.logo is not None else None,
        "outro": Path(settings.outro).name if settings.outro is not None else None,
        "ready": bool(logo_ok and outro_ok and ffmpeg and voice_ok),
        "lan_urls": lan_urls(port),
        "max_reference_seconds": float(max_reference_seconds),
    }
