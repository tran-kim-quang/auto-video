from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


class ScriptError(ValueError):
    """Raised when a narration script cannot map exactly to its slides."""


@dataclass(frozen=True, slots=True)
class SlideScriptSegment:
    slide_id: int
    text: str
    text_sha256: str


_SLIDE_MARKER = re.compile(r"\bslide\s+(-?\d+)\s*:", re.IGNORECASE)


def _segment(slide_id: int, text: str) -> SlideScriptSegment:
    normalized = unicodedata.normalize("NFC", text).strip()
    if not normalized:
        raise ScriptError(f"slide {slide_id} has empty narration")
    return SlideScriptSegment(
        slide_id=slide_id,
        text=normalized,
        text_sha256=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    )


def parse_slide_script_text(text: str) -> tuple[SlideScriptSegment, ...]:
    if not isinstance(text, str):
        raise ScriptError("narration script must be text")
    normalized = unicodedata.normalize("NFC", text)
    if not normalized.strip():
        raise ScriptError("narration script is empty")

    markers = list(_SLIDE_MARKER.finditer(normalized))
    if not markers:
        return (_segment(1, normalized),)

    marker_ids = [int(marker.group(1)) for marker in markers]
    if any(slide_id <= 0 for slide_id in marker_ids):
        raise ScriptError("slide identifiers must be positive")

    prefix = normalized[: markers[0].start()].strip()
    first_id = marker_ids[0]
    segments: list[SlideScriptSegment] = []
    if first_id == 1:
        if prefix:
            raise ScriptError("content before explicit Slide 1 marker is not allowed")
    elif first_id == 2:
        segments.append(_segment(1, prefix))
    else:
        raise ScriptError(f"expected slide 1 or slide 2, found slide {first_id}")

    expected_id = first_id
    previous_id: int | None = None
    for index, marker in enumerate(markers):
        slide_id = marker_ids[index]
        if previous_id == slide_id:
            raise ScriptError(f"duplicate slide {slide_id}")
        if slide_id != expected_id:
            raise ScriptError(f"expected slide {expected_id}, found slide {slide_id}")
        end = markers[index + 1].start() if index + 1 < len(markers) else len(normalized)
        segments.append(_segment(slide_id, normalized[marker.end() : end]))
        previous_id = slide_id
        expected_id += 1

    return tuple(segments)


def load_slide_script(path: Path) -> tuple[SlideScriptSegment, ...]:
    script_path = Path(path)
    try:
        source = script_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise ScriptError(f"cannot read narration script {script_path}: {exc}") from exc
    try:
        return parse_slide_script_text(source)
    except ScriptError as exc:
        raise ScriptError(f"invalid narration script {script_path}: {exc}") from exc


def validate_script_slide_count(
    segments: Sequence[SlideScriptSegment], slide_count: int
) -> None:
    if slide_count <= 0:
        raise ScriptError("PPTX must have a positive slide count")
    if len(segments) != slide_count:
        raise ScriptError(
            f"PPTX has {slide_count} slides but script has {len(segments)} narration segments"
        )
