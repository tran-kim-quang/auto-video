from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path

import pytest

from video_workflow.narration_script import (
    ScriptError,
    load_slide_script,
    parse_slide_script_text,
    validate_script_slide_count,
)


def test_discards_header_and_starts_at_slide_two_when_slide_one_is_absent() -> None:
    segments = parse_slide_script_text(
        "TIÊU ĐỀ KHÔNG ĐỌC\n=====\nSlide 2: Hai. Slide 3: Ba.\n====="
    )

    assert [(segment.slide_id, segment.text) for segment in segments] == [
        (2, "Hai."),
        (3, "Ba."),
    ]


def test_accepts_explicit_slide_one_and_multiline_text() -> None:
    segments = parse_slide_script_text(
        "HEADER\n====\nSlide 1:\nDòng một.\nSlide 2: Dòng hai.\nDòng tiếp theo.\n****"
    )

    assert [segment.text for segment in segments] == [
        "Dòng một.",
        "Dòng hai.\nDòng tiếp theo.",
    ]


def test_accepts_markers_without_space() -> None:
    segments = parse_slide_script_text("slide1:\nMột.\nslide2:\nHai.\nSLIDE3 : Ba.")

    assert [(segment.slide_id, segment.text) for segment in segments] == [
        (1, "Một."),
        (2, "Hai."),
        (3, "Ba."),
    ]


def test_preserves_math_semicolons_and_normalizes_unicode_nfc() -> None:
    segments = parse_slide_script_text(
        "Slide 1: Điểm A (0; 6; 3). Slide 2: Cafe\u0301."
    )

    assert segments[0].text == "Điểm A (0; 6; 3)."
    assert segments[1].text == "Café."
    assert unicodedata.is_normalized("NFC", segments[1].text)
    assert segments[1].text_sha256 == hashlib.sha256("Café.".encode()).hexdigest()


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("", "narration script is empty"),
        ("Slide 1: ", "slide 1 has empty narration"),
        ("Slide 1: A Slide 3: C", "expected slide 2"),
        ("Slide 1: A Slide 2: B Slide 2: C", "duplicate slide 2"),
        ("Slide 0: A", "slide identifiers must be positive"),
        ("Slide 3: A", "expected slide 1 or slide 2"),
    ],
)
def test_rejects_invalid_slide_sequences(source: str, message: str) -> None:
    with pytest.raises(ScriptError, match=message):
        parse_slide_script_text(source)


def test_requires_script_range_to_end_at_final_pptx_slide() -> None:
    segments = parse_slide_script_text("Slide 1: A. Slide 2: B.")

    with pytest.raises(ScriptError, match="must end at slide 3"):
        validate_script_slide_count(segments, 3)


def test_accepts_contiguous_script_starting_at_slide_two() -> None:
    segments = parse_slide_script_text("Header. Slide 2: B. Slide 3: C.")

    validate_script_slide_count(segments, 3)
    assert [segment.slide_id for segment in segments] == [2, 3]


def test_load_slide_script_reads_utf8_strictly_and_reports_path(tmp_path: Path) -> None:
    valid = tmp_path / "lesson.txt"
    valid.write_text("Slide 1: Một. Slide 2: Hai.", encoding="utf-8")
    assert [segment.text for segment in load_slide_script(valid)] == ["Một.", "Hai."]

    invalid = tmp_path / "broken.txt"
    invalid.write_bytes(b"\xff")
    with pytest.raises(ScriptError, match=r"broken\.txt"):
        load_slide_script(invalid)


def test_validate_rejects_non_positive_pptx_slide_count() -> None:
    segments = parse_slide_script_text("Một.")

    with pytest.raises(ScriptError, match="positive slide count"):
        validate_script_slide_count(segments, 0)
