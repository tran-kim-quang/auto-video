from __future__ import annotations

from pathlib import Path

import pytest

from video_workflow.leaf_output import publish_leaf_output


def test_output_has_exactly_two_files(tmp_path: Path) -> None:
    leaf = tmp_path / "lesson"
    leaf.mkdir()
    video = tmp_path / "rendered.mp4"
    video.write_bytes(b"video")

    result = publish_leaf_output(leaf, video, "T40", "WEBVTT\n\n")

    assert result == leaf / "output" / "T40.mp4"
    assert sorted(path.name for path in (leaf / "output").iterdir()) == [
        "T40.mp4",
        "vi.vtt",
    ]
    assert result.read_bytes() == b"video"
    assert (leaf / "output" / "vi.vtt").read_text(encoding="utf-8") == (
        "WEBVTT\n\n"
    )


def test_replaces_previous_output(tmp_path: Path) -> None:
    leaf = tmp_path / "lesson"
    output = leaf / "output"
    output.mkdir(parents=True)
    (output / "old.json").write_text("old", encoding="utf-8")
    video = tmp_path / "rendered.mp4"
    video.write_bytes(b"new")

    publish_leaf_output(leaf, video, "T40", "WEBVTT\n\n")

    assert sorted(path.name for path in output.iterdir()) == ["T40.mp4", "vi.vtt"]


def test_failure_keeps_old_output(tmp_path: Path) -> None:
    leaf = tmp_path / "lesson"
    output = leaf / "output"
    output.mkdir(parents=True)
    old = output / "old.mp4"
    old.write_bytes(b"old")

    with pytest.raises(FileNotFoundError):
        publish_leaf_output(leaf, tmp_path / "missing.mp4", "T40", "WEBVTT\n\n")

    assert old.read_bytes() == b"old"
    assert list(leaf.glob(".output.staging-*")) == []
