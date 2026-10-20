from __future__ import annotations

import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .upload_export import ExportItem
from .upload_identity import UploadIdentity


@dataclass(frozen=True, slots=True)
class SyncTarget:
    relpath: str
    identity: UploadIdentity
    item: ExportItem


@dataclass(frozen=True, slots=True)
class ReadyTarget:
    target: SyncTarget
    lesson: dict[str, Any]
    overwrite: bool
    warnings: list[str]


@dataclass(frozen=True, slots=True)
class SyncPlan:
    ready: list[ReadyTarget]
    not_on_production: list[SyncTarget]
    blocked: list[tuple[SyncTarget, str]]


def norm(text: str | None) -> str:
    return unicodedata.normalize("NFC", text or "").strip()


def _matching_video(lesson: Mapping[str, Any], identity: UploadIdentity):
    return next(
        (
            video
            for video in lesson.get("videos", [])
            if video.get("sectionNo") == identity.section_no
            and video.get("partNo") == identity.part_no
        ),
        None,
    )


def plan_production_sync(
    targets: Sequence[SyncTarget],
    lessons_by_course: Mapping[int, list[dict[str, Any]]],
) -> SyncPlan:
    ready: list[ReadyTarget] = []
    not_on_production: list[SyncTarget] = []
    blocked: list[tuple[SyncTarget, str]] = []
    resolved: list[tuple[SyncTarget, dict[str, Any]]] = []
    for target in targets:
        identity = target.identity
        lessons = lessons_by_course.get(identity.course_id, [])
        matches = [
            lesson
            for lesson in lessons
            if norm(lesson.get("slug")) == norm(identity.lesson_slug)
        ]
        if not matches:
            matches = [
                lesson
                for lesson in lessons
                if norm(lesson.get("title")) == norm(identity.lesson_title)
            ]
        if len(matches) > 1:
            blocked.append(
                (
                    target,
                    f'Nhiều bài trên production trùng tên "{identity.lesson_title}"',
                )
            )
            continue
        if not matches:
            not_on_production.append(target)
            continue
        resolved.append((target, matches[0]))

    def lesson_key(target: SyncTarget, lesson: Mapping[str, Any]) -> tuple[Any, ...]:
        lesson_id = lesson.get("id")
        return (
            target.identity.course_id,
            "id" if lesson_id is not None else "slug",
            lesson_id if lesson_id is not None else norm(lesson.get("slug")),
        )

    by_slot: dict[tuple[Any, ...], list[SyncTarget]] = defaultdict(list)
    section_parts: dict[tuple[Any, ...], set[int]] = defaultdict(set)
    for target, lesson in resolved:
        identity = target.identity
        key = lesson_key(target, lesson)
        by_slot[(*key, identity.section_no, identity.part_no)].append(target)
        section_parts[(*key, identity.section_no)].add(identity.part_no)

    collisions = {
        target.relpath: group
        for group in by_slot.values()
        if len(group) > 1
        for target in group
    }

    for target, lesson in resolved:
        identity = target.identity
        collision = collisions.get(target.relpath)
        if collision is not None:
            other = next(value for value in collision if value.relpath != target.relpath)
            blocked.append(
                (target, f"Trùng đích R2 với bài {other.relpath}")
            )
            continue

        if norm(lesson.get("description")):
            blocked.append(
                (target, "Bài có mô tả trên production — import sẽ xoá mô tả")
            )
            continue

        parts = section_parts[(*lesson_key(target, lesson), identity.section_no)]
        multipart = identity.part_no > 1 or len(parts) > 1
        if identity.part_no == 1 and not multipart:
            period = next(
                (
                    value
                    for value in lesson.get("periods", [])
                    if value.get("order") == identity.section_no
                ),
                None,
            )
            if period is not None and norm(period.get("title")) != norm(
                identity.video_title
            ):
                blocked.append(
                    (
                        target,
                        f'Tên tiết khác production: "{identity.video_title}" '
                        f'≠ "{period.get("title", "")}"',
                    )
                )
                continue

        existing_video = _matching_video(lesson, identity)
        if existing_video is not None and norm(existing_video.get("slug")) != norm(
            identity.video_slug
        ):
            blocked.append(
                (
                    target,
                    f'Slug video khác production: "{identity.video_slug}" '
                    f'≠ "{existing_video.get("slug", "")}"',
                )
            )
            continue

        warnings = (
            [
                f"Tên tiết {identity.section_no} sẽ đổi thành tên của phần xử lý sau cùng"
            ]
            if multipart
            else []
        )
        ready.append(
            ReadyTarget(
                target=target,
                lesson=lesson,
                overwrite=existing_video is not None,
                warnings=warnings,
            )
        )
    return SyncPlan(
        ready=ready, not_on_production=not_on_production, blocked=blocked
    )


def _ready_by_video_path(plan: SyncPlan) -> dict[str, ReadyTarget]:
    return {
        f"{ready.target.identity.triple}.mp4": ready for ready in plan.ready
    }


def import_ready_video_manifest(
    manifest: dict[str, Any], plan: SyncPlan
) -> dict[str, Any]:
    ready_by_path = _ready_by_video_path(plan)
    videos: list[dict[str, Any]] = []
    for value in manifest.get("videos", []):
        ready = ready_by_path.get(value.get("relativePath"))
        if ready is None:
            continue
        updated = dict(value)
        updated.update(
            lessonOrder=ready.lesson["order"],
            lessonSlug=ready.lesson["slug"],
            lessonTitle=ready.lesson["title"],
        )
        videos.append(updated)
    result = dict(manifest)
    result["videos"] = videos
    return result


def import_ready_cc_manifest(
    manifest: dict[str, Any], plan: SyncPlan, uploaded_triples: set[str]
) -> dict[str, Any]:
    ready_by_triple = {
        ready.target.identity.triple: ready for ready in plan.ready
    }
    uploaded = {
        value[:-4] if value.endswith(".mp4") else value for value in uploaded_triples
    }
    subtitles: list[dict[str, Any]] = []
    for value in manifest.get("subtitles", []):
        relative_path = str(value.get("relativePath", ""))
        triple = relative_path.removesuffix("/vi.vtt")
        ready = ready_by_triple.get(triple)
        if ready is None or triple not in uploaded:
            continue
        updated = dict(value)
        updated["lessonSlug"] = ready.lesson["slug"]
        subtitles.append(updated)
    result = dict(manifest)
    result["subtitles"] = subtitles
    return result


def _lesson_by_slug(
    lessons_by_course: Mapping[int, list[dict]], course_id: int, slug: str
) -> dict | None:
    return next(
        (
            lesson
            for lesson in lessons_by_course.get(course_id, [])
            if norm(lesson.get("slug")) == norm(slug)
        ),
        None,
    )


def _video_by_numbers(lesson: Mapping[str, Any] | None, section: int, part: int):
    if lesson is None:
        return None
    return next(
        (
            video
            for video in lesson.get("videos", [])
            if video.get("sectionNo") == section and video.get("partNo") == part
        ),
        None,
    )


def worker_observed(
    lessons_by_course: Mapping[int, list[dict]],
    plan: SyncPlan,
    video_manifest: dict,
) -> bool:
    ready_by_path = _ready_by_video_path(plan)
    for value in video_manifest.get("videos", []):
        ready = ready_by_path.get(value.get("relativePath"))
        if ready is None:
            return False
        course_id = ready.target.identity.course_id
        lesson = _lesson_by_slug(
            lessons_by_course, course_id, str(value.get("lessonSlug", ""))
        )
        video = _video_by_numbers(
            lesson, int(value["sectionNo"]), int(value.get("partNo", 1))
        )
        if video is None or video.get("durationMinutes") != value.get(
            "durationMinutes"
        ):
            return False
    return True


def unconfirmed_targets(
    before: Mapping[int, list[dict]],
    after: Mapping[int, list[dict]],
    plan: SyncPlan,
    video_manifest: dict,
) -> list[str]:
    ready_by_path = _ready_by_video_path(plan)
    result: list[str] = []
    for value in video_manifest.get("videos", []):
        ready = ready_by_path.get(value.get("relativePath"))
        if ready is None:
            continue
        identity = ready.target.identity
        before_lesson = _lesson_by_slug(before, identity.course_id, ready.lesson["slug"])
        after_lesson = _lesson_by_slug(after, identity.course_id, ready.lesson["slug"])
        before_video = _video_by_numbers(
            before_lesson, identity.section_no, identity.part_no
        )
        after_video = _video_by_numbers(
            after_lesson, identity.section_no, identity.part_no
        )
        duration = value.get("durationMinutes")
        if (
            before_video is not None
            and after_video is not None
            and before_video.get("durationMinutes")
            == after_video.get("durationMinutes")
            == duration
        ):
            result.append(ready.target.relpath)
    return result


def _difference(lesson: Mapping[str, Any], field: str, old: Any, new: Any) -> str:
    return f"Bài {lesson.get('order')} {lesson.get('title')}: {field} {old!r} → {new!r}"


def _same_value(old: Any, new: Any) -> bool:
    if isinstance(old, str) and isinstance(new, str):
        return norm(old) == norm(new)
    return old == new


def verify_changes(
    before: Mapping[int, list[dict]],
    after: Mapping[int, list[dict]],
    plan: SyncPlan,
    video_manifest: dict,
) -> list[str]:
    ready_by_path = _ready_by_video_path(plan)
    target_by_lesson: dict[Any, list[tuple[ReadyTarget, dict[str, Any]]]] = {}
    for manifest_value in video_manifest.get("videos", []):
        ready = ready_by_path.get(manifest_value.get("relativePath"))
        if ready is not None:
            target_by_lesson.setdefault(ready.lesson.get("id"), []).append(
                (ready, manifest_value)
            )

    differences: list[str] = []
    after_by_id = {
        lesson.get("id"): lesson
        for lessons in after.values()
        for lesson in lessons
    }
    before_by_id = {
        lesson.get("id"): lesson
        for lessons in before.values()
        for lesson in lessons
    }
    lesson_fields = ("id", "title", "slug", "order", "description", "durationMinutes")
    for lessons in before.values():
        for old_lesson in lessons:
            lesson_id = old_lesson.get("id")
            new_lesson = after_by_id.get(lesson_id)
            if new_lesson is None:
                differences.append(_difference(old_lesson, "lesson", old_lesson, None))
                continue
            targets = target_by_lesson.get(lesson_id, [])
            for field in lesson_fields:
                old_value = old_lesson.get(field)
                new_value = new_lesson.get(field)
                if _same_value(old_value, new_value):
                    continue
                if field == "durationMinutes" and targets:
                    continue
                differences.append(_difference(old_lesson, field, old_value, new_value))

            target_tuples = {
                (
                    ready.target.identity.section_no,
                    ready.target.identity.part_no,
                ): (ready, value)
                for ready, value in targets
            }
            multipart_sections = {
                ready.target.identity.section_no
                for ready, _value in targets
                if ready.warnings
            }
            expected_period_titles: dict[int, str] = {}
            for ready, value in targets:
                expected_period_titles[ready.target.identity.section_no] = str(
                    value.get("videoTitle", ready.target.identity.video_title)
                )

            old_periods = {value.get("order"): value for value in old_lesson.get("periods", [])}
            new_periods = {value.get("order"): value for value in new_lesson.get("periods", [])}
            for order, old_period in old_periods.items():
                new_period = new_periods.get(order)
                if new_period is None:
                    differences.append(
                        _difference(old_lesson, f"period {order}", old_period, None)
                    )
                    continue
                for field in ("order", "title"):
                    if _same_value(old_period.get(field), new_period.get(field)):
                        continue
                    if (
                        field == "title"
                        and order in multipart_sections
                        and norm(new_period.get("title"))
                        == norm(expected_period_titles.get(order))
                    ):
                        continue
                    differences.append(
                        _difference(
                            old_lesson,
                            f"period {order}.{field}",
                            old_period.get(field),
                            new_period.get(field),
                        )
                    )
            for order, new_period in new_periods.items():
                if order in old_periods:
                    continue
                if (
                    order in expected_period_titles
                    and norm(new_period.get("title"))
                    == norm(expected_period_titles[order])
                ):
                    continue
                differences.append(
                    _difference(old_lesson, f"period {order}", None, new_period)
                )

            old_videos = {
                (value.get("sectionNo"), value.get("partNo")): value
                for value in old_lesson.get("videos", [])
            }
            new_videos = {
                (value.get("sectionNo"), value.get("partNo")): value
                for value in new_lesson.get("videos", [])
            }
            for key, old_video in old_videos.items():
                new_video = new_videos.get(key)
                if new_video is None:
                    differences.append(
                        _difference(old_lesson, f"video {key[0]}/{key[1]}", old_video, None)
                    )
                    continue
                for field in ("id", "sectionNo", "partNo", "slug", "durationMinutes"):
                    if _same_value(old_video.get(field), new_video.get(field)):
                        continue
                    if field == "durationMinutes" and key in target_tuples:
                        continue
                    differences.append(
                        _difference(
                            old_lesson,
                            f"video {key[0]}/{key[1]}.{field}",
                            old_video.get(field),
                            new_video.get(field),
                        )
                    )
            for key, new_video in new_videos.items():
                if key in old_videos:
                    continue
                target = target_tuples.get(key)
                if target is not None:
                    ready, value = target
                    expected_slug = value.get(
                        "videoSlug", ready.target.identity.video_slug
                    )
                    if norm(new_video.get("slug")) == norm(str(expected_slug)) and new_video.get(
                        "durationMinutes"
                    ) == value.get("durationMinutes"):
                        continue
                differences.append(
                    _difference(old_lesson, f"video {key[0]}/{key[1]}", None, new_video)
                )

    for lesson_id, new_lesson in after_by_id.items():
        if lesson_id not in before_by_id:
            differences.append(
                _difference(new_lesson, "lesson", None, new_lesson)
            )
    return differences
