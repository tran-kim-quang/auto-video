from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
import shutil
import subprocess
import threading
from tempfile import TemporaryDirectory

from .powerpoint import PowerPointExportError


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
    office = shutil.which("libreoffice") or shutil.which("soffice")
    if office is None:
        raise PowerPointExportError(
            "LibreOffice is unavailable; install the Ubuntu package libreoffice-impress-nogui"
        )
    pdftocairo = shutil.which("pdftocairo")
    if pdftocairo is None:
        raise PowerPointExportError(
            "pdftocairo is unavailable; install the Ubuntu package poppler-utils"
        )

    target_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="libreoffice-export-", dir=target_dir.parent) as temporary:
        work_dir = Path(temporary)
        profile_dir = work_dir / "profile"
        profile_dir.mkdir()
        office_argv = [
            office,
            "--headless",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
            "--convert-to",
            "pdf:impress_pdf_Export",
            "--outdir",
            str(work_dir),
            str(pptx.resolve()),
        ]
        _run_process(office_argv, "LibreOffice conversion", cancel_event)

        pdfs = [path for path in work_dir.glob("*.pdf") if path.is_file() and path.stat().st_size > 0]
        if len(pdfs) != 1:
            raise PowerPointExportError(
                f"LibreOffice conversion created {len(pdfs)} non-empty PDF files; expected 1"
            )
        pdf_path = pdfs[0]
        exported: dict[int, Path] = {}
        for slide_id in requested:
            output = (target_dir / f"slide_{slide_id:03d}.png").resolve()
            output.unlink(missing_ok=True)
            prefix = output.with_suffix("")
            poppler_argv = [
                pdftocairo,
                "-png",
                "-singlefile",
                "-f",
                str(slide_id),
                "-l",
                str(slide_id),
                "-scale-to-x",
                str(width),
                "-scale-to-y",
                str(height),
                str(pdf_path),
                str(prefix),
            ]
            _run_process(poppler_argv, "Poppler slide rasterization", cancel_event)
            if not output.is_file() or output.stat().st_size == 0:
                raise PowerPointExportError(f"Poppler did not export slide {slide_id}")
            exported[slide_id] = output
        return exported


def _run_process(
    argv: list[str],
    action: str,
    cancel_event: threading.Event | None,
) -> tuple[str, str]:
    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
    )
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.1)
            break
        except subprocess.TimeoutExpired:
            if cancel_event is None or not cancel_event.is_set():
                continue
            process.terminate()
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise PowerPointExportError(f"{action} cancelled")

    if process.returncode != 0:
        detail = (stderr or stdout or f"process exited with {process.returncode}")[-2000:]
        raise PowerPointExportError(f"{action} failed: {detail}")
    return stdout, stderr
