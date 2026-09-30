from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
import threading
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ElementTree


class PowerPointExportError(RuntimeError):
    """Raised when slides cannot be exported through PowerPoint."""


def count_pptx_slides(pptx: Path) -> int:
    pptx = Path(pptx)
    if not pptx.is_file():
        raise PowerPointExportError(f"PPTX file does not exist: {pptx}")
    try:
        with ZipFile(pptx) as archive:
            root = ElementTree.fromstring(archive.read("ppt/presentation.xml"))
    except (BadZipFile, KeyError, ElementTree.ParseError, OSError) as exc:
        raise PowerPointExportError(f"cannot read PPTX slide list: {exc}") from exc
    namespace = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main"}
    slide_list = root.find("p:sldIdLst", namespace)
    count = len(slide_list) if slide_list is not None else 0
    if count <= 0:
        raise PowerPointExportError("PPTX contains no slides")
    return count


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
    if not pptx.is_file():
        raise PowerPointExportError(f"PPTX file does not exist: {pptx}")
    requested = sorted(set(slide_ids))
    if not requested:
        raise PowerPointExportError("no slide IDs were requested")
    if width <= 0 or height <= 0:
        raise PowerPointExportError("export dimensions must be positive")

    try:
        import win32com.client
    except ImportError as exc:
        raise PowerPointExportError("pywin32 is required to automate Microsoft PowerPoint") from exc

    app = None
    presentation = None
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise PowerPointExportError("PowerPoint export cancelled")
        app = win32com.client.DispatchEx("PowerPoint.Application")
        presentation = app.Presentations.Open(
            str(pptx.resolve()),
            ReadOnly=True,
            Untitled=False,
            WithWindow=False,
        )
        slide_count = int(presentation.Slides.Count)
        missing = [slide_id for slide_id in requested if not 1 <= slide_id <= slide_count]
        if missing:
            raise PowerPointExportError(f"PPTX does not contain slide {missing[0]}")

        target_dir.mkdir(parents=True, exist_ok=True)
        exported: dict[int, Path] = {}
        for slide_id in requested:
            if cancel_event is not None and cancel_event.is_set():
                raise PowerPointExportError("PowerPoint export cancelled")
            output = (target_dir / f"slide_{slide_id:03d}.png").resolve()
            presentation.Slides.Item(slide_id).Export(str(output), "PNG", width, height)
            if not output.is_file() or output.stat().st_size == 0:
                raise PowerPointExportError(f"PowerPoint did not export slide {slide_id}")
            exported[slide_id] = output
            if cancel_event is not None and cancel_event.is_set():
                raise PowerPointExportError("PowerPoint export cancelled")
        return exported
    except PowerPointExportError:
        raise
    except Exception as exc:
        raise PowerPointExportError(f"PowerPoint export failed: {exc}") from exc
    finally:
        if presentation is not None:
            try:
                presentation.Close()
            except Exception:
                pass
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass
