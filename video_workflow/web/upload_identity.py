from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


MISSING_LEVELS = (
    "Thiếu tầng Mã môn/Bài: cần dạng "
    "<Mã môn>/<Bài N_Tên bài>/<T<tiết>_Tên tiết>"
)
BAD_FORMAT = 'Tên bài hoặc tên tiết không đúng dạng "Bài N_…" / "T<số>_…"'
NO_LESSON_ORDER = (
    "Bài không có số thứ tự — cần điền lessonOrder trong manifest trước khi import"
)


@dataclass(frozen=True, slots=True)
class UploadIdentity:
    triple: str
    course_id: int
    course_slug: str
    lesson_slug: str
    lesson_order: int | None
    video_slug: str
    video_title: str


@dataclass(frozen=True, slots=True)
class UploadCheck:
    relpath: str
    identity: UploadIdentity | None
    issue: str | None
    warning: str | None


def upload_triple(leaf_relpath: str) -> str | None:
    parts = [part for part in leaf_relpath.replace("\\", "/").split("/") if part]
    if len(parts) < 3:
        return None
    return "/".join(parts[-3:])


def _failure_checks(
    leaf_relpaths: Sequence[str], message: str
) -> dict[str, UploadCheck]:
    issue = f"Không kiểm tra được với pyramid-upload-to-r2: {message}"
    return {
        relpath: UploadCheck(relpath, identity=None, issue=issue, warning=None)
        for relpath in leaf_relpaths
    }


def _identity(triple: str, value: dict[str, Any]) -> UploadIdentity:
    return UploadIdentity(
        triple=triple,
        course_id=int(value["courseId"]),
        course_slug=str(value["courseSlug"]),
        lesson_slug=str(value["lessonSlug"]),
        lesson_order=(
            None if value.get("lessonOrder") is None else int(value["lessonOrder"])
        ),
        video_slug=str(value["videoSlug"]),
        video_title=str(value["videoTitle"]),
    )


def check_upload_identities(
    repo: Path,
    leaf_relpaths: Sequence[str],
    *,
    run: Callable[..., Any] = subprocess.run,
    node: str = "node",
) -> dict[str, UploadCheck]:
    repo = Path(repo).resolve()
    triples = {relpath: upload_triple(relpath) for relpath in leaf_relpaths}
    helper_paths = [f"{triple}.mp4" for triple in triples.values() if triple is not None]
    payload: dict[str, Any] = {}
    if helper_paths:
        helper = Path(__file__).with_name("node") / "parse_video_paths.mjs"
        try:
            completed = run(
                [node, str(helper), str(repo)],
                input=json.dumps(helper_paths, ensure_ascii=False),
                capture_output=True,
                check=True,
                encoding="utf-8",
                cwd=repo,
                timeout=30,
            )
            payload = json.loads(completed.stdout)
        except Exception as exc:
            return _failure_checks(leaf_relpaths, str(exc))

    checks: dict[str, UploadCheck] = {}
    for relpath, triple in triples.items():
        if triple is None:
            checks[relpath] = UploadCheck(relpath, None, MISSING_LEVELS, None)
            continue
        result = payload.get(f"{triple}.mp4")
        if not isinstance(result, dict):
            return _failure_checks(leaf_relpaths, "helper trả về thiếu kết quả")
        parsed = result.get("identity")
        if parsed is None:
            course = triple.split("/", 1)[0]
            issue = (
                BAD_FORMAT
                if result.get("courseKnown")
                else f'Mã môn "{course}" không có trong danh sách khoá học'
            )
            checks[relpath] = UploadCheck(relpath, None, issue, None)
            continue
        identity = _identity(triple, parsed)
        warning = NO_LESSON_ORDER if identity.lesson_order is None else None
        checks[relpath] = UploadCheck(relpath, identity, None, warning)

    by_target: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for relpath, check in checks.items():
        if check.identity is not None:
            identity = check.identity
            by_target[
                (identity.course_slug, identity.lesson_slug, identity.video_slug)
            ].append(relpath)
    for duplicate_paths in by_target.values():
        if len(duplicate_paths) < 2:
            continue
        for relpath in duplicate_paths:
            other = next(path for path in duplicate_paths if path != relpath)
            checks[relpath] = replace(
                checks[relpath], issue=f"Trùng đích R2 với bài {other}"
            )
    return checks
