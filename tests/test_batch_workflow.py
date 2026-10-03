from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest

from video_workflow import batch_workflow
from video_workflow.batch_workflow import BatchRequest
from video_workflow.probe import MediaInfo, MediaProbeError


def _lesson(root: Path, name: str) -> Path:
    lesson = root / name
    lesson.mkdir(parents=True)
    for filename in (
        f"P1_{name}_V8.mp4",
        f"P2_{name}_V8.mp4",
        f"{name}_1_Slide.pptx",
        f"{name}_2_Slide.pptx",
    ):
        (lesson / filename).write_bytes(b"source")
    return lesson


def _assets(root: Path) -> Path:
    root.mkdir()
    (root / "logo.png").write_bytes(b"logo")
    (root / "Outro720.mp4").write_bytes(b"outro")
    return root


def _media(duration: int = 4000) -> MediaInfo:
    return MediaInfo(
        duration_ms=duration,
        width=1280,
        height=720,
        fps=Fraction(24, 1),
        audio_duration_ms=duration,
        has_audio=True,
        video_codec="h264",
        audio_codec="aac",
    )


def _install_successful_stages(monkeypatch: pytest.MonkeyPatch, calls: list[object]) -> None:
    monkeypatch.setattr(batch_workflow, "probe_media", lambda _: _media())
    monkeypatch.setattr(batch_workflow, "count_pptx_slides", lambda _: 40)

    def merge_decks(_first, _second, output, image_dir):
        calls.append("pptx")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"merged")
        image_dir.mkdir(parents=True, exist_ok=True)
        images = []
        for number in range(1, 41):
            image = image_dir / f"slide-{number:02d}.png"
            image.write_bytes(b"png")
            images.append(image)
        return tuple(images)

    def align(_video, _images, slide_ids, timeline, report, _config):
        calls.append(("align", tuple(slide_ids)))
        timeline.write_text(
            json.dumps([
                {
                    "slide_id": slide_ids[0],
                    "start_ms": 0,
                    "end_ms": 4000,
                    "duration_ms": 4000,
                }
            ]),
            encoding="utf-8",
        )
        report.write_text("{}", encoding="utf-8")

    def build(request):
        calls.append(request)
        request.output.write_bytes(b"video")
        Path(f"{request.output}.report.json").write_text("{}", encoding="utf-8")

    def merge(request):
        calls.append(request)
        request.output.write_bytes(b"final")
        Path(f"{request.output}.report.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(batch_workflow, "merge_pptx_as_images", merge_decks)
    monkeypatch.setattr(batch_workflow, "align_video_to_slides", align)
    monkeypatch.setattr(batch_workflow, "build_video", build)
    monkeypatch.setattr(batch_workflow, "merge_videos", merge)


def test_valid_final_video_skips_every_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lesson = _lesson(tmp_path / "source", "T8")
    assets = _assets(tmp_path / "assets")
    final = lesson / "output" / "T8_final.mp4"
    final.parent.mkdir()
    final.write_bytes(b"valid")
    monkeypatch.setattr(batch_workflow, "probe_media", lambda _: _media())
    monkeypatch.setattr(
        batch_workflow,
        "merge_pptx_as_images",
        lambda *_args: pytest.fail("completed lesson was processed"),
    )

    report = batch_workflow.run_batch(BatchRequest(tmp_path / "source", assets))

    assert [(item.lesson, item.status) for item in report.results] == [("T8", "skipped")]


def test_full_flow_uses_logo_for_both_outro_only_for_part2_and_merges_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lesson(tmp_path / "source", "T8")
    assets = _assets(tmp_path / "assets")
    calls: list[object] = []
    _install_successful_stages(monkeypatch, calls)

    report = batch_workflow.run_batch(BatchRequest(tmp_path / "source", assets))

    builds = [item for item in calls if isinstance(item, batch_workflow.BuildRequest)]
    merges = [item for item in calls if isinstance(item, batch_workflow.MergeRequest)]
    assert len(builds) == 2
    assert builds[0].logo == assets / "logo.png" and builds[0].outro is None
    assert builds[1].logo == assets / "logo.png"
    assert builds[1].outro == assets / "Outro720.mp4"
    assert builds[0].timeline.name == "P1_T8_timeline.json"
    assert builds[1].timeline.name == "P2_T8_timeline.json"
    assert isinstance(calls[-1], batch_workflow.MergeRequest)
    assert len(merges) == 1
    assert report.results[0].status == "completed"


def test_corrupt_final_is_rebuilt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lesson = _lesson(tmp_path / "source", "T8")
    assets = _assets(tmp_path / "assets")
    final = lesson / "output" / "T8_final.mp4"
    final.parent.mkdir()
    final.write_bytes(b"corrupt")
    calls: list[object] = []
    _install_successful_stages(monkeypatch, calls)
    original_probe = batch_workflow.probe_media
    monkeypatch.setattr(
        batch_workflow,
        "probe_media",
        lambda path: (_ for _ in ()).throw(MediaProbeError("bad"))
        if Path(path) == final and final.read_bytes() == b"corrupt"
        else original_probe(path),
    )

    report = batch_workflow.run_batch(BatchRequest(tmp_path / "source", assets))

    assert report.results[0].status == "completed"
    assert final.read_bytes() == b"final"


def test_valid_intermediates_are_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lesson = _lesson(tmp_path / "source", "T8")
    assets = _assets(tmp_path / "assets")
    output = lesson / "output"
    output.mkdir()
    (output / "T8_Slide.pptx").write_bytes(b"merged")
    for number in range(1, 41):
        images = output / "slide-images"
        images.mkdir(exist_ok=True)
        (images / f"slide-{number:02d}.png").write_bytes(b"png")
    for part, slide_id in ((1, 1), (2, 21)):
        (output / f"P{part}_T8_timeline.json").write_text(
            json.dumps([{"slide_id": slide_id, "start_ms": 0, "end_ms": 4000}]),
            encoding="utf-8",
        )
        (output / f"P{part}_T8_slide.mp4").write_bytes(b"video")
    monkeypatch.setattr(batch_workflow, "probe_media", lambda _: _media())
    monkeypatch.setattr(batch_workflow, "count_pptx_slides", lambda _: 40)
    monkeypatch.setattr(
        batch_workflow, "merge_pptx_as_images", lambda *_: pytest.fail("merged deck rebuilt")
    )
    monkeypatch.setattr(
        batch_workflow, "align_video_to_slides", lambda *_: pytest.fail("timeline rebuilt")
    )
    monkeypatch.setattr(batch_workflow, "build_video", lambda *_: pytest.fail("part rebuilt"))
    monkeypatch.setattr(
        batch_workflow,
        "merge_videos",
        lambda request: request.output.write_bytes(b"final"),
    )

    report = batch_workflow.run_batch(BatchRequest(tmp_path / "source", assets))

    assert report.results[0].status == "completed"


def test_failure_in_one_lesson_does_not_stop_next(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _lesson(tmp_path / "source", "T8")
    _lesson(tmp_path / "source", "T9")
    assets = _assets(tmp_path / "assets")
    calls: list[object] = []
    _install_successful_stages(monkeypatch, calls)
    normal_merge = batch_workflow.merge_pptx_as_images

    def fail_t8(first, *args):
        if "T8" in str(first):
            raise RuntimeError("T8 failed")
        return normal_merge(first, *args)

    monkeypatch.setattr(batch_workflow, "merge_pptx_as_images", fail_t8)

    report = batch_workflow.run_batch(BatchRequest(tmp_path / "source", assets))

    assert [(item.lesson, item.status) for item in report.results] == [
        ("T8", "failed"),
        ("T9", "completed"),
    ]


def test_nested_lesson_result_uses_relative_path_and_is_reported_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson = _lesson(tmp_path / "source" / "Van8", "T8")
    assets = _assets(tmp_path / "assets")
    final = lesson / "output" / "T8_final.mp4"
    final.parent.mkdir()
    final.write_bytes(b"valid")
    monkeypatch.setattr(batch_workflow, "probe_media", lambda _: _media())
    emitted = []

    report = batch_workflow.run_batch(
        BatchRequest(tmp_path / "source", assets), on_result=emitted.append
    )

    assert [item.lesson for item in emitted] == ["Van8/T8"]
    assert report.results == emitted
