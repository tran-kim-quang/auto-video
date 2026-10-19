from __future__ import annotations

import os
import re
import shutil
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import uuid4

from ..job_models import _RESERVED_NAMES


MAX_BATCH_BYTES = 1_073_741_824
_INVALID_WINDOWS_CHARS = set('<>:"|?*')
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_COPY_CHUNK_BYTES = 1024 * 1024


class UploadPathError(ValueError):
    pass


class UploadTooLarge(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StoredBatch:
    id: str
    root_name: str
    path: Path


def safe_relative_path(raw: str) -> PurePosixPath:
    if not isinstance(raw, str) or not raw:
        raise UploadPathError("Đường dẫn upload không được để trống")
    normalized = unicodedata.normalize("NFC", raw.replace("\\", "/"))
    if normalized.startswith("/") or _DRIVE_PREFIX.match(normalized):
        raise UploadPathError("Đường dẫn upload phải là đường dẫn tương đối")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise UploadPathError("Đường dẫn upload chứa thành phần không hợp lệ")
    for part in parts:
        if part != part.rstrip(" ."):
            raise UploadPathError("Tên file không được kết thúc bằng dấu cách hoặc dấu chấm")
        if any(character in _INVALID_WINDOWS_CHARS or ord(character) < 32 for character in part):
            raise UploadPathError("Tên file chứa ký tự Windows không hợp lệ")
        if part.split(".", 1)[0].upper() in _RESERVED_NAMES:
            raise UploadPathError("Tên file dành riêng cho Windows")
    if len(normalized) > 240:
        raise UploadPathError("Đường dẫn upload dài quá 240 ký tự")
    return PurePosixPath(*parts)


def is_skipped_upload(path: PurePosixPath) -> bool:
    for part in path.parts:
        folded = part.casefold()
        if (
            folded == "output"
            or part.startswith(".")
            or part.startswith("~$")
            or folded in {"thumbs.db", "desktop.ini"}
        ):
            return True
    return False


def new_batch_id(now: datetime | None = None) -> str:
    moment = now or datetime.now()
    return f"{moment:%Y%m%d-%H%M%S}-{uuid4().hex[:4]}"


def store_batch(
    uploads_dir: Path,
    files: Iterable[tuple[str, BinaryIO]],
    *,
    max_bytes: int = MAX_BATCH_BYTES,
) -> StoredBatch:
    uploads_dir = Path(uploads_dir)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    batch_id = new_batch_id()
    incoming = uploads_dir / f".incoming-{batch_id}"
    destination = uploads_dir / batch_id
    root_name: str | None = None
    total_bytes = 0
    incoming.mkdir()
    try:
        for raw_path, stream in files:
            relative = safe_relative_path(raw_path)
            candidate_root = relative.parts[0]
            if root_name is None:
                root_name = candidate_root
            elif candidate_root != root_name:
                raise UploadPathError("Chỉ upload một folder gốc mỗi lần")
            if is_skipped_upload(relative):
                continue

            output = incoming.joinpath(*relative.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("wb") as handle:
                while True:
                    chunk = stream.read(_COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        raise UploadTooLarge("Batch vượt quá 1 GB")
                    handle.write(chunk)

        if root_name is None:
            raise UploadPathError("Batch không có file")
        os.replace(incoming, destination)
    except BaseException:
        shutil.rmtree(incoming, ignore_errors=True)
        raise
    return StoredBatch(id=batch_id, root_name=root_name, path=destination)
