import importlib.util
from pathlib import Path

import pytest

import video_workflow.slide_export as slide_export
from video_workflow.powerpoint import PowerPointExportError


ROOT = Path(__file__).parents[1]
PPTX = ROOT / "test" / "TOAN7_C4_B12_T36_2_fixed.pptx"


def test_slide_export_facade_exists() -> None:
    assert importlib.util.find_spec("video_workflow.slide_export") is not None


def test_dispatches_to_powerpoint_on_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(slide_export.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(
        slide_export,
        "export_powerpoint_slides",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {},
    )

    slide_export.export_slides(PPTX, [2, 1, 2], tmp_path)

    assert calls[0][0][1] == [1, 2]


def test_dispatches_to_libreoffice_on_linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(slide_export.sys, "platform", "linux", raising=False)
    monkeypatch.setattr(
        slide_export,
        "export_libreoffice_slides",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {},
    )

    slide_export.export_slides(PPTX, [3], tmp_path)

    assert calls[0][0][1] == [3]


def test_rejects_missing_slide_before_backend_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slide_export.sys, "platform", "linux", raising=False)
    monkeypatch.setattr(
        slide_export,
        "export_libreoffice_slides",
        lambda *args, **kwargs: pytest.fail("backend started"),
    )
    with pytest.raises(PowerPointExportError, match="slide 21"):
        slide_export.export_slides(PPTX, [1, 21], tmp_path)


def test_rejects_unsupported_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slide_export.sys, "platform", "darwin", raising=False)

    with pytest.raises(PowerPointExportError, match="unsupported operating system"):
        slide_export.export_slides(PPTX, [1], tmp_path)
