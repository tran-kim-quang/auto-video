from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
import sys
import threading

from .powerpoint import PowerPointExportError, count_pptx_slides
from .powerpoint import export_slides as export_powerpoint_slides


def export_slides(
    pptx: Path,
    slide_ids: Collection[int],
    target_dir: Path,
    width: int = 1280,
    height: int = 720,
    cancel_event: threading.Event | None = None,
) -> dict[int, Path]:
    pptx = Path(pptx)
    target_dir = Path(target_dir)
    requested = sorted(set(slide_ids))
    if not requested:
        raise PowerPointExportError("no slide IDs were requested")
    if width <= 0 or height <= 0:
        raise PowerPointExportError("export dimensions must be positive")
    slide_count = count_pptx_slides(pptx)
    missing = [slide_id for slide_id in requested if not 1 <= slide_id <= slide_count]
    if missing:
        raise PowerPointExportError(f"PPTX does not contain slide {missing[0]}")

    args = (pptx, requested, target_dir)
    kwargs = {"width": width, "height": height, "cancel_event": cancel_event}
    if sys.platform == "win32":
        return export_powerpoint_slides(*args, **kwargs)
    if sys.platform.startswith("linux"):
        return export_libreoffice_slides(*args, **kwargs)
    raise PowerPointExportError(f"unsupported operating system: {sys.platform}")


def export_libreoffice_slides(*args, **kwargs):
    from .libreoffice import export_slides

    return export_slides(*args, **kwargs)
