from __future__ import annotations

import json
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from video_workflow.cli import main
from video_workflow.pipeline import BuildRequest, WorkflowError, build_video
from video_workflow.probe import MediaInfo


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "test"


def _request(tmp_path: Path) -> BuildRequest:
    return BuildRequest(
        source_media=FIXTURES / "TOAN7_C4_B12_T36_2.mp4",
        pptx=FIXTURES / "TOAN7_C4_B12_T36_2_fixed.pptx",
        timeline=FIXTURES / "timeline_slide_TOAN7_C4_B12_T36_2.txt",
        logo=FIXTURES / "logo.png",
        outro=FIXTURES / "outro.mp4",
        output=tmp_path / "final.mp4",
    )


def _media(
    duration_ms: int,
    *,
    width: int = 1280,
    height: int = 720,
    audio_duration_ms: int | None = None,
    has_audio: bool = True,
) -> MediaInfo:
    return MediaInfo(
        duration_ms=duration_ms,
        width=width,
        height=height,
        fps=Fraction(24),
        audio_duration_ms=duration_ms if audio_duration_ms is None and has_audio else audio_duration_ms,
        has_audio=has_audio,
        video_codec="h264",
        audio_codec="aac" if has_audio else None,
    )


def test_rejects_missing_input_before_media_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(tmp_path)
    request = replace(request, logo=tmp_path / "missing.png")
    called = False

    def unexpected_probe(_: Path) -> MediaInfo:
        nonlocal called
        called = True
        raise AssertionError("probe should not run")

    monkeypatch.setattr("video_workflow.pipeline.probe_media", unexpected_probe)

    with pytest.raises(WorkflowError, match="logo.*does not exist"):
        build_video(request)

    assert called is False


def test_rejects_short_source_audio_before_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(tmp_path)
    monkeypatch.setattr(
        "video_workflow.pipeline.probe_media",
        lambda _: _media(1000, audio_duration_ms=1000),
    )
    monkeypatch.setattr("video_workflow.pipeline.count_pptx_slides", lambda _: 20)
    exported = False

    def unexpected_export(*args, **kwargs):
        nonlocal exported
        exported = True
        raise AssertionError("export should not run")

    monkeypatch.setattr("video_workflow.pipeline.export_slides", unexpected_export)

    with pytest.raises(WorkflowError, match="shorter"):
        build_video(request)

    assert exported is False


def test_existing_output_is_not_overwritten(tmp_path: Path) -> None:
    request = _request(tmp_path)
    request.output.write_bytes(b"keep-me")

    with pytest.raises(WorkflowError, match="already exists"):
        build_video(request)

    assert request.output.read_bytes() == b"keep-me"


def test_phase_failure_publishes_no_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(tmp_path)
    monkeypatch.setattr("video_workflow.pipeline.count_pptx_slides", lambda _: 20)
    monkeypatch.setattr(
        "video_workflow.pipeline.probe_media",
        lambda path: _media(584_400) if Path(path) == request.source_media else _media(7701, width=720, height=1280),
    )

    def fail_export(*args, **kwargs):
        raise RuntimeError("PowerPoint failed")

    monkeypatch.setattr("video_workflow.pipeline.export_slides", fail_export)

    with pytest.raises(WorkflowError, match="PowerPoint failed"):
        build_video(request)

    assert not request.output.exists()
    assert not Path(f"{request.output}.report.json").exists()


def test_success_publishes_verified_video_and_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(tmp_path)
    source_info = _media(584_400)
    outro_info = _media(7701, width=720, height=1280)
    final_info = _media(588_492)
    monkeypatch.setattr("video_workflow.pipeline.count_pptx_slides", lambda _: 20)

    def fake_probe(path: Path) -> MediaInfo:
        path = Path(path)
        if path == request.source_media:
            return source_info
        if path == request.outro:
            return outro_info
        return final_info

    monkeypatch.setattr("video_workflow.pipeline.probe_media", fake_probe)

    def fake_export(_pptx, slide_ids, target, **_kwargs):
        result = {}
        Path(target).mkdir(parents=True, exist_ok=True)
        for slide_id in sorted(set(slide_ids)):
            path = Path(target) / f"slide_{slide_id:03d}.png"
            path.write_bytes(b"png")
            result[slide_id] = path
        return result

    monkeypatch.setattr("video_workflow.pipeline.export_slides", fake_export)
    monkeypatch.setattr(
        "video_workflow.pipeline.render_lecture",
        lambda *args, **kwargs: Path(args[4]).write_bytes(b"lecture"),
    )
    monkeypatch.setattr(
        "video_workflow.pipeline.normalize_outro",
        lambda *args, **kwargs: Path(args[1]).write_bytes(b"outro"),
    )
    monkeypatch.setattr(
        "video_workflow.pipeline.join_parts",
        lambda *args, **kwargs: Path(args[2]).write_bytes(b"final"),
    )

    stages: list[str] = []
    report = build_video(request, on_stage=stages.append)

    assert request.output.read_bytes() == b"final"
    report_path = Path(f"{request.output}.report.json")
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert report.slide_count == payload["slide_count"] == 20
    assert payload["lecture_end_ms"] == 580_791
    assert payload["trimmed_source_tail_ms"] == 3609
    assert payload["expected_duration_ms"] == 588_492
    assert payload["checks"] == ["video_1280x720", "video_24fps", "h264_aac_streams", "duration"]
    assert stages == [
        "validating", "exporting_slides", "rendering_lecture",
        "preparing_outro", "joining", "verifying",
    ]


def test_cli_accepts_all_five_inputs_and_unicode_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "kết quả có dấu.mp4"
    captured: list[BuildRequest] = []

    def fake_build(request: BuildRequest):
        captured.append(request)
        return object()

    monkeypatch.setattr("video_workflow.cli.build_video", fake_build)

    exit_code = main(
        [
            "--video",
            str(FIXTURES / "TOAN7_C4_B12_T36_2.mp4"),
            "--pptx",
            str(FIXTURES / "TOAN7_C4_B12_T36_2_fixed.pptx"),
            "--timeline",
            str(FIXTURES / "timeline_slide_TOAN7_C4_B12_T36_2.txt"),
            "--logo",
            str(FIXTURES / "logo.png"),
            "--outro",
            str(FIXTURES / "outro.mp4"),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert captured[0].output == output
    assert captured[0].source_media == FIXTURES / "TOAN7_C4_B12_T36_2.mp4"
    assert captured[0].margin_px == 0


def test_cli_accepts_source_media_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[BuildRequest] = []
    monkeypatch.setattr("video_workflow.cli.build_video", lambda request: captured.append(request) or object())
    args = [
        "--source-media", str(FIXTURES / "TOAN7_C4_B12_T36_2.mp4"),
        "--pptx", str(FIXTURES / "TOAN7_C4_B12_T36_2_fixed.pptx"),
        "--timeline", str(FIXTURES / "timeline_slide_TOAN7_C4_B12_T36_2.txt"),
        "--logo", str(FIXTURES / "logo.png"), "--outro", str(FIXTURES / "outro.mp4"),
        "--output", str(tmp_path / "new.mp4"),
    ]
    assert main(args) == 0
    assert captured[0].source_media == FIXTURES / "TOAN7_C4_B12_T36_2.mp4"


@pytest.mark.parametrize(
    ("use_logo", "use_outro"),
    [(True, False), (False, True), (False, False)],
)
def test_optional_asset_combinations_skip_only_missing_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_logo: bool,
    use_outro: bool,
) -> None:
    original = _request(tmp_path)
    request = replace(
        original,
        logo=original.logo if use_logo else None,
        outro=original.outro if use_outro else None,
    )
    lecture_end_ms = 580_791
    outro_duration_ms = 7701 if use_outro else 0
    final_info = _media(lecture_end_ms + outro_duration_ms)
    monkeypatch.setattr("video_workflow.pipeline.count_pptx_slides", lambda _: 20)

    def fake_probe(path: Path) -> MediaInfo:
        path = Path(path)
        if path == request.source_media:
            return _media(584_400)
        if request.outro is not None and path == request.outro:
            return _media(7701, width=720, height=1280)
        return final_info

    monkeypatch.setattr("video_workflow.pipeline.probe_media", fake_probe)

    def fake_export(_pptx, slide_ids, target, **_kwargs):
        result = {}
        Path(target).mkdir(parents=True, exist_ok=True)
        for slide_id in sorted(set(slide_ids)):
            path = Path(target) / f"slide_{slide_id:03d}.png"
            path.write_bytes(b"png")
            result[slide_id] = path
        return result

    rendered_logos = []
    calls = []
    monkeypatch.setattr("video_workflow.pipeline.export_slides", fake_export)

    def fake_render(*args, **_kwargs):
        rendered_logos.append(args[3])
        Path(args[4]).write_bytes(b"lecture")

    def fake_normalize(*args, **_kwargs):
        calls.append("normalize")
        Path(args[1]).write_bytes(b"outro")

    def fake_join(*args, **_kwargs):
        calls.append("join")
        Path(args[2]).write_bytes(b"final")

    monkeypatch.setattr("video_workflow.pipeline.render_lecture", fake_render)
    monkeypatch.setattr("video_workflow.pipeline.normalize_outro", fake_normalize)
    monkeypatch.setattr("video_workflow.pipeline.join_parts", fake_join)

    stages: list[str] = []
    report = build_video(request, on_stage=stages.append)

    assert rendered_logos == [request.logo]
    assert calls == (["normalize", "join"] if use_outro else [])
    assert request.output.read_bytes() == (b"final" if use_outro else b"lecture")
    assert report.inputs["logo"] == (str(request.logo.resolve()) if use_logo else None)
    assert report.inputs["outro"] == (str(request.outro.resolve()) if use_outro else None)
    assert report.outro_duration_ms == outro_duration_ms
    assert report.expected_duration_ms == lecture_end_ms + outro_duration_ms
    assert ("preparing_outro" in stages) is use_outro
    assert ("joining" in stages) is use_outro


def test_cli_allows_logo_and_outro_to_be_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[BuildRequest] = []
    monkeypatch.setattr(
        "video_workflow.cli.build_video",
        lambda request: captured.append(request) or object(),
    )

    exit_code = main(
        [
            "--video",
            str(FIXTURES / "TOAN7_C4_B12_T36_2.mp4"),
            "--pptx",
            str(FIXTURES / "TOAN7_C4_B12_T36_2_fixed.pptx"),
            "--timeline",
            str(FIXTURES / "timeline_slide_TOAN7_C4_B12_T36_2.txt"),
            "--output",
            str(tmp_path / "no-assets.mp4"),
        ]
    )

    assert exit_code == 0
    assert captured[0].logo is None
    assert captured[0].outro is None
