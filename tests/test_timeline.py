from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_workflow.timeline import TimelineError, parse_timeline, validate_timeline


FIXTURE = Path(__file__).parents[1] / "test" / "timeline_slide_TOAN7_C4_B12_T36_2.txt"


def test_parses_real_txt_timeline_and_tail() -> None:
    timeline = parse_timeline(FIXTURE)

    assert len(timeline.slides) == 20
    assert timeline.slides[0].slide_id == 1
    assert timeline.slides[-1].end_ms == 580_791
    assert timeline.tail == (580_791, 584_400)


def test_parses_json_with_reordered_and_repeated_slide_ids(tmp_path: Path) -> None:
    path = tmp_path / "timeline.json"
    path.write_text(
        json.dumps(
            [
                {"slide_id": 2, "start_ms": 0, "end_ms": 1000, "duration_ms": 1000},
                {"slide_id": 1, "start_ms": 1000, "end_ms": 2500, "duration_ms": 1500},
                {"slide_id": 2, "start_ms": 2500, "end_ms": 3000, "duration_ms": 500},
            ]
        ),
        encoding="utf-8",
    )

    timeline = parse_timeline(path)

    assert [cue.slide_id for cue in timeline.slides] == [2, 1, 2]


def test_rounds_absolute_boundaries_to_frames_without_drift() -> None:
    timeline = parse_timeline(FIXTURE)

    spans = validate_timeline(timeline, slide_count=20, audio_duration_ms=584_400, fps=24)

    assert spans[0].start_frame == 0
    assert spans[0].end_frame == 350
    assert spans[-1].end_frame == 13_939
    assert all(left.end_frame == right.start_frame for left, right in zip(spans, spans[1:]))


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([{"slide_id": 1, "start_ms": 0, "end_ms": 1000, "duration_ms": 950}], "duration"),
        ([{"slide_id": 1, "start_ms": 0, "end_ms": 1000}, {"slide_id": 2, "start_ms": 1100, "end_ms": 2000}], "gap or overlap"),
        ([{"slide_id": 3, "start_ms": 0, "end_ms": 1000}], "slide_id 3"),
        ([{"slide_id": 1, "start_ms": 0, "end_ms": 0}], "positive"),
    ],
)
def test_rejects_invalid_json_timeline(tmp_path: Path, rows: list[dict[str, int]], message: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(rows), encoding="utf-8")

    with pytest.raises(TimelineError, match=message):
        timeline = parse_timeline(path)
        validate_timeline(timeline, slide_count=2, audio_duration_ms=5000, fps=24)


def test_rejects_outro_before_last_slide(tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_text(
        "Slide 01: 00:00.000 --> 00:01.000\n"
        "Outro/blank: 00:01.000 --> 00:02.000\n"
        "Slide 02: 00:02.000 --> 00:03.000\n",
        encoding="utf-8",
    )

    with pytest.raises(TimelineError, match="final row"):
        parse_timeline(path)
