from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])\s+|\n+")


def _split_long_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    midpoint = len(text) / 2
    punctuation_breaks = [
        index + 1
        for index, character in enumerate(text)
        if character in ";:," and index + 1 < len(text)
    ]
    breaks = punctuation_breaks or [
        index
        for index, character in enumerate(text)
        if character.isspace() and 0 < index < len(text) - 1
    ]
    if not breaks:
        return [text]

    split_at = min(breaks, key=lambda index: (abs(index - midpoint), index))
    left = text[:split_at].strip()
    right = text[split_at:].strip()
    if not left or not right:
        return [text]
    return _split_long_text(left, max_chars) + _split_long_text(right, max_chars)


def split_cue_texts(text: str, max_chars: int = 90) -> list[str]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    sentences = [
        " ".join(part.split())
        for part in _SENTENCE_BREAK.split(normalized)
        if part.strip()
    ]
    parts: list[str] = []
    for sentence in sentences:
        parts.extend(_split_long_text(sentence, max_chars))
    return parts


def format_vtt_timestamp(ms: int) -> str:
    milliseconds = max(0, int(ms))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _merge_short_cues(
    cues: list[tuple[int, int, str]], min_cue_ms: int
) -> list[tuple[int, int, str]]:
    merged = list(cues)
    while len(merged) > 1:
        short_index = next(
            (
                index
                for index, (start, end, _text) in enumerate(merged)
                if end - start < min_cue_ms
            ),
            None,
        )
        if short_index is None:
            break
        start, end, text = merged[short_index]
        if short_index < len(merged) - 1:
            next_start, next_end, next_text = merged[short_index + 1]
            merged[short_index : short_index + 2] = [
                (start, next_end, f"{text} {next_text}")
            ]
        else:
            previous_start, _previous_end, previous_text = merged[short_index - 1]
            merged[short_index - 1 :] = [
                (previous_start, end, f"{previous_text} {text}")
            ]
    return merged


def _escape_vtt_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_vtt_from_report(
    slides: Sequence[Mapping[str, Any]],
    *,
    max_chars: int = 90,
    min_cue_ms: int = 600,
) -> str:
    all_cues: list[tuple[int, int, str]] = []
    for slide in slides:
        start = int(slide["start_ms"])
        end = int(slide["end_ms"])
        if end <= start:
            continue
        texts = split_cue_texts(str(slide["text"]), max_chars=max_chars)
        if not texts:
            continue
        weights = [len("".join(text.split())) or 1 for text in texts]
        total = sum(weights)
        accumulated = 0
        bounds = [start]
        for weight in weights:
            accumulated += weight
            bounds.append(start + round((end - start) * accumulated / total))
        bounds[-1] = end
        slide_cues = [
            (bounds[index], bounds[index + 1], text)
            for index, text in enumerate(texts)
        ]
        all_cues.extend(_merge_short_cues(slide_cues, min_cue_ms))

    result = ["WEBVTT\n\n"]
    for index, (start, end, text) in enumerate(all_cues, start=1):
        result.append(
            f"{index}\n"
            f"{format_vtt_timestamp(start)} --> {format_vtt_timestamp(end)}\n"
            f"{_escape_vtt_text(text)}\n\n"
        )
    return "".join(result)
