from __future__ import annotations

import json
import os
import struct
import threading
import wave
from dataclasses import replace
from pathlib import Path

import pytest

from video_workflow import narration
from video_workflow.narration import (
    NarrationCancelled,
    NarrationError,
    build_narration,
)
from video_workflow.narration_script import parse_slide_script_text
from video_workflow.voicestudio_client import (
    GenerationMetadata,
    VoiceStudioConfig,
    VoiceStudioOutOfMemory,
)


def _write_wav(path: Path, duration_ms: int, value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = duration_ms * 16
    frame = struct.pack("<h", value)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(frame * frame_count)


class _Client:
    def __init__(self, durations=None, failures=None, after_generate=None) -> None:
        self.durations = durations or {"Một.": 120, "Hai.": 180, "Ba.": 100}
        self.failures = {key: list(value) for key, value in (failures or {}).items()}
        self.after_generate = after_generate
        self.calls: list[tuple[str, int, str]] = []
        self.flushes = 0

    def generate(self, text, destination, config):
        self.calls.append((text, config.steps, config.profile_id))
        queued = self.failures.get(text, [])
        if queued:
            error = queued.pop(0)
            if error is not None:
                raise error
        _write_wav(Path(destination), self.durations[text], 1000 * len(self.calls))
        if self.after_generate is not None:
            self.after_generate(text)
        return GenerationMetadata(
            audio_id=f"audio-{len(self.calls)}",
            generation_seconds=0.25,
            audio_path=f"take-{len(self.calls)}.wav",
            seed=config.seed,
            audio_duration_seconds=self.durations[text] / 1000,
            routing_headers={"X-OmniVoice-Routing": "cuda"},
        )

    def flush_memory(self):
        self.flushes += 1
        return {"flushed": True}


def _segments(source: str = "Slide 1: Một. Slide 2: Hai."):
    return parse_slide_script_text(source)


def _build(
    tmp_path: Path,
    client=None,
    *,
    source="Slide 1: Một. Slide 2: Hai.",
    config=None,
):
    return build_narration(
        _segments(source),
        client or _Client(),
        tmp_path / "artifact",
        tmp_path / "cache",
        config or VoiceStudioConfig(),
    )


def test_builds_one_validated_wav_per_slide_and_contiguous_renderer_timeline(
    tmp_path: Path,
) -> None:
    progress = []
    result = build_narration(
        _segments(),
        _Client(),
        tmp_path / "artifact",
        tmp_path / "cache",
        VoiceStudioConfig(),
        on_progress=lambda current, total: progress.append((current, total)),
    )

    assert [slide.path.name for slide in result.slides] == [
        "slide_001.wav",
        "slide_002.wav",
    ]
    assert all(slide.path.is_file() for slide in result.slides)
    timeline = json.loads(result.timeline_path.read_text(encoding="utf-8"))
    assert timeline == [
        {"slide_id": 1, "start_ms": 0, "end_ms": 120, "duration_ms": 120},
        {"slide_id": 2, "start_ms": 120, "end_ms": 300, "duration_ms": 180},
    ]
    assert result.total_duration_ms == 300
    assert progress == [(1, 2), (2, 2)]


def test_combined_wav_orders_slides_without_inserted_gaps(tmp_path: Path) -> None:
    result = _build(tmp_path)

    with wave.open(str(result.combined_audio_path), "rb") as handle:
        assert handle.getframerate() == 16_000
        assert handle.getnframes() == 4_800
        first = struct.unpack("<h", handle.readframes(1))[0]
        handle.setpos(2_400)
        second = struct.unpack("<h", handle.readframes(1))[0]
    assert first == 1000
    assert second == 2000


def test_manifest_records_identity_media_hash_and_generation_metadata(
    tmp_path: Path,
) -> None:
    result = _build(tmp_path)

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["config"]["profile_id"] == "599fc048"
    assert manifest["config"]["steps"] == 32
    first = manifest["slides"][0]
    assert first["text"] == "Một."
    assert first["text_sha256"] == result.slides[0].text_sha256
    assert first["wav"] == "audio/slide_001.wav"
    assert first["size"] > 0
    assert first["sample_rate"] == 16_000
    assert first["channels"] == 1
    assert len(first["content_sha256"]) == 64
    assert first["generation"]["audio_id"] == "audio-1"
    assert first["generation"]["routing_headers"] == {
        "X-OmniVoice-Routing": "cuda"
    }


def test_unchanged_manifest_reuses_valid_wav_without_generation(
    tmp_path: Path,
) -> None:
    first_client = _Client()
    _build(tmp_path, first_client)
    second_client = _Client()

    _build(tmp_path, second_client)

    assert len(first_client.calls) == 2
    assert second_client.calls == []


def test_editing_one_text_regenerates_only_that_slide(tmp_path: Path) -> None:
    _build(tmp_path, _Client())
    client = _Client(durations={"Một.": 120, "Ba.": 100})

    _build(tmp_path, client, source="Slide 1: Một. Slide 2: Ba.")

    assert [call[0] for call in client.calls] == ["Ba."]


@pytest.mark.parametrize(
    "config",
    [
        replace(VoiceStudioConfig(), profile_id="new-profile"),
        replace(VoiceStudioConfig(), config_version="tts-v2"),
    ],
)
def test_profile_or_config_version_change_invalidates_all_slides(
    tmp_path: Path, config: VoiceStudioConfig
) -> None:
    _build(tmp_path, _Client())
    client = _Client()

    _build(tmp_path, client, config=config)

    assert [call[0] for call in client.calls] == ["Một.", "Hai."]


def test_corrupt_wav_or_partial_manifest_is_regenerated(tmp_path: Path) -> None:
    _build(tmp_path, _Client())
    (tmp_path / "cache" / "audio" / "slide_001.wav").write_bytes(b"broken")
    (tmp_path / "cache" / "audio-manifest.json").write_text("{", encoding="utf-8")
    client = _Client()

    _build(tmp_path, client)

    assert [call[0] for call in client.calls] == ["Một.", "Hai."]


def test_oom_flushes_and_retries_once_at_32_steps(tmp_path: Path) -> None:
    client = _Client(failures={"Một.": [VoiceStudioOutOfMemory("CUDA OOM"), None]})

    _build(tmp_path, client)

    assert client.flushes == 1
    assert client.calls[:2] == [
        ("Một.", 32, "599fc048"),
        ("Một.", 32, "599fc048"),
    ]


def test_second_oom_fails_without_quality_or_cpu_fallback(tmp_path: Path) -> None:
    client = _Client(
        failures={
            "Một.": [VoiceStudioOutOfMemory("first"), VoiceStudioOutOfMemory("second")]
        }
    )

    with pytest.raises(NarrationError, match="slide 1.*GPU memory"):
        _build(tmp_path, client)

    assert client.flushes == 1
    assert client.calls == [
        ("Một.", 32, "599fc048"),
        ("Một.", 32, "599fc048"),
    ]


def test_cancel_after_current_request_preserves_validated_cache_and_stops_next_slide(
    tmp_path: Path,
) -> None:
    cancelled = threading.Event()
    client = _Client(after_generate=lambda _text: cancelled.set())

    with pytest.raises(NarrationCancelled):
        build_narration(
            _segments(),
            client,
            tmp_path / "artifact",
            tmp_path / "cache",
            VoiceStudioConfig(),
            cancel_event=cancelled,
        )

    assert [call[0] for call in client.calls] == ["Một."]
    assert (tmp_path / "cache" / "audio" / "slide_001.wav").is_file()
    manifest = json.loads(
        (tmp_path / "cache" / "audio-manifest.json").read_text(encoding="utf-8")
    )
    assert [slide["slide_id"] for slide in manifest["slides"]] == [1]


def test_rename_failure_never_replaces_previous_validated_wav(
    tmp_path: Path, monkeypatch
) -> None:
    _build(tmp_path, _Client())
    cached = tmp_path / "cache" / "audio" / "slide_001.wav"
    original = cached.read_bytes()
    real_replace = os.replace

    def fail_audio_replace(source, destination):
        if Path(destination) == cached and Path(source).suffix == ".partial":
            raise OSError("disk rename failed")
        return real_replace(source, destination)

    monkeypatch.setattr(narration.os, "replace", fail_audio_replace)
    client = _Client(durations={"Mới.": 100, "Hai.": 180})

    with pytest.raises(NarrationError, match="disk rename failed"):
        _build(tmp_path, client, source="Slide 1: Mới. Slide 2: Hai.")

    assert cached.read_bytes() == original
