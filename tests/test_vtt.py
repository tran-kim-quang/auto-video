from __future__ import annotations

import re

from video_workflow.vtt import (
    format_vtt_timestamp,
    render_vtt_from_report,
    split_cue_texts,
)


def test_proportional_split_within_slide() -> None:
    vtt = render_vtt_from_report(
        [
            {
                "text": "Câu một. Câu hai dài hơn.",
                "start_ms": 0,
                "end_ms": 10000,
            }
        ]
    )

    assert vtt == (
        "WEBVTT\n\n"
        "1\n00:00:00.000 --> 00:00:03.500\nCâu một.\n\n"
        "2\n00:00:03.500 --> 00:00:10.000\nCâu hai dài hơn.\n\n"
    )


def test_slide_boundaries_exact() -> None:
    vtt = render_vtt_from_report(
        [
            {"text": "Một. Hai.", "start_ms": 0, "end_ms": 5000},
            {"text": "Ba. Bốn.", "start_ms": 5000, "end_ms": 12345},
        ]
    )

    assert " --> 00:00:05.000\n" in vtt
    assert " --> 00:00:12.345\n" in vtt
    assert re.findall(r"(?m)^(\d+)$", vtt) == ["1", "2", "3", "4"]


def test_long_sentence_split_under_limit() -> None:
    sentence = (
        "Đây là một mệnh đề khá dài với nhiều từ dùng để kiểm tra việc tách câu, "
        "và phần còn lại cũng đủ dài để vượt quá giới hạn chín mươi ký tự quy định."
    )

    parts = split_cue_texts(sentence)

    assert all(len(part) <= 90 for part in parts)
    assert " ".join(" ".join(parts).split()) == " ".join(sentence.split())


def test_long_sentence_without_punctuation_splits_at_space() -> None:
    words = [f"từ{i:02d}" for i in range(30)]
    sentence = " ".join(words)

    parts = split_cue_texts(sentence)

    assert len(parts) > 1
    assert all(len(part) <= 90 for part in parts)
    assert " ".join(parts).split() == words


def test_short_cue_merged() -> None:
    vtt = render_vtt_from_report(
        [
            {
                "text": "A. Câu này dài hơn nhiều lắm đấy.",
                "start_ms": 0,
                "end_ms": 2000,
            }
        ]
    )

    assert re.findall(r"(?m)^(\d+)$", vtt) == ["1"]
    assert "A. Câu này dài hơn nhiều lắm đấy." in vtt


def test_newline_splits_and_escaping() -> None:
    vtt = render_vtt_from_report(
        [{"text": "x < y\nz & w", "start_ms": 0, "end_ms": 4000}]
    )

    assert "x &lt; y" in vtt
    assert "z &amp; w" in vtt
    assert re.findall(r"(?m)^(\d+)$", vtt) == ["1", "2"]


def test_timestamp_hours() -> None:
    assert format_vtt_timestamp(3_723_004) == "01:02:03.004"
