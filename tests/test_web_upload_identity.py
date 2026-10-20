from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from video_workflow.web.upload_identity import (
    check_upload_identities,
    upload_triple,
)


MISSING_LEVELS = (
    "Thiếu tầng Mã môn/Bài: cần dạng "
    "<Mã môn>/<Bài N_Tên bài>/<T<tiết>_Tên tiết>"
)
BAD_FORMAT = 'Tên bài hoặc tên tiết không đúng dạng "Bài N_…" / "T<số>_…"'
NO_ORDER = "Bài không có số thứ tự — cần điền lessonOrder trong manifest trước khi import"


def _parsed(
    relative_path: str,
    *,
    course_slug: str = "601-toan-hoc-lop-6",
    lesson_slug: str = "19-bai-19-x",
    lesson_order: int | None = 19,
    video_slug: str = "48-y",
    part_no: int = 1,
) -> dict[str, object]:
    return {
        "relativePath": relative_path,
        "courseId": 601,
        "courseSlug": course_slug,
        "lessonOrder": lesson_order,
        "lessonTitle": "Bài 19: X",
        "lessonSlug": lesson_slug,
        "sectionNo": 48,
        "partNo": part_no,
        "videoTitle": "Tiết 48 - Y",
        "videoSlug": video_slug,
    }


def _runner(payload: dict[str, dict[str, object]], calls: list | None = None):
    def run(*args, **kwargs):
        if calls is not None:
            calls.append((args, kwargs))
        return SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=0)

    return run


def test_upload_triple_uses_last_three_components() -> None:
    assert (
        upload_triple("Ngay 20-10/Toan6/Bài 19_X/T48_Y")
        == "Toan6/Bài 19_X/T48_Y"
    )
    assert upload_triple("test gen/T48_Y") is None


def test_identity_keeps_section_part_and_lesson_title(tmp_path: Path) -> None:
    paths = ["Toan6/Bài 19_X/T48_Y", "Toan6/Bài 19_X/T48_2_Y"]
    payload = {
        f"{paths[0]}.mp4": {
            "identity": _parsed(f"{paths[0]}.mp4"),
            "courseKnown": True,
        },
        f"{paths[1]}.mp4": {
            "identity": _parsed(
                f"{paths[1]}.mp4", video_slug="48-2-y", part_no=2
            ),
            "courseKnown": True,
        },
    }

    checks = check_upload_identities(tmp_path, paths, run=_runner(payload))

    assert checks[paths[0]].identity is not None
    assert checks[paths[0]].identity.section_no == 48
    assert checks[paths[0]].identity.part_no == 1
    assert checks[paths[0]].identity.lesson_title == "Bài 19: X"
    assert checks[paths[1]].identity is not None
    assert checks[paths[1]].identity.part_no == 2


def test_exact_issues_and_warning_from_helper_results(tmp_path: Path) -> None:
    paths = [
        "test gen/T48_Y",
        "MonLa/Bài 1_X/T1_Y",
        "Toan6/Bài sai/X",
        "Toan6/Ôn tập chương 1/T1_Ôn tập",
    ]
    helper_payload = {
        "MonLa/Bài 1_X/T1_Y.mp4": {"identity": None, "courseKnown": False},
        "Toan6/Bài sai/X.mp4": {"identity": None, "courseKnown": True},
        "Toan6/Ôn tập chương 1/T1_Ôn tập.mp4": {
            "identity": _parsed(
                "Toan6/Ôn tập chương 1/T1_Ôn tập.mp4",
                lesson_slug="on-tap-chuong-1",
                lesson_order=None,
                video_slug="1-on-tap",
            ),
            "courseKnown": True,
        },
    }

    checks = check_upload_identities(tmp_path, paths, run=_runner(helper_payload))

    assert checks[paths[0]].issue == MISSING_LEVELS
    assert checks[paths[1]].issue == 'Mã môn "MonLa" không có trong danh sách khoá học'
    assert checks[paths[2]].issue == BAD_FORMAT
    assert checks[paths[3]].issue is None
    assert checks[paths[3]].warning == NO_ORDER


def test_duplicate_target_marks_only_the_duplicate_pair(tmp_path: Path) -> None:
    paths = [
        "root/Toan6/Bài 19_X/T48_Y",
        "other/Toan6/Bài 19_X/T48_Y",
        "root/Toan6/Bài 19_X/T49_Z",
    ]
    triples = [upload_triple(path) for path in paths]
    payload = {
        f"{triples[0]}.mp4": {
            "identity": _parsed(f"{triples[0]}.mp4"),
            "courseKnown": True,
        },
        f"{triples[2]}.mp4": {
            "identity": _parsed(f"{triples[2]}.mp4", video_slug="49-z"),
            "courseKnown": True,
        },
    }

    checks = check_upload_identities(tmp_path, paths, run=_runner(payload))

    assert checks[paths[0]].issue == f"Trùng đích R2 với bài {paths[1]}"
    assert checks[paths[1]].issue == f"Trùng đích R2 với bài {paths[0]}"
    assert checks[paths[2]].issue is None


def test_helper_failure_blocks_every_leaf(tmp_path: Path) -> None:
    def fail(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("node", 30)

    paths = ["Toan6/Bài 19_X/T48_Y", "Toan6/Bài 20_Z/T49_Q"]
    checks = check_upload_identities(tmp_path, paths, run=fail)

    for path in paths:
        assert checks[path].issue == (
            "Không kiểm tra được với pyramid-upload-to-r2: "
            "Command 'node' timed out after 30 seconds"
        )


def test_helper_receives_utf8_json_and_expected_subprocess_options(tmp_path: Path) -> None:
    path = "Toan6/Bài 19_Hình chữ nhật, hình thoi/T48_Hình thang cân"
    triple = upload_triple(path)
    calls: list = []
    payload = {
        f"{triple}.mp4": {
            "identity": _parsed(f"{triple}.mp4"),
            "courseKnown": True,
        }
    }

    check_upload_identities(tmp_path, [path], run=_runner(payload, calls))

    args, kwargs = calls[0]
    assert args[0][0] == "node"
    assert json.loads(kwargs["input"]) == [f"{triple}.mp4"]
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["cwd"] == tmp_path
    assert kwargs["timeout"] == 30


def test_helper_resolves_relative_repo_before_changing_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(tmp_path)
    path = "Toan6/Bài 19_X/T48_Y"
    triple = upload_triple(path)
    calls: list = []
    payload = {
        f"{triple}.mp4": {
            "identity": _parsed(f"{triple}.mp4"),
            "courseKnown": True,
        }
    }

    check_upload_identities(Path("repo"), [path], run=_runner(payload, calls))

    args, kwargs = calls[0]
    assert args[0][2] == str(repo)
    assert kwargs["cwd"] == repo


@pytest.mark.skipif(
    shutil.which("node") is None
    or not Path(
        "/home/meconlonton/work/pyramid-upload-to-r2/scripts/local-bulk-upload.mjs"
    ).is_file(),
    reason="Node or pyramid-upload-to-r2 is unavailable",
)
def test_real_helper_preserves_utf8_and_lesson_order() -> None:
    repo = Path("/home/meconlonton/work/pyramid-upload-to-r2")
    numbered = "Toan6/Bài 19_Hình chữ nhật, hình thoi/T48_Hình thang cân"
    unordered = "Toan6/Ôn tập chương 1/T1_Ôn tập"

    checks = check_upload_identities(repo, [numbered, unordered])

    assert checks[numbered].identity is not None
    assert checks[numbered].identity.course_id == 601
    assert checks[numbered].identity.lesson_order == 19
    assert checks[unordered].warning == NO_ORDER
