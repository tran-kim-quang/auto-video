from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from video_workflow import slide_alignment
from video_workflow.probe import MediaInfo
from video_workflow.timeline import parse_timeline


def _pattern(path: Path, side: str) -> Path:
    image = Image.new("RGB", (320, 180), "white")
    draw = ImageDraw.Draw(image)
    if side == "left":
        draw.rectangle((20, 20, 140, 160), fill="black")
    else:
        draw.ellipse((180, 20, 300, 160), fill="black")
    image.save(path)
    return path


def _media(duration_ms: int) -> MediaInfo:
    return MediaInfo(
        duration_ms=duration_ms,
        width=1280,
        height=720,
        fps=Fraction(24, 1),
        has_audio=True,
        audio_duration_ms=duration_ms,
        video_codec="h264",
        audio_codec="aac",
    )


def test_similarity_scores_identical_image_above_different_pattern(tmp_path: Path) -> None:
    left = Image.open(_pattern(tmp_path / "left.png", "left"))
    right = Image.open(_pattern(tmp_path / "right.png", "right"))

    identical = slide_alignment.image_similarity(left, left.copy())
    different = slide_alignment.image_similarity(left, right)

    assert identical == pytest.approx(1.0)
    assert different < 0.8


def test_similarity_detects_slide_embedded_above_notebook_caption(tmp_path: Path) -> None:
    slide_path = _pattern(tmp_path / "slide.png", "left")
    slide = Image.open(slide_path).resize((684, 382))
    notebook_frame = Image.new("RGB", (1280, 720), "white")
    notebook_frame.paste(slide, (298, 102))
    ImageDraw.Draw(notebook_frame).text((350, 550), "generated narration caption", fill="black")

    score = slide_alignment.image_similarity(notebook_frame, Image.open(slide_path))

    assert score > 0.9


def test_similarity_detects_smaller_slide_left_of_notebook_caption() -> None:
    slide = Image.new("RGB", (1280, 720), "#123b34")
    draw = ImageDraw.Draw(slide)
    draw.rectangle((100, 100, 1180, 620), outline="white", width=8)
    draw.text((180, 260), "lesson content", fill="white")
    notebook_frame = Image.new("RGB", (1280, 720), "white")
    notebook_frame.paste(slide.resize((448, 252)), (77, 235))
    ImageDraw.Draw(notebook_frame).text((700, 330), "large narration caption", fill="black")

    score = slide_alignment.image_similarity(notebook_frame, slide)

    assert score > 0.9


def test_monotonic_matching_never_moves_back_to_an_earlier_slide(tmp_path: Path) -> None:
    left = Image.open(_pattern(tmp_path / "left.png", "left"))
    right = Image.open(_pattern(tmp_path / "right.png", "right"))
    scores = slide_alignment.monotonic_matches([left, right, left], [left, right])

    assert [match.slide_index for match in scores] == sorted(
        match.slide_index for match in scores
    )


def test_debounce_removes_short_interior_false_transition() -> None:
    matches = [
        slide_alignment.SampleMatch(0, 0, 0.98),
        slide_alignment.SampleMatch(1000, 1, 0.75),
        slide_alignment.SampleMatch(2000, 0, 0.97),
        slide_alignment.SampleMatch(3000, 0, 0.98),
    ]

    cleaned = slide_alignment.debounce_matches(matches, min_stable_ms=1500)

    assert [match.slide_index for match in cleaned] == [0, 0, 0, 0]


def test_alignment_writes_contiguous_part2_timeline_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slide1 = _pattern(tmp_path / "slide1.png", "left")
    slide2 = _pattern(tmp_path / "slide2.png", "right")
    frames = [
        (0, Image.open(slide1)),
        (1000, Image.open(slide1)),
        (2000, Image.open(slide2)),
        (3000, Image.open(slide2)),
    ]
    monkeypatch.setattr(slide_alignment, "probe_media", lambda _: _media(4000))
    monkeypatch.setattr(
        slide_alignment,
        "sample_video_frames",
        lambda *_args, **_kwargs: frames,
    )
    timeline_path = tmp_path / "timeline.json"
    report_path = tmp_path / "report.json"

    report = slide_alignment.align_video_to_slides(
        tmp_path / "video.mp4",
        [slide1, slide2],
        [21, 22],
        timeline_path,
        report_path,
        slide_alignment.AlignmentConfig(min_stable_ms=1000),
    )

    payload = json.loads(timeline_path.read_text(encoding="utf-8"))
    assert payload == [
        {"slide_id": 21, "start_ms": 0, "end_ms": 2000, "duration_ms": 2000},
        {"slide_id": 22, "start_ms": 2000, "end_ms": 4000, "duration_ms": 2000},
    ]
    parsed = parse_timeline(timeline_path)
    assert parsed.slides[0].start_ms == 0
    assert parsed.slides[0].end_ms == parsed.slides[1].start_ms
    assert report.timeline_path == str(timeline_path.resolve())
    assert json.loads(report_path.read_text(encoding="utf-8"))["slide_ids"] == [21, 22]


def test_alignment_rejects_low_confidence_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slide = _pattern(tmp_path / "slide.png", "left")
    unrelated = Image.new("RGB", (320, 180), "gray")
    monkeypatch.setattr(slide_alignment, "probe_media", lambda _: _media(2000))
    monkeypatch.setattr(
        slide_alignment,
        "sample_video_frames",
        lambda *_args, **_kwargs: [(0, unrelated), (1000, unrelated)],
    )

    with pytest.raises(slide_alignment.AlignmentError, match="confidence"):
        slide_alignment.align_video_to_slides(
            tmp_path / "video.mp4",
            [slide],
            [1],
            tmp_path / "timeline.json",
            tmp_path / "report.json",
        )


def test_alignment_refines_coarse_transition_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slide1 = _pattern(tmp_path / "slide1.png", "left")
    slide2 = _pattern(tmp_path / "slide2.png", "right")
    left = Image.open(slide1)
    right = Image.open(slide2)

    def samples(_video, _duration, interval, _target, **_kwargs):
        if interval == 1000:
            return [(0, left), (1000, left), (2000, right), (3000, right)]
        return [(1000, left), (1500, right), (1900, right)]

    monkeypatch.setattr(slide_alignment, "probe_media", lambda _: _media(4000))
    monkeypatch.setattr(slide_alignment, "sample_video_frames", samples)
    timeline_path = tmp_path / "timeline.json"

    slide_alignment.align_video_to_slides(
        tmp_path / "video.mp4",
        [slide1, slide2],
        [1, 2],
        timeline_path,
        tmp_path / "report.json",
        slide_alignment.AlignmentConfig(min_stable_ms=100),
    )

    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    assert timeline[0]["end_ms"] == 1500
    assert timeline[1]["start_ms"] == 1500
