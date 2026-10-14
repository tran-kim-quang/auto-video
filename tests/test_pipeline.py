from __future__ import annotations

import json
import os
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from video_workflow import pipeline
from video_workflow.narration import AudioArtifact, NarrationCancelled, NarrationResult
from video_workflow.pipeline import (
    NarratedBuildRequest,
    WorkflowCancelled,
    WorkflowError,
    _verify_final,
    build_narrated_video,
)
from video_workflow.probe import MediaInfo
from video_workflow.voicestudio_client import VoiceStudioConfig


def _media(
    duration_ms: int,
    *,
    width: int | None = 1280,
    height: int | None = 720,
    has_audio: bool = True,
) -> MediaInfo:
    return MediaInfo(
        duration_ms=duration_ms,
        width=width,
        height=height,
        fps=Fraction(24) if width is not None else None,
        audio_duration_ms=duration_ms if has_audio else None,
        has_audio=has_audio,
        video_codec="h264" if width is not None else None,
        audio_codec="aac" if has_audio and width is not None else "pcm_s16le",
        sample_rate=48_000 if has_audio else None,
        channels=2 if has_audio else None,
    )


def _request(tmp_path: Path) -> NarratedBuildRequest:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    pptx = inputs / "Bài học.pptx"
    script = inputs / "Bài học.txt"
    logo = inputs / "logo.png"
    outro = inputs / "outro.mp4"
    pptx.write_bytes(b"pptx-v1")
    script.write_text("Một. Slide 2: Hai.", encoding="utf-8")
    logo.write_bytes(b"logo-v1")
    outro.write_bytes(b"outro-v1")
    return NarratedBuildRequest(
        pptx=pptx,
        script=script,
        logo=logo,
        outro=outro,
        artifact_directory=tmp_path / "output" / "Bài học",
        profile_config=VoiceStudioConfig(),
    )


def _fake_narration(artifact_directory: Path) -> NarrationResult:
    artifact_directory = Path(artifact_directory)
    audio = artifact_directory / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    slides = []
    cursor = 0
    timeline = []
    manifest_slides = []
    for slide_id, text, duration in ((1, "Một.", 120), (2, "Hai.", 180)):
        wav = audio / f"slide_{slide_id:03d}.wav"
        wav.write_bytes(f"wav-{slide_id}".encode())
        end = cursor + duration
        timeline.append(
            {
                "slide_id": slide_id,
                "start_ms": cursor,
                "end_ms": end,
                "duration_ms": duration,
            }
        )
        manifest_slides.append(
            {
                "slide_id": slide_id,
                "text": text,
                "text_sha256": f"text-{slide_id}",
                "wav": f"audio/{wav.name}",
                "size": wav.stat().st_size,
                "sample_rate": 16_000,
                "channels": 1,
                "duration_ms": duration,
                "content_sha256": f"audio-{slide_id}",
                "generation": {"audio_id": f"id-{slide_id}"},
            }
        )
        slides.append(
            AudioArtifact(
                slide_id=slide_id,
                text=text,
                text_sha256=f"text-{slide_id}",
                path=wav,
                start_ms=cursor,
                end_ms=end,
                duration_ms=duration,
                size=wav.stat().st_size,
                sample_rate=16_000,
                channels=1,
                content_sha256=f"audio-{slide_id}",
                generation_metadata={"audio_id": f"id-{slide_id}"},
            )
        )
        cursor = end
    timeline_path = artifact_directory / "timeline.json"
    timeline_path.write_text(json.dumps(timeline), encoding="utf-8")
    combined = artifact_directory / "combined.wav"
    combined.write_bytes(b"combined")
    manifest = artifact_directory / "audio-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "config": {"profile_id": "599fc048", "steps": 32},
                "slides": manifest_slides,
            }
        ),
        encoding="utf-8",
    )
    return NarrationResult(tuple(slides), timeline_path, combined, manifest, cursor)


def _patch_success(monkeypatch, request: NarratedBuildRequest):
    calls = {"narration": 0, "render": [], "normalize": 0, "join": 0}
    monkeypatch.setattr(pipeline, "count_pptx_slides", lambda _path: 2)

    def build_narration(_segments, _client, artifact, _cache, _config, **_kwargs):
        calls["narration"] += 1
        return _fake_narration(Path(artifact))

    def export(_pptx, slide_ids, target, **_kwargs):
        target = Path(target)
        target.mkdir(parents=True, exist_ok=True)
        result = {}
        for slide_id in slide_ids:
            image = target / f"slide_{slide_id:03d}.png"
            image.write_bytes(f"png-{slide_id}".encode())
            result[slide_id] = image
        return result

    def render(slides, spans, narration_audio, logo, target, **_kwargs):
        calls["render"].append((narration_audio, logo, len(slides), len(spans)))
        Path(target).write_bytes(b"lecture")

    def normalize(_source, target, **_kwargs):
        calls["normalize"] += 1
        Path(target).write_bytes(b"normalized-outro")

    def join(_lecture, _outro, target, **_kwargs):
        calls["join"] += 1
        Path(target).write_bytes(b"final-video")

    def probe(path):
        path = Path(path)
        if path == request.outro:
            return _media(500, width=720, height=1280)
        return _media(800)

    monkeypatch.setattr(pipeline, "build_narration", build_narration)
    monkeypatch.setattr(pipeline, "export_slides", export)
    monkeypatch.setattr(pipeline, "render_lecture", render)
    monkeypatch.setattr(pipeline, "normalize_outro", normalize)
    monkeypatch.setattr(pipeline, "join_parts", join)
    monkeypatch.setattr(pipeline, "probe_media", probe)
    return calls


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_build_publishes_complete_artifact_tree(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    _patch_success(monkeypatch, request)

    report = build_narrated_video(request, client=object(), cache_root=tmp_path / "cache")

    root = request.artifact_directory
    assert (root / "Bài học.mp4").read_bytes() == b"final-video"
    assert (root / "report.json").is_file()
    assert (root / "timeline.json").is_file()
    assert (root / "combined.wav").is_file()
    assert (root / "audio-manifest.json").is_file()
    assert sorted(path.name for path in (root / "audio").iterdir()) == [
        "slide_001.wav",
        "slide_002.wav",
    ]
    assert sorted(path.name for path in (root / "slide-images").iterdir()) == [
        "slide_001.png",
        "slide_002.png",
    ]
    assert report.slide_count == 2


def test_logo_is_on_lecture_and_outro_is_appended_exactly_once(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request(tmp_path)
    calls = _patch_success(monkeypatch, request)

    build_narrated_video(request, client=object(), cache_root=tmp_path / "cache")

    assert len(calls["render"]) == 1
    narration_audio, logo, image_count, span_count = calls["render"][0]
    assert Path(narration_audio).name == "combined.wav"
    assert logo == request.logo
    assert (image_count, span_count) == (2, 2)
    assert calls["normalize"] == 1
    assert calls["join"] == 1


def test_report_contains_input_hashes_profile_settings_slide_times_and_checks(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request(tmp_path)
    _patch_success(monkeypatch, request)

    report = build_narrated_video(request, client=object(), cache_root=tmp_path / "cache")
    payload = json.loads(
        (request.artifact_directory / "report.json").read_text(encoding="utf-8")
    )

    assert payload["inputs"]["pptx"]["path"] == str(request.pptx.resolve())
    assert len(payload["inputs"]["pptx"]["sha256"]) == 64
    assert payload["profile_config"]["profile_id"] == "599fc048"
    assert payload["profile_config"]["steps"] == 32
    assert [(row["start_ms"], row["end_ms"]) for row in payload["slides"]] == [
        (0, 120),
        (120, 300),
    ]
    assert payload["total_narration_duration_ms"] == 300
    assert payload["outro_duration_ms"] == 500
    assert payload["expected_duration_ms"] == report.actual_duration_ms == 800
    assert payload["checks"] == [
        "video_1280x720",
        "video_24fps",
        "h264_aac_streams",
        "duration",
    ]


def test_matching_signature_and_valid_artifacts_skip_all_build_steps(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request(tmp_path)
    calls = _patch_success(monkeypatch, request)
    first = build_narrated_video(request, client=object(), cache_root=tmp_path / "cache")
    monkeypatch.setattr(
        pipeline,
        "build_narration",
        lambda *_args, **_kwargs: pytest.fail("matching completed output must be skipped"),
    )

    second = build_narrated_video(request, client=object(), cache_root=tmp_path / "cache")

    assert second.input_signature == first.input_signature
    assert calls["narration"] == 1


@pytest.mark.parametrize("missing", ["combined.wav", "audio/slide_002.wav"])
def test_stale_or_missing_artifact_rebuilds(
    tmp_path: Path, monkeypatch, missing: str
) -> None:
    request = _request(tmp_path)
    calls = _patch_success(monkeypatch, request)
    build_narrated_video(request, client=object(), cache_root=tmp_path / "cache")
    (request.artifact_directory / missing).unlink()

    build_narrated_video(request, client=object(), cache_root=tmp_path / "cache")

    assert calls["narration"] == 2


def test_changed_script_signature_rebuilds(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    calls = _patch_success(monkeypatch, request)
    build_narrated_video(request, client=object(), cache_root=tmp_path / "cache")
    request.script.write_text("Mới. Slide 2: Hai.", encoding="utf-8")

    build_narrated_video(request, client=object(), cache_root=tmp_path / "cache")

    assert calls["narration"] == 2


def test_failed_or_cancelled_staging_preserves_previous_published_directory(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request(tmp_path)
    _patch_success(monkeypatch, request)
    build_narrated_video(request, client=object(), cache_root=tmp_path / "cache")
    before = _snapshot(request.artifact_directory)
    request.script.write_text("Mới. Slide 2: Hai.", encoding="utf-8")
    monkeypatch.setattr(
        pipeline,
        "build_narration",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(NarrationCancelled("stop")),
    )

    with pytest.raises(WorkflowCancelled, match="stop"):
        build_narrated_video(request, client=object(), cache_root=tmp_path / "cache")

    assert _snapshot(request.artifact_directory) == before


def test_promotion_failure_rolls_back_previous_directory(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request(tmp_path)
    _patch_success(monkeypatch, request)
    build_narrated_video(request, client=object(), cache_root=tmp_path / "cache")
    before = _snapshot(request.artifact_directory)
    request.script.write_text("Mới. Slide 2: Hai.", encoding="utf-8")
    real_replace = os.replace

    def fail_staging_promotion(source, destination):
        source_path, destination_path = Path(source), Path(destination)
        if (
            destination_path == request.artifact_directory
            and ".staging-" in source_path.name
        ):
            raise OSError("publish rename failed")
        return real_replace(source, destination)

    monkeypatch.setattr(pipeline.os, "replace", fail_staging_promotion)

    with pytest.raises(WorkflowError, match="publish rename failed"):
        build_narrated_video(request, client=object(), cache_root=tmp_path / "cache")

    assert _snapshot(request.artifact_directory) == before


@pytest.mark.parametrize("field", ["pptx", "script", "logo", "outro"])
def test_changed_input_before_publish_aborts_as_stale(
    tmp_path: Path, monkeypatch, field: str
) -> None:
    request = _request(tmp_path)
    _patch_success(monkeypatch, request)
    target = Path(getattr(request, field))
    real_join = pipeline.join_parts

    def join_then_mutate(*args, **kwargs):
        real_join(*args, **kwargs)
        target.write_bytes(target.read_bytes() + b"changed")

    monkeypatch.setattr(pipeline, "join_parts", join_then_mutate)

    with pytest.raises(WorkflowError, match="inputs changed while job was running"):
        build_narrated_video(request, client=object(), cache_root=tmp_path / "cache")
    assert not request.artifact_directory.exists()


def test_requires_all_four_inputs_before_work(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    request.logo.unlink()
    monkeypatch.setattr(
        pipeline,
        "build_narration",
        lambda *_args, **_kwargs: pytest.fail("must validate before synthesis"),
    )

    with pytest.raises(WorkflowError, match="logo file does not exist"):
        build_narrated_video(request, client=object(), cache_root=tmp_path / "cache")


def test_final_validation_accepts_container_fps_rounding() -> None:
    info = replace(_media(800), fps=Fraction(151164, 6299))

    assert "video_24fps" in _verify_final(info, expected_duration_ms=800, fps=24)
