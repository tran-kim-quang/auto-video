from __future__ import annotations

import io
import re
from pathlib import Path, PurePosixPath

import pytest

from video_workflow.web.uploads import (
    UploadPathError,
    UploadTooLarge,
    new_batch_id,
    safe_relative_path,
    store_batch,
)


@pytest.mark.parametrize(
    "raw",
    [
        "../x.txt",
        "/etc/x",
        "C:/x.txt",
        "a/../../b",
        "a/CON.txt",
        "a/b?.txt",
        "a/b. ",
    ],
)
def test_rejects_traversal(raw: str) -> None:
    with pytest.raises(UploadPathError):
        safe_relative_path(raw)


def test_backslash_and_nfc() -> None:
    assert safe_relative_path("Toa\u0301n\\bai.txt") == PurePosixPath(
        "Toán/bai.txt"
    )


def test_store_batch_keeps_tree(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"

    stored = store_batch(
        uploads,
        [
            ("root/t40/a.pptx", io.BytesIO(b"deck")),
            ("root/t40/a.txt", io.BytesIO(b"script")),
        ],
    )

    assert stored.path == uploads / stored.id
    assert stored.root_name == "root"
    assert (stored.path / "root" / "t40" / "a.pptx").read_bytes() == b"deck"
    assert (stored.path / "root" / "t40" / "a.txt").read_bytes() == b"script"


def test_skips_output_and_junk(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"

    stored = store_batch(
        uploads,
        [
            ("root/t40/a.pptx", io.BytesIO(b"deck")),
            ("root/t40/a.txt", io.BytesIO(b"script")),
            ("root/t40/output/old.mp4", io.BytesIO(b"old")),
            ("root/t40/.DS_Store", io.BytesIO(b"junk")),
        ],
    )

    assert not (stored.path / "root" / "t40" / "output").exists()
    assert not (stored.path / "root" / "t40" / ".DS_Store").exists()


def test_multiple_roots_rejected(tmp_path: Path) -> None:
    with pytest.raises(
        UploadPathError,
        match="Chỉ upload một folder gốc mỗi lần",
    ):
        store_batch(
            tmp_path / "uploads",
            [
                ("one/a.txt", io.BytesIO(b"a")),
                ("two/b.txt", io.BytesIO(b"b")),
            ],
        )


def test_too_large_leaves_nothing(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"

    with pytest.raises(UploadTooLarge, match="Batch vượt quá 1 GB"):
        store_batch(
            uploads,
            [("root/a.txt", io.BytesIO(b"12345678901"))],
            max_bytes=10,
        )

    assert list(uploads.iterdir()) == []


class _BrokenStream:
    def read(self, _size: int) -> bytes:
        raise OSError("upload interrupted")


def test_stream_error_leaves_nothing(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"

    with pytest.raises(OSError, match="upload interrupted"):
        store_batch(
            uploads,
            [
                ("root/a.txt", io.BytesIO(b"first")),
                ("root/b.txt", _BrokenStream()),
            ],
        )

    assert list(uploads.iterdir()) == []


def test_batch_id_format() -> None:
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", new_batch_id())
