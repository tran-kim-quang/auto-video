from __future__ import annotations

import copy
import unicodedata
from pathlib import Path

from video_workflow.web.production_sync import (
    SyncPlan,
    SyncTarget,
    import_ready_cc_manifest,
    import_ready_video_manifest,
    plan_production_sync,
    unconfirmed_targets,
    verify_changes,
    worker_observed,
)
from video_workflow.web.upload_export import ExportItem
from video_workflow.web.upload_identity import UploadIdentity


def _target(
    name: str,
    *,
    course_id: int = 601,
    lesson_slug: str | None = None,
    lesson_title: str | None = None,
    section_no: int = 48,
    part_no: int = 1,
    video_slug: str | None = None,
    video_title: str | None = None,
) -> SyncTarget:
    lesson_slug = lesson_slug or f"{name}-lesson"
    lesson_title = lesson_title or f"Bài {name}: Tên bài"
    video_slug = video_slug or f"{section_no}-{name}-video"
    video_title = video_title or f"Tiết {section_no} - {name}"
    triple = f"Toan6/Bài {name}_Tên bài/T{section_no}_{name}"
    identity = UploadIdentity(
        triple=triple,
        course_id=course_id,
        course_slug="601-toan-hoc-lop-6",
        lesson_slug=lesson_slug,
        lesson_order=int(name) if name.isdigit() else None,
        lesson_title=lesson_title,
        section_no=section_no,
        part_no=part_no,
        video_slug=video_slug,
        video_title=video_title,
    )
    return SyncTarget(
        relpath=f"root/{triple}",
        identity=identity,
        item=ExportItem(
            relpath=f"root/{triple}",
            triple=triple,
            video=Path(f"/{name}.mp4"),
            vtt=Path(f"/{name}.vtt"),
        ),
    )


def _lesson(
    lesson_id: str,
    *,
    title: str,
    slug: str,
    order: int,
    description: str | None = None,
    duration: int = 5,
    periods: list[dict] | None = None,
    videos: list[dict] | None = None,
) -> dict:
    return {
        "id": lesson_id,
        "courseId": 601,
        "title": title,
        "slug": slug,
        "order": order,
        "description": description,
        "durationMinutes": duration,
        "periods": periods or [],
        "videos": videos or [],
    }


def test_plan_matches_slug_then_falls_back_to_normalized_title() -> None:
    slug_target = _target("19", lesson_slug="19-bai-19-x")
    title_target = _target(
        "20", lesson_slug="20-bai-20-y", lesson_title="Bài 20: Café"
    )
    nfd_target = _target(
        "21",
        lesson_slug="21-bai-21-z",
        lesson_title=unicodedata.normalize("NFD", "Bài 21: Đối xứng"),
    )
    lessons = {
        601: [
            _lesson(
                "l19", title="Tên không dùng", slug="19-bai-19-x", order=29
            ),
            _lesson("l20", title="Bài 20: Café", slug="30-bai-20-y", order=30),
            _lesson(
                "l21", title="Bài 21: Đối xứng", slug="31-bai-21-z", order=31
            ),
        ]
    }

    plan = plan_production_sync([slug_target, title_target, nfd_target], lessons)

    assert [ready.lesson["id"] for ready in plan.ready] == ["l19", "l20", "l21"]
    assert plan.not_on_production == []
    assert plan.blocked == []


def test_plan_blocks_duplicate_titles_and_marks_missing_lesson() -> None:
    duplicate = _target("19", lesson_slug="parse-slug", lesson_title="Trùng tên")
    missing = _target("20", lesson_slug="missing", lesson_title="Không có")
    lessons = {
        601: [
            _lesson("a", title="Trùng tên", slug="prod-a", order=1),
            _lesson("b", title="Trùng tên", slug="prod-b", order=2),
        ]
    }

    plan = plan_production_sync([duplicate, missing], lessons)

    assert plan.blocked == [
        (duplicate, 'Nhiều bài trên production trùng tên "Trùng tên"')
    ]
    assert plan.not_on_production == [missing]


def test_plan_uses_three_block_reasons_in_required_order() -> None:
    described = _target("1")
    wrong_period = _target("2", section_no=49, video_title="Tiết mới")
    wrong_slug = _target("3", section_no=50, video_slug="50-moi")
    lessons = {
        601: [
            _lesson(
                "l1",
                title=described.identity.lesson_title,
                slug=described.identity.lesson_slug,
                order=1,
                description="Có mô tả",
                periods=[{"id": "p1", "order": 48, "title": "Tiết cũ"}],
                videos=[
                    {
                        "id": "v1",
                        "sectionNo": 48,
                        "partNo": 1,
                        "slug": "48-cu",
                        "durationMinutes": 5,
                    }
                ],
            ),
            _lesson(
                "l2",
                title=wrong_period.identity.lesson_title,
                slug=wrong_period.identity.lesson_slug,
                order=2,
                periods=[{"id": "p2", "order": 49, "title": "Tiết cũ"}],
            ),
            _lesson(
                "l3",
                title=wrong_slug.identity.lesson_title,
                slug=wrong_slug.identity.lesson_slug,
                order=3,
                periods=[{"id": "p3", "order": 50, "title": wrong_slug.identity.video_title}],
                videos=[
                    {
                        "id": "v3",
                        "sectionNo": 50,
                        "partNo": 1,
                        "slug": "50-cu",
                        "durationMinutes": 5,
                    }
                ],
            ),
        ]
    }

    plan = plan_production_sync([described, wrong_period, wrong_slug], lessons)

    assert plan.blocked == [
        (described, "Bài có mô tả trên production — import sẽ xoá mô tả"),
        (wrong_period, 'Tên tiết khác production: "Tiết mới" ≠ "Tiết cũ"'),
        (wrong_slug, 'Slug video khác production: "50-moi" ≠ "50-cu"'),
    ]


def test_plan_warns_for_multiple_parts_and_detects_overwrite() -> None:
    first = _target("19", part_no=1)
    second = _target("19", part_no=2, video_slug="48-2-video")
    lesson = _lesson(
        "l19",
        title=first.identity.lesson_title,
        slug=first.identity.lesson_slug,
        order=29,
        periods=[{"id": "p48", "order": 48, "title": "Tên cũ được phép"}],
        videos=[
            {
                "id": "v48",
                "sectionNo": 48,
                "partNo": 1,
                "slug": first.identity.video_slug,
                "durationMinutes": 5,
            }
        ],
    )

    plan = plan_production_sync([first, second], {601: [lesson]})

    assert [ready.overwrite for ready in plan.ready] == [True, False]
    assert [ready.warnings for ready in plan.ready] == [
        ["Tên tiết 48 sẽ đổi thành tên của phần xử lý sau cùng"],
        ["Tên tiết 48 sẽ đổi thành tên của phần xử lý sau cùng"],
    ]


def test_plan_blocks_distinct_sources_that_resolve_to_same_production_slot() -> None:
    first = _target(
        "19",
        lesson_slug="parse-lesson-a",
        lesson_title="Bài 19: Chung",
        video_slug="48-video-a",
    )
    second = _target(
        "20",
        lesson_slug="parse-lesson-b",
        lesson_title="Bài 19: Chung",
        video_slug="48-video-b",
    )
    lesson = _lesson(
        "production-19",
        title="Bài 19: Chung",
        slug="29-bai-19-chung",
        order=29,
        periods=[{"id": "p48", "order": 48, "title": first.identity.video_title}],
    )

    plan = plan_production_sync([first, second], {601: [lesson]})

    assert plan.ready == []
    assert plan.blocked == [
        (first, f"Trùng đích R2 với bài {second.relpath}"),
        (second, f"Trùng đích R2 với bài {first.relpath}"),
    ]


def test_import_ready_manifests_filter_and_only_replace_production_identity() -> None:
    target = _target("19", lesson_title="Bài 19 production")
    lesson = _lesson(
        "l19", title="Bài 19 production", slug="29-bai-19-production", order=29
    )
    plan = plan_production_sync([target], {601: [lesson]})
    original_video = {
        "relativePath": f"{target.identity.triple}.mp4",
        "courseId": 601,
        "lessonOrder": 19,
        "lessonSlug": "19-bai-19-x",
        "lessonTitle": "Bài 19 cũ",
        "sectionNo": 48,
        "partNo": 1,
        "videoSlug": target.identity.video_slug,
        "durationMinutes": 7,
        "videoKey": "videos/keep/master.m3u8",
    }
    video_manifest = {
        "generatedAt": "now",
        "videos": [original_video, {"relativePath": "Toan6/Bài 99/T99.mp4"}],
    }
    original_cc = {
        "relativePath": f"{target.identity.triple}/vi.vtt",
        "lessonSlug": "19-bai-19-x",
        "r2Key": "videos/keep/subtitles/vi.vtt",
        "linkUrl": "https://cdn/keep/vi.vtt",
    }
    cc_manifest = {
        "subtitles": [original_cc, {"relativePath": "Toan6/Bài 99/T99/vi.vtt"}]
    }

    ready_video = import_ready_video_manifest(video_manifest, plan)
    ready_cc = import_ready_cc_manifest(
        cc_manifest, plan, {target.identity.triple}
    )

    assert ready_video == {
        "generatedAt": "now",
        "videos": [
            {
                **original_video,
                "lessonOrder": 29,
                "lessonSlug": "29-bai-19-production",
                "lessonTitle": "Bài 19 production",
            }
        ],
    }
    assert ready_cc == {
        "subtitles": [
            {
                **original_cc,
                "lessonSlug": "29-bai-19-production",
            }
        ]
    }
    assert ready_cc["subtitles"][0]["r2Key"] == original_cc["r2Key"]
    assert video_manifest["videos"][0] == original_video


def test_worker_observed_requires_matching_target_video_and_duration() -> None:
    target = _target("19")
    before_lesson = _lesson(
        "l19", title=target.identity.lesson_title, slug=target.identity.lesson_slug, order=29
    )
    plan = plan_production_sync([target], {601: [before_lesson]})
    manifest = {
        "videos": [
            {
                "relativePath": f"{target.identity.triple}.mp4",
                "courseId": 601,
                "lessonSlug": target.identity.lesson_slug,
                "sectionNo": 48,
                "partNo": 1,
                "durationMinutes": 7,
            }
        ]
    }
    observed = copy.deepcopy(before_lesson)
    observed["videos"] = [
        {
            "id": "v48",
            "sectionNo": 48,
            "partNo": 1,
            "slug": target.identity.video_slug,
            "durationMinutes": 7,
        }
    ]

    assert worker_observed({601: [observed]}, plan, manifest) is True
    observed["videos"][0]["durationMinutes"] = 6
    assert worker_observed({601: [observed]}, plan, manifest) is False


def test_unconfirmed_targets_reports_unchanged_matching_duration() -> None:
    target = _target("19")
    base = _lesson(
        "l19",
        title=target.identity.lesson_title,
        slug=target.identity.lesson_slug,
        order=29,
        videos=[
            {
                "id": "v48",
                "sectionNo": 48,
                "partNo": 1,
                "slug": target.identity.video_slug,
                "durationMinutes": 7,
            }
        ],
    )
    plan = plan_production_sync([target], {601: [base]})
    manifest = {
        "videos": [
            {
                "relativePath": f"{target.identity.triple}.mp4",
                "courseId": 601,
                "lessonSlug": target.identity.lesson_slug,
                "sectionNo": 48,
                "partNo": 1,
                "durationMinutes": 7,
            }
        ]
    }

    assert unconfirmed_targets({601: [base]}, {601: [copy.deepcopy(base)]}, plan, manifest) == [
        target.relpath
    ]


def test_verify_changes_allows_expected_updates_and_flags_unrelated_or_slug_changes() -> None:
    target = _target("19")
    target_before = _lesson(
        "l19", title=target.identity.lesson_title, slug=target.identity.lesson_slug, order=29
    )
    other_before = _lesson("other", title="Bài khác", slug="bai-khac", order=30)
    before = {601: [target_before, other_before]}
    plan = plan_production_sync([target], before)
    manifest = {
        "videos": [
            {
                "relativePath": f"{target.identity.triple}.mp4",
                "courseId": 601,
                "lessonSlug": target.identity.lesson_slug,
                "sectionNo": 48,
                "partNo": 1,
                "videoSlug": target.identity.video_slug,
                "videoTitle": target.identity.video_title,
                "durationMinutes": 7,
            }
        ]
    }
    target_after = copy.deepcopy(target_before)
    target_after["durationMinutes"] = 7
    target_after["periods"] = [
        {"id": "p48", "order": 48, "title": target.identity.video_title}
    ]
    target_after["videos"] = [
        {
            "id": "v48",
            "sectionNo": 48,
            "partNo": 1,
            "slug": target.identity.video_slug,
            "durationMinutes": 7,
        }
    ]
    allowed_after = {601: [target_after, copy.deepcopy(other_before)]}

    assert verify_changes(before, allowed_after, plan, manifest) == []

    unrelated_after = copy.deepcopy(allowed_after)
    unrelated_after[601][1]["title"] = "Bài khác bị đổi"
    unrelated = verify_changes(before, unrelated_after, plan, manifest)
    assert "Bài 30 Bài khác: title 'Bài khác' → 'Bài khác bị đổi'" in unrelated

    slug_after = copy.deepcopy(allowed_after)
    slug_after[601][0]["slug"] = "slug-bi-doi"
    changed_slug = verify_changes(before, slug_after, plan, manifest)
    assert (
        "Bài 29 Bài 19: Tên bài: slug '19-lesson' → 'slug-bi-doi'"
        in changed_slug
    )


def test_verify_multipart_period_title_uses_last_manifest_entry() -> None:
    first = _target("19", part_no=1, video_title="Tên phần một")
    second = _target(
        "20",
        lesson_slug=first.identity.lesson_slug,
        lesson_title=first.identity.lesson_title,
        part_no=2,
        video_slug="48-phan-hai",
        video_title="Tên phần hai",
    )
    before_lesson = _lesson(
        "l19",
        title=first.identity.lesson_title,
        slug=first.identity.lesson_slug,
        order=29,
    )
    before = {601: [before_lesson]}
    plan = plan_production_sync([first, second], before)
    manifest = {
        "videos": [
            {
                "relativePath": f"{second.identity.triple}.mp4",
                "sectionNo": 48,
                "partNo": 2,
                "videoSlug": second.identity.video_slug,
                "videoTitle": second.identity.video_title,
                "durationMinutes": 4,
            },
            {
                "relativePath": f"{first.identity.triple}.mp4",
                "sectionNo": 48,
                "partNo": 1,
                "videoSlug": first.identity.video_slug,
                "videoTitle": first.identity.video_title,
                "durationMinutes": 3,
            },
        ]
    }
    after_lesson = copy.deepcopy(before_lesson)
    after_lesson["durationMinutes"] = 7
    after_lesson["periods"] = [
        {"id": "p48", "order": 48, "title": first.identity.video_title}
    ]
    after_lesson["videos"] = [
        {
            "id": "v48-1",
            "sectionNo": 48,
            "partNo": 1,
            "slug": first.identity.video_slug,
            "durationMinutes": 3,
        },
        {
            "id": "v48-2",
            "sectionNo": 48,
            "partNo": 2,
            "slug": second.identity.video_slug,
            "durationMinutes": 4,
        },
    ]

    assert verify_changes(before, {601: [after_lesson]}, plan, manifest) == []

    before_existing = copy.deepcopy(before_lesson)
    before_existing["periods"] = [
        {"id": "p48", "order": 48, "title": "Tên tiết cũ"}
    ]
    after_unrelated = copy.deepcopy(after_lesson)
    after_unrelated["periods"][0]["title"] = "Tên không có trong manifest"
    differences = verify_changes(
        {601: [before_existing]}, {601: [after_unrelated]}, plan, manifest
    )
    assert any("period 48.title" in value for value in differences)


def test_verify_ignores_canonically_equivalent_period_title_change() -> None:
    target = _target("19", video_title="Tiết 48 - Đối xứng")
    before_lesson = _lesson(
        "l19",
        title=target.identity.lesson_title,
        slug=target.identity.lesson_slug,
        order=29,
        periods=[
            {"id": "p48", "order": 48, "title": target.identity.video_title}
        ],
    )
    before = {601: [before_lesson]}
    plan = plan_production_sync([target], before)
    manifest = {
        "videos": [
            {
                "relativePath": f"{target.identity.triple}.mp4",
                "sectionNo": 48,
                "partNo": 1,
                "videoSlug": target.identity.video_slug,
                "videoTitle": target.identity.video_title,
                "durationMinutes": 5,
            }
        ]
    }
    after_lesson = copy.deepcopy(before_lesson)
    after_lesson["periods"][0]["title"] = unicodedata.normalize(
        "NFD", target.identity.video_title
    )

    assert verify_changes(before, {601: [after_lesson]}, plan, manifest) == []
