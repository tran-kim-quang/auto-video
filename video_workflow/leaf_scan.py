from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .folder_batch import _ignored_directory
from .narration_script import ScriptError, load_slide_script
from .powerpoint import count_pptx_slides


@dataclass(frozen=True, slots=True)
class LeafFolder:
    path: Path
    relpath: str
    pptx: Path | None
    script: Path | None
    slide_count: int | None
    script_chars: int
    has_output: bool
    error: str | None

    @property
    def valid(self) -> bool:
        return self.error is None


def _ignored_file(path: Path) -> bool:
    name = path.name
    return (
        name.startswith(".")
        or name.startswith("~$")
        or name.casefold() in {"thumbs.db", "desktop.ini"}
    )


def _leaf(
    path: Path,
    relpath: str,
    *,
    pptx: Path | None,
    script: Path | None,
    slide_count: int | None = None,
    script_chars: int = 0,
    error: str | None,
) -> LeafFolder:
    return LeafFolder(
        path=path,
        relpath=relpath,
        pptx=pptx,
        script=script,
        slide_count=slide_count,
        script_chars=script_chars,
        has_output=(path / "output").is_dir(),
        error=error,
    )


def inspect_leaf(path: Path, relpath: str) -> LeafFolder:
    path = Path(path)
    files = sorted(
        (
            candidate
            for candidate in path.iterdir()
            if candidate.is_file() and not _ignored_file(candidate)
        ),
        key=lambda candidate: candidate.name.casefold(),
    )
    pptx_files = [file for file in files if file.suffix.casefold() == ".pptx"]
    script_files = [file for file in files if file.suffix.casefold() == ".txt"]
    pptx = pptx_files[0] if len(pptx_files) == 1 else None
    script = script_files[0] if len(script_files) == 1 else None

    if len(files) > 2:
        return _leaf(
            path,
            relpath,
            pptx=pptx,
            script=script,
            error=f"Có {len(files)} file (cần đúng 1 .pptx và 1 .txt)",
        )
    if len(pptx_files) == 2:
        return _leaf(
            path,
            relpath,
            pptx=None,
            script=script,
            error="Có 2 file .pptx",
        )
    if len(script_files) == 2:
        return _leaf(
            path,
            relpath,
            pptx=pptx,
            script=None,
            error="Có 2 file .txt",
        )
    if not pptx_files:
        return _leaf(
            path,
            relpath,
            pptx=None,
            script=script,
            error="Thiếu file .pptx",
        )
    if not script_files:
        return _leaf(
            path,
            relpath,
            pptx=pptx,
            script=None,
            error="Thiếu file kịch bản .txt",
        )

    assert pptx is not None
    assert script is not None
    try:
        slide_count = count_pptx_slides(pptx)
    except Exception:
        return _leaf(
            path,
            relpath,
            pptx=pptx,
            script=script,
            error="Không đọc được file .pptx",
        )

    try:
        script.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        return _leaf(
            path,
            relpath,
            pptx=pptx,
            script=script,
            slide_count=slide_count,
            error="Kịch bản không đọc được (cần mã hoá UTF-8)",
        )

    try:
        segments = load_slide_script(script)
    except ScriptError as exc:
        return _leaf(
            path,
            relpath,
            pptx=pptx,
            script=script,
            slide_count=slide_count,
            error=f"Kịch bản không hợp lệ: {exc}",
        )

    script_chars = sum(len(segment.text) for segment in segments)
    last_slide_id = segments[-1].slide_id
    if last_slide_id != slide_count:
        return _leaf(
            path,
            relpath,
            pptx=pptx,
            script=script,
            slide_count=slide_count,
            script_chars=script_chars,
            error=(
                f"Kịch bản có {last_slide_id} slide, "
                f"PPTX có {slide_count} slide"
            ),
        )

    return _leaf(
        path,
        relpath,
        pptx=pptx,
        script=script,
        slide_count=slide_count,
        script_chars=script_chars,
        error=None,
    )


def scan_leaf_folders(root: Path) -> tuple[LeafFolder, ...]:
    root = Path(root)
    leaves: list[LeafFolder] = []
    for current, directories, filenames in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        path = Path(current)
        directories[:] = sorted(
            (
                name
                for name in directories
                if not _ignored_directory(path / name)
            ),
            key=str.casefold,
        )
        if directories:
            continue
        if not any(not _ignored_file(path / name) for name in filenames):
            continue
        relpath = unicodedata.normalize(
            "NFC",
            path.relative_to(root).as_posix(),
        )
        leaves.append(inspect_leaf(path, relpath))

    leaves.sort(key=lambda leaf: leaf.relpath.casefold())
    return tuple(leaves)
