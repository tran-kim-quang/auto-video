from pathlib import Path

import pytest

from video_workflow.web.output_location import (
    OutputRootError,
    is_parked,
    move_output_files,
    output_paths,
    parked_paths,
    publish_output_files,
    resolve_output_root,
)


def test_output_paths_use_last_three_parts() -> None:
    assert output_paths(
        Path("/o"), "Ngay 20-10/Toan6/Bài 19_X/T48_Y"
    ) == (
        Path("/o/Toan6/Bài 19_X/T48_Y.mp4"),
        Path("/o/Toan6/Bài 19_X/T48_Y/vi.vtt"),
    )


def test_output_paths_keep_all_parts_when_less_than_three() -> None:
    assert output_paths(Path("/o"), "test gen/T48_Y") == (
        Path("/o/test gen/T48_Y.mp4"),
        Path("/o/test gen/T48_Y/vi.vtt"),
    )


def test_parked_paths_use_last_three_parts_under_waiting_directory() -> None:
    paths = parked_paths(Path("/o"), "Ngay/Toan6/Bài 1_X/T1_Y")

    assert paths == (
        Path("/o/_chua-co-tren-production/Toan6/Bài 1_X/T1_Y.mp4"),
        Path("/o/_chua-co-tren-production/Toan6/Bài 1_X/T1_Y/vi.vtt"),
    )
    assert is_parked(Path("/o"), paths[0]) is True
    assert is_parked(Path("/o"), Path("/o/Toan6/Bài 1_X/T1_Y.mp4")) is False


def test_move_output_files_replaces_existing_destinations(tmp_path: Path) -> None:
    video_src = tmp_path / "source.mp4"
    vtt_src = tmp_path / "source" / "vi.vtt"
    video_dest = tmp_path / "parked" / "lesson.mp4"
    vtt_dest = tmp_path / "parked" / "lesson" / "vi.vtt"
    video_src.write_bytes(b"new video")
    vtt_src.parent.mkdir()
    vtt_src.write_text("WEBVTT\nnew\n", encoding="utf-8")
    video_dest.parent.mkdir()
    video_dest.write_bytes(b"old video")
    vtt_dest.parent.mkdir()
    vtt_dest.write_text("WEBVTT\nold\n", encoding="utf-8")

    move_output_files(video_src, vtt_src, video_dest, vtt_dest)

    assert video_dest.read_bytes() == b"new video"
    assert vtt_dest.read_text(encoding="utf-8") == "WEBVTT\nnew\n"
    assert not video_src.exists()
    assert not vtt_src.exists()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", "Cần nhập thư mục lưu kết quả"),
        ("   ", "Cần nhập thư mục lưu kết quả"),
        ("relative/path", "Thư mục lưu kết quả phải là đường dẫn tuyệt đối"),
    ],
)
def test_resolve_output_root_rejects_missing_or_relative_paths(
    tmp_path: Path, raw: str, expected: str
) -> None:
    with pytest.raises(OutputRootError, match=f"^{expected}$"):
        resolve_output_root(raw, tmp_path)


def test_resolve_output_root_rejects_internal_directory(tmp_path: Path) -> None:
    internal = tmp_path / "uploads" / "nested"

    with pytest.raises(
        OutputRootError, match="^Không được lưu vào thư mục nội bộ của tool$"
    ):
        resolve_output_root(str(internal), tmp_path)


def test_resolve_output_root_reports_unwritable_path(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    requested = blocker / "out"

    with pytest.raises(
        OutputRootError,
        match=f"^Không ghi được vào thư mục: {requested}$",
    ):
        resolve_output_root(str(requested), tmp_path)


def test_resolve_output_root_expands_home_and_creates_unicode_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    home_result = resolve_output_root("~/x", tmp_path / "workspace")
    unicode_result = resolve_output_root(
        str(tmp_path / "Video bài giảng"), tmp_path / "workspace"
    )

    assert home_result == (tmp_path / "x").resolve()
    assert home_result.is_dir()
    assert unicode_result == (tmp_path / "Video bài giảng").resolve()
    assert unicode_result.is_dir()


def test_publish_output_files_replaces_files_without_removing_lesson_extras(
    tmp_path: Path,
) -> None:
    source = tmp_path / "render.mp4"
    video_dest = tmp_path / "out" / "Toan6" / "Bài 19_X" / "T48_Y.mp4"
    vtt_dest = tmp_path / "out" / "Toan6" / "Bài 19_X" / "T48_Y" / "vi.vtt"
    quiz = vtt_dest.parent / "quiz.json"
    quiz.parent.mkdir(parents=True)
    quiz.write_text('{"keep": true}', encoding="utf-8")

    source.write_bytes(b"first video")
    publish_output_files(source, "WEBVTT\n\nfirst\n", video_dest, vtt_dest)
    source.write_bytes(b"second video")
    publish_output_files(source, "WEBVTT\n\nsecond\n", video_dest, vtt_dest)

    assert video_dest.read_bytes() == b"second video"
    assert vtt_dest.read_text(encoding="utf-8") == "WEBVTT\n\nsecond\n"
    assert quiz.read_text(encoding="utf-8") == '{"keep": true}'
    assert not list(video_dest.parent.glob("*.tmp-*"))
    assert not list(vtt_dest.parent.glob("*.tmp-*"))
