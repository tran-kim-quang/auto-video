from __future__ import annotations

from fractions import Fraction
from pathlib import Path
import threading

import pytest

from video_workflow.powerpoint import PowerPointExportError, count_pptx_slides, export_slides
from video_workflow.probe import MediaProbeError, probe_media


ROOT = Path(__file__).parents[1]
SOURCE_VIDEO = ROOT / "test" / "TOAN7_C4_B12_T36_2.mp4"
OUTRO_VIDEO = ROOT / "test" / "outro.mp4"
PPTX = ROOT / "test" / "TOAN7_C4_B12_T36_2_fixed.pptx"


def test_counts_slides_from_real_pptx_without_opening_powerpoint() -> None:
    assert count_pptx_slides(PPTX) == 20


def test_probes_real_source_video_and_audio() -> None:
    info = probe_media(SOURCE_VIDEO)

    assert (info.width, info.height) == (1280, 720)
    assert info.fps == Fraction(24, 1)
    assert info.has_audio is True
    assert info.duration_ms == pytest.approx(584_400, abs=2)
    assert info.audio_duration_ms == pytest.approx(584_400, abs=2)


def test_probes_portrait_outro() -> None:
    info = probe_media(OUTRO_VIDEO)

    assert (info.width, info.height) == (720, 1280)
    assert info.has_audio is True
    assert info.duration_ms == pytest.approx(7701, abs=2)


def test_rejects_missing_media_file(tmp_path: Path) -> None:
    with pytest.raises(MediaProbeError, match="does not exist"):
        probe_media(tmp_path / "missing.mp4")


class _FakeSlide:
    def __init__(self, slide_id: int, calls: list[tuple]) -> None:
        self.slide_id = slide_id
        self.calls = calls

    def Export(self, path: str, kind: str, width: int, height: int) -> None:
        self.calls.append(("export", self.slide_id, path, kind, width, height))
        Path(path).write_bytes(b"png")


class _FakeSlides:
    Count = 3

    def __init__(self, calls: list[tuple]) -> None:
        self.calls = calls

    def Item(self, slide_id: int) -> _FakeSlide:
        return _FakeSlide(slide_id, self.calls)


class _FakePresentation:
    def __init__(self, calls: list[tuple]) -> None:
        self.Slides = _FakeSlides(calls)
        self.calls = calls

    def Close(self) -> None:
        self.calls.append(("close",))


class _FakePresentations:
    def __init__(self, calls: list[tuple], presentation: _FakePresentation) -> None:
        self.calls = calls
        self.presentation = presentation

    def Open(self, path: str, ReadOnly: bool, Untitled: bool, WithWindow: bool) -> _FakePresentation:
        self.calls.append(("open", path, ReadOnly, Untitled, WithWindow))
        return self.presentation


class _FakePowerPoint:
    def __init__(self, calls: list[tuple]) -> None:
        self.calls = calls
        self.presentation = _FakePresentation(calls)
        self.Presentations = _FakePresentations(calls, self.presentation)
        self.Visible = True

    def Quit(self) -> None:
        self.calls.append(("quit",))


class _RejectHiddenPowerPoint(_FakePowerPoint):
    @property
    def Visible(self) -> bool:
        return True

    @Visible.setter
    def Visible(self, value: bool) -> None:
        if value is False:
            raise RuntimeError("Hiding the application window is not allowed")


def test_exports_unique_requested_slides_and_preserves_unicode_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple] = []
    app = _FakePowerPoint(calls)
    monkeypatch.setattr("win32com.client.DispatchEx", lambda _: app)
    pptx = tmp_path / "Bài giảng có dấu.pptx"
    pptx.write_bytes(b"pptx")
    target = tmp_path / "ảnh tạm"

    result = export_slides(pptx, [3, 1, 3], target)

    assert list(result) == [1, 3]
    assert calls[0] == ("open", str(pptx.resolve()), True, False, False)
    exports = [call for call in calls if call[0] == "export"]
    assert [call[1] for call in exports] == [1, 3]
    assert all(call[3:] == ("PNG", 1280, 720) for call in exports)
    assert calls[-2:] == [("close",), ("quit",)]


def test_exports_when_powerpoint_rejects_hiding_the_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple] = []
    app = _RejectHiddenPowerPoint(calls)
    monkeypatch.setattr("win32com.client.DispatchEx", lambda _: app)
    pptx = tmp_path / "slides.pptx"
    pptx.write_bytes(b"pptx")

    result = export_slides(pptx, [1], tmp_path / "images")

    assert result[1].is_file()
    assert calls[0] == ("open", str(pptx.resolve()), True, False, False)


def test_rejects_missing_slide_before_any_export_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple] = []
    app = _FakePowerPoint(calls)
    monkeypatch.setattr("win32com.client.DispatchEx", lambda _: app)
    pptx = tmp_path / "slides.pptx"
    pptx.write_bytes(b"pptx")

    with pytest.raises(PowerPointExportError, match="slide 4"):
        export_slides(pptx, [1, 4], tmp_path / "images")

    assert not [call for call in calls if call[0] == "export"]
    assert calls[-2:] == [("close",), ("quit",)]


def test_cancellation_between_slide_exports_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple] = []
    event = threading.Event()
    app = _FakePowerPoint(calls)
    original_item = app.presentation.Slides.Item

    def item(slide_id: int):
        slide = original_item(slide_id)
        original_export = slide.Export

        def export(*args):
            original_export(*args)
            event.set()

        slide.Export = export
        return slide

    app.presentation.Slides.Item = item
    monkeypatch.setattr("win32com.client.DispatchEx", lambda _: app)
    pptx = tmp_path / "slides.pptx"
    pptx.write_bytes(b"pptx")

    with pytest.raises(PowerPointExportError, match="cancelled"):
        export_slides(pptx, [1, 2], tmp_path / "images", cancel_event=event)

    assert [call[1] for call in calls if call[0] == "export"] == [1]
    assert calls[-2:] == [("close",), ("quit",)]
