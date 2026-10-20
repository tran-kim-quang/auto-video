from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from video_workflow.web.upload_export import (
    ExportItem,
    build_cc_tree,
    build_video_tree,
    find_output_files,
    new_export_dir,
)


def _item(tmp_path: Path) -> ExportItem:
    source = tmp_path / "leaf" / "output"
    source.mkdir(parents=True)
    video = source / "TOAN6_B19_T48.mp4"
    vtt = source / "vi.vtt"
    video.write_bytes(b"video bytes")
    vtt.write_text("WEBVTT\n", encoding="utf-8")
    return ExportItem(
        relpath="root/Toan6/Bài 19_X/T48_Y",
        triple="Toan6/Bài 19_X/T48_Y",
        video=video,
        vtt=vtt,
    )


def test_builds_video_and_cc_trees_with_source_contents(tmp_path: Path) -> None:
    item = _item(tmp_path)
    export_dir = tmp_path / "export"

    video_root = build_video_tree(export_dir, [item])
    cc_root = build_cc_tree(export_dir, [item])

    video = video_root / "Toan6" / "Bài 19_X" / "T48_Y.mp4"
    vtt = cc_root / "Toan6" / "Bài 19_X" / "T48_Y" / "vi.vtt"
    assert video.read_bytes() == b"video bytes"
    assert vtt.read_text(encoding="utf-8") == "WEBVTT\n"


def test_find_output_files_accepts_one_legacy_named_mp4(tmp_path: Path) -> None:
    item = _item(tmp_path)

    assert find_output_files(item.video.parent.parent) == (item.video, item.vtt)


def test_find_output_files_rejects_two_mp4_files(tmp_path: Path) -> None:
    item = _item(tmp_path)
    (item.video.parent / "T48_Hình thang cân.mp4").write_bytes(b"other")

    assert find_output_files(item.video.parent.parent) is None


def test_link_failure_falls_back_to_copy(tmp_path: Path, monkeypatch) -> None:
    item = _item(tmp_path)

    def fail_link(_source, _destination):
        raise OSError("cross-device link")

    monkeypatch.setattr(os, "link", fail_link)

    video_root = build_video_tree(tmp_path / "export", [item])

    copied = video_root / "Toan6" / "Bài 19_X" / "T48_Y.mp4"
    assert copied.read_bytes() == item.video.read_bytes()


def test_new_export_dir_uses_batch_and_timestamp(tmp_path: Path) -> None:
    created = new_export_dir(
        tmp_path / "exports",
        "batch-1",
        now=datetime(2026, 10, 20, 16, 52, 7),
    )

    assert created == tmp_path / "exports" / "batch-1" / "20261020-165207"
    assert created.is_dir()
