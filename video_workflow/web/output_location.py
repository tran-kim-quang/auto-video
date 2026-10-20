from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path


class OutputRootError(ValueError):
    pass


INTERNAL_DIRS = ("uploads", "work", "tts-cache", "state", "logs", "exports")
PARKED_DIR = "_chua-co-tren-production"


def resolve_output_root(raw: str, workspace_root: Path) -> Path:
    value = raw.strip()
    if not value:
        raise OutputRootError("Cần nhập thư mục lưu kết quả")

    path = Path(value).expanduser()
    if not path.is_absolute():
        raise OutputRootError("Thư mục lưu kết quả phải là đường dẫn tuyệt đối")

    path = path.resolve()
    workspace = workspace_root.resolve()
    if any(path.is_relative_to(workspace / name) for name in INTERNAL_DIRS):
        raise OutputRootError("Không được lưu vào thư mục nội bộ của tool")

    probe: Path | None = None
    try:
        path.mkdir(parents=True, exist_ok=True)
        descriptor, probe_name = tempfile.mkstemp(prefix=".write-test-", dir=path)
        os.close(descriptor)
        probe = Path(probe_name)
        probe.unlink()
    except OSError as exc:
        if probe is not None:
            probe.unlink(missing_ok=True)
        raise OutputRootError(f"Không ghi được vào thư mục: {path}") from exc

    return path


def output_relative_parts(leaf_relpath: str) -> tuple[str, ...]:
    parts = Path(leaf_relpath).parts
    return tuple(parts[-3:] if len(parts) >= 3 else parts)


def output_paths(output_root: Path, leaf_relpath: str) -> tuple[Path, Path]:
    parts = output_relative_parts(leaf_relpath)
    video_path = output_root.joinpath(*parts[:-1], f"{parts[-1]}.mp4")
    vtt_path = output_root.joinpath(*parts, "vi.vtt")
    return video_path, vtt_path


def parked_paths(output_root: Path, leaf_relpath: str) -> tuple[Path, Path]:
    return output_paths(Path(output_root) / PARKED_DIR, leaf_relpath)


def is_parked(output_root: Path, video_path: Path) -> bool:
    parked_root = (Path(output_root) / PARKED_DIR).resolve()
    return Path(video_path).resolve().is_relative_to(parked_root)


def move_output_files(
    video_src: Path,
    vtt_src: Path,
    video_dest: Path,
    vtt_dest: Path,
) -> None:
    video_dest.parent.mkdir(parents=True, exist_ok=True)
    vtt_dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(video_src, video_dest)
    os.replace(vtt_src, vtt_dest)


def publish_output_files(
    video_src: Path,
    vtt_text: str,
    video_dest: Path,
    vtt_dest: Path,
) -> None:
    video_dest.parent.mkdir(parents=True, exist_ok=True)
    vtt_dest.parent.mkdir(parents=True, exist_ok=True)

    video_tmp = video_dest.with_name(f"{video_dest.name}.tmp-{uuid.uuid4().hex}")
    try:
        shutil.copyfile(video_src, video_tmp)
        os.replace(video_tmp, video_dest)
    finally:
        video_tmp.unlink(missing_ok=True)

    vtt_tmp = vtt_dest.with_name(f"{vtt_dest.name}.tmp-{uuid.uuid4().hex}")
    try:
        with vtt_tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(vtt_text)
        os.replace(vtt_tmp, vtt_dest)
    finally:
        vtt_tmp.unlink(missing_ok=True)
