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


def test_parses_inline_markers_and_implicit_slide_one() -> None:
    segments = parse_slide_script_text("Mở đầu. Slide 2: Hai. Slide 3: Ba.")

    assert [(segment.slide_id, segment.text) for segment in segments] == [
        (1, "Mở đầu."),
        (2, "Hai."),
        (3, "Ba."),
    ]


def test_accepts_explicit_slide_one_and_multiline_text() -> None:
    segments = parse_slide_script_text(
        "Slide 1:\nDòng một.\nSlide 2: Dòng hai.\nDòng tiếp theo."
    )

    assert [segment.text for segment in segments] == [
        "Dòng một.",
        "Dòng hai.\nDòng tiếp theo.",
    ]


def test_preserves_math_semicolons_and_normalizes_unicode_nfc() -> None:
    segments = parse_slide_script_text("Điểm A (0; 6; 3). Slide 2: Cafe\u0301.")

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
        ("Preface Slide 1: A", "content before explicit Slide 1"),
    ],
)
def test_rejects_invalid_slide_sequences(source: str, message: str) -> None:
    with pytest.raises(ScriptError, match=message):
        parse_slide_script_text(source)


def test_requires_script_count_to_equal_pptx_count() -> None:
    segments = parse_slide_script_text("A. Slide 2: B.")

    with pytest.raises(ScriptError, match="PPTX has 3 slides.*script has 2"):
        validate_script_slide_count(segments, 3)


def test_load_slide_script_reads_utf8_strictly_and_reports_path(tmp_path: Path) -> None:
    valid = tmp_path / "lesson.txt"
    valid.write_text("Một. Slide 2: Hai.", encoding="utf-8")
    assert [segment.text for segment in load_slide_script(valid)] == ["Một.", "Hai."]

    invalid = tmp_path / "broken.txt"
    invalid.write_bytes(b"\xff")
    with pytest.raises(ScriptError, match=r"broken\.txt"):
        load_slide_script(invalid)


def test_validate_rejects_non_positive_pptx_slide_count() -> None:
    segments = parse_slide_script_text("Một.")

    with pytest.raises(ScriptError, match="positive slide count"):
        validate_script_slide_count(segments, 0)
