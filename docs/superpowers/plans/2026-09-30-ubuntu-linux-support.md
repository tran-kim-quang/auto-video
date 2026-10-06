# Ubuntu Linux Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a complete Ubuntu Desktop setup and runtime path while preserving the existing Windows PowerPoint workflow.

**Architecture:** `video_workflow.slide_export` validates requests and dispatches to the existing PowerPoint COM backend on Windows or a new LibreOffice/Poppler backend on Linux. Platform-specific process setup stays at the edges: the worker initializes COM only on Windows, the UI opens folders through the native command, and Ubuntu dependencies are installed by idempotent Bash scripts.

**Tech Stack:** Python 3.12+, pytest, Tkinter, PowerPoint COM/pywin32 on Windows, LibreOffice Impress headless, Poppler `pdftocairo`, FFmpeg/ffprobe, Bash, Ubuntu `apt`.

**Spec:** `docs/superpowers/specs/2026-09-30-linux-support-design.md`

## Global Constraints

- Support Ubuntu Desktop 24.04 LTS and 26.04 LTS with Python 3.12 or newer.
- Preserve `setup.bat`, `run-ui.bat`, `scripts/setup.ps1`, CLI options, queue storage and output/report formats.
- Windows must continue to export through a separate Microsoft PowerPoint COM instance.
- Linux must export static slides through LibreOffice headless and Poppler; PowerPoint animation, transition, embedded video and macros remain unsupported.
- Pass every external-program argument as an argv list with `shell=False`; Unicode, spaces and shell metacharacters must remain literal.
- Do not install licensed Microsoft fonts; document that exact source fonts are required for the closest layout match.
- Cancellation or phase failure must publish neither a final video nor a report.
- Unit tests must not require LibreOffice, Poppler, PowerPoint or a graphical display.

## Review Focus

- A PPTX path containing Vietnamese text, spaces and shell metacharacters must reach LibreOffice as one literal argv item; Task 2 pins this with `test_libreoffice_uses_literal_paths_and_isolated_profile`.
- LibreOffice may choose the PDF filename from the document title rather than the source stem; Task 2 requires exactly one new non-empty PDF instead of assuming a basename in `test_libreoffice_accepts_the_single_generated_pdf`.
- A cancelled child process may ignore `terminate()`; Task 2 verifies the timeout fallback calls `kill()` in `test_cancelled_process_is_killed_after_terminate_timeout`.
- Linux has no `os.startfile`; Task 3 verifies `xdg-open` receives the selected output directory in `test_open_output_folder_uses_xdg_open_on_linux`.
- `setup.sh --check-only` must never gain privilege or mutate the system, even with missing dependencies; Task 4 uses a fake `sudo` sentinel in `test_setup_check_only_never_calls_sudo`.

---

### Task 1: Add the platform-neutral slide-export facade

**Files:**
- Create: `video_workflow/slide_export.py`
- Create: `tests/test_slide_export.py`
- Modify: `video_workflow/pipeline.py:11-13`
- Preserve: `video_workflow/powerpoint.py`
- Test: `tests/test_probe_powerpoint.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `video_workflow.powerpoint.PowerPointExportError`, `count_pptx_slides(...)`, and `export_slides(...)`.
- Produces: `video_workflow.slide_export.count_pptx_slides(pptx: Path) -> int` and `export_slides(pptx, slide_ids, target_dir, width=1280, height=720, cancel_event=None) -> dict[int, Path]`.

- [ ] **Step 1: Write the failing module-existence test**

```python
import importlib.util


def test_slide_export_facade_exists() -> None:
    assert importlib.util.find_spec("video_workflow.slide_export") is not None
```

- [ ] **Step 2: Run the test and verify the expected failure**

Run: `python -m pytest tests/test_slide_export.py::test_slide_export_facade_exists -q`

Expected: FAIL because `video_workflow.slide_export` does not exist.

- [ ] **Step 3: Create the facade shell, then write failing dispatch and validation tests**

Create `video_workflow/slide_export.py` with the public signatures raising `NotImplementedError`, then add tests using the real PPTX fixture and monkeypatched backends:

```python
from pathlib import Path
import threading

import pytest

import video_workflow.slide_export as slide_export
from video_workflow.powerpoint import PowerPointExportError

ROOT = Path(__file__).parents[1]
PPTX = ROOT / "test" / "TOAN7_C4_B12_T36_2_fixed.pptx"


def test_dispatches_to_powerpoint_on_windows(tmp_path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(slide_export.sys, "platform", "win32")
    monkeypatch.setattr(slide_export, "export_powerpoint_slides", lambda *args, **kwargs: calls.append((args, kwargs)) or {})
    slide_export.export_slides(PPTX, [2, 1, 2], tmp_path)
    assert calls[0][0][1] == [1, 2]


def test_dispatches_to_libreoffice_on_linux(tmp_path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(slide_export.sys, "platform", "linux")
    monkeypatch.setattr(slide_export, "export_libreoffice_slides", lambda *args, **kwargs: calls.append((args, kwargs)) or {})
    slide_export.export_slides(PPTX, [3], tmp_path)
    assert calls[0][0][1] == [3]


def test_rejects_missing_slide_before_backend_start(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(slide_export.sys, "platform", "linux")
    monkeypatch.setattr(slide_export, "export_libreoffice_slides", lambda *args, **kwargs: pytest.fail("backend started"))
    with pytest.raises(PowerPointExportError, match="slide 21"):
        slide_export.export_slides(PPTX, [1, 21], tmp_path)


def test_rejects_unsupported_platform(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(slide_export.sys, "platform", "darwin")
    with pytest.raises(PowerPointExportError, match="unsupported operating system"):
        slide_export.export_slides(PPTX, [1], tmp_path)
```

- [ ] **Step 4: Run the new behavior tests and verify they fail for the missing behavior**

Run: `python -m pytest tests/test_slide_export.py -q`

Expected: FAIL because the facade still raises `NotImplementedError` or lacks the backend attributes.

- [ ] **Step 5: Implement validation and dispatch with lazy-safe backend imports**

Use the existing PPTX counter and error type, normalize slide IDs once, validate before backend startup, and dispatch without importing pywin32:

```python
from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
import sys
import threading

from .powerpoint import PowerPointExportError, count_pptx_slides
from .powerpoint import export_slides as export_powerpoint_slides


def export_libreoffice_slides(*args, **kwargs):
    from .libreoffice import export_slides
    return export_slides(*args, **kwargs)


def export_slides(pptx, slide_ids, target_dir, width=1280, height=720, cancel_event=None):
    pptx, target_dir = Path(pptx), Path(target_dir)
    requested = sorted(set(slide_ids))
    if not requested:
        raise PowerPointExportError("no slide IDs were requested")
    if width <= 0 or height <= 0:
        raise PowerPointExportError("export dimensions must be positive")
    count = count_pptx_slides(pptx)
    missing = [slide_id for slide_id in requested if not 1 <= slide_id <= count]
    if missing:
        raise PowerPointExportError(f"PPTX does not contain slide {missing[0]}")
    args = (pptx, requested, target_dir)
    kwargs = {"width": width, "height": height, "cancel_event": cancel_event}
    if sys.platform == "win32":
        return export_powerpoint_slides(*args, **kwargs)
    if sys.platform.startswith("linux"):
        return export_libreoffice_slides(*args, **kwargs)
    raise PowerPointExportError(f"unsupported operating system: {sys.platform}")
```

Keep `count_pptx_slides` imported at module scope so its public facade name remains available.

- [ ] **Step 6: Point the pipeline at the facade and run focused regressions**

Change only the import in `video_workflow/pipeline.py`:

```python
from .slide_export import count_pptx_slides, export_slides
```

Run: `python -m pytest tests/test_slide_export.py tests/test_probe_powerpoint.py tests/test_pipeline.py -q`

Expected: facade and all existing Windows/pipeline tests PASS.

- [ ] **Step 7: Commit Task 1**

```bash
git add video_workflow/slide_export.py video_workflow/pipeline.py tests/test_slide_export.py
git commit -m "refactor: dispatch slide export by platform"
```

### Task 2: Implement the LibreOffice and Poppler backend

**Files:**
- Create: `video_workflow/libreoffice.py`
- Create: `tests/test_libreoffice.py`
- Modify: `tests/test_slide_export.py`

**Interfaces:**
- Consumes: validated, sorted slide IDs from `video_workflow.slide_export.export_slides` and `PowerPointExportError` from `video_workflow.powerpoint`.
- Produces: `video_workflow.libreoffice.export_slides(pptx, slide_ids, target_dir, width=1280, height=720, cancel_event=None) -> dict[int, Path]` and private `_run_process(argv, action, cancel_event) -> tuple[str, str]`.

- [ ] **Step 1: Write the failing backend-existence test**

```python
import importlib.util


def test_libreoffice_backend_exists() -> None:
    assert importlib.util.find_spec("video_workflow.libreoffice") is not None
```

Run: `python -m pytest tests/test_libreoffice.py::test_libreoffice_backend_exists -q`

Expected: FAIL because the backend module does not exist.

- [ ] **Step 2: Create a minimal backend shell and write failing command-construction tests**

Create the module with `export_slides` raising `NotImplementedError`. Add a fake process runner that writes one PDF after the LibreOffice command and the requested PNG after each Poppler command:

```python
def test_libreoffice_uses_literal_paths_and_isolated_profile(tmp_path, monkeypatch) -> None:
    pptx = tmp_path / "Bài giảng $(không chạy).pptx"
    pptx.write_bytes(b"pptx")
    calls = []

    def fake_run(argv, action, cancel_event):
        calls.append((argv, action))
        if action == "LibreOffice conversion":
            Path(argv[argv.index("--outdir") + 1], "generated.pdf").write_bytes(b"pdf")
        else:
            Path(f"{argv[-1]}.png").write_bytes(b"png")
        return "", ""

    monkeypatch.setattr("video_workflow.libreoffice.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("video_workflow.libreoffice._run_process", fake_run)
    result = export_slides(pptx, [2, 1, 2], tmp_path / "ảnh")
    office_argv = calls[0][0]
    assert str(pptx.resolve()) in office_argv
    assert any(item.startswith("-env:UserInstallation=file:") for item in office_argv)
    assert list(result) == [1, 2]


def test_libreoffice_accepts_the_single_generated_pdf(tmp_path, monkeypatch) -> None:
    pptx = tmp_path / "deck.with.dots.pptx"
    pptx.write_bytes(b"pptx")

    def fake_run(argv, action, cancel_event):
        if action == "LibreOffice conversion":
            Path(argv[argv.index("--outdir") + 1], "title-from-metadata.pdf").write_bytes(b"pdf")
        else:
            Path(f"{argv[-1]}.png").write_bytes(b"png")
        return "", ""

    monkeypatch.setattr("video_workflow.libreoffice.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("video_workflow.libreoffice._run_process", fake_run)
    assert export_slides(pptx, [1], tmp_path / "images")[1].name == "slide_001.png"


@pytest.mark.parametrize(("missing", "message"), [("office", "libreoffice-impress-nogui"), ("pdftocairo", "poppler-utils")])
def test_missing_tool_names_the_ubuntu_package(tmp_path, monkeypatch, missing, message) -> None:
    pptx = tmp_path / "slides.pptx"
    pptx.write_bytes(b"pptx")
    def fake_which(name):
        if missing == "office" and name in {"libreoffice", "soffice"}:
            return None
        if name == missing:
            return None
        return f"/usr/bin/{name}"
    monkeypatch.setattr("video_workflow.libreoffice.shutil.which", fake_which)
    with pytest.raises(PowerPointExportError, match=message):
        export_slides(pptx, [1], tmp_path / "images")
```

- [ ] **Step 3: Run the command tests and verify the expected failures**

Run: `python -m pytest tests/test_libreoffice.py -q`

Expected: FAIL because Linux conversion is not implemented.

- [ ] **Step 4: Implement tool discovery, isolated conversion and per-page rasterization**

Implement these concrete command shapes:

```python
office_argv = [
    office, "--headless", "--nologo", "--nodefault", "--nofirststartwizard",
    f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
    "--convert-to", "pdf:impress_pdf_Export", "--outdir", str(work_dir),
    str(pptx.resolve()),
]

png_prefix = (target_dir / f"slide_{slide_id:03d}").resolve()
poppler_argv = [
    pdftocairo, "-png", "-singlefile", "-f", str(slide_id), "-l", str(slide_id),
    "-scale-to-x", str(width), "-scale-to-y", str(height),
    str(pdf_path), str(png_prefix),
]
```

Use `TemporaryDirectory(prefix="libreoffice-export-", dir=target_dir.parent)` for the PDF and profile. After conversion, select exactly one non-empty `*.pdf`; zero or multiple candidates raise `PowerPointExportError`. Remove an existing target PNG before rasterization and verify a new non-empty PNG after the process exits.

- [ ] **Step 5: Write failing process error and cancellation tests**

```python
def test_process_failure_includes_stderr(monkeypatch) -> None:
    process = FakeProcess(returncode=7, stderr="filter failed")
    monkeypatch.setattr("video_workflow.libreoffice.subprocess.Popen", lambda *args, **kwargs: process)
    with pytest.raises(PowerPointExportError, match="filter failed"):
        _run_process(["soffice"], "LibreOffice conversion", None)


def test_cancelled_process_is_killed_after_terminate_timeout(monkeypatch) -> None:
    event = threading.Event()
    event.set()
    process = FakeProcess(returncode=None, timeout_after_terminate=True)
    monkeypatch.setattr("video_workflow.libreoffice.subprocess.Popen", lambda *args, **kwargs: process)
    with pytest.raises(PowerPointExportError, match="cancelled"):
        _run_process(["soffice"], "LibreOffice conversion", event)
    assert process.terminated is True
    assert process.killed is True
```

`FakeProcess` is a test-only class implementing `communicate(timeout)`, `terminate()`, `kill()` and `returncode`; it returns captured text after success/failure and raises `subprocess.TimeoutExpired` while simulating a running process.

- [ ] **Step 6: Implement cancellable process execution**

Use `Popen(argv, stdout=PIPE, stderr=PIPE, text=True, shell=False)`. Repeatedly call `communicate(timeout=0.1)`; on `TimeoutExpired`, check cancellation. On cancellation, call `terminate()`, then `communicate(timeout=2)`, fall back to `kill()` and a final `communicate()`, and raise `PowerPointExportError(f"{action} cancelled")`. For non-zero exit, include the last 2000 characters of stderr, or stdout if stderr is empty.

- [ ] **Step 7: Verify backend and facade tests**

Run: `python -m pytest tests/test_libreoffice.py tests/test_slide_export.py tests/test_probe_powerpoint.py -q`

Expected: all tests PASS without requiring installed LibreOffice or Poppler.

- [ ] **Step 8: Commit Task 2**

```bash
git add video_workflow/libreoffice.py tests/test_libreoffice.py tests/test_slide_export.py
git commit -m "feat: export slides with LibreOffice on Linux"
```

### Task 3: Remove Windows-only runtime assumptions from worker and UI

**Files:**
- Modify: `video_workflow/worker.py:1-95`
- Modify: `video_workflow/ui.py:1-190`
- Modify: `tests/test_worker.py`
- Modify: `tests/test_ui.py`

**Interfaces:**
- Consumes: unchanged `QueueWorker`, `WorkflowApp` and queue-controller APIs.
- Produces: `_com_context()` in `worker.py` and `_open_directory(path: Path) -> None` in `ui.py`.

- [ ] **Step 1: Write the failing Linux import test before changing worker imports**

```python
def test_worker_imports_without_pythoncom_on_linux() -> None:
    code = (
        "import builtins, sys; original=builtins.__import__; "
        "builtins.__import__=lambda name,*a,**k: "
        "(_ for _ in ()).throw(ImportError('blocked')) if name=='pythoncom' else original(name,*a,**k); "
        "sys.platform='linux'; import video_workflow.worker"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
```

Run: `python -m pytest tests/test_worker.py::test_worker_imports_without_pythoncom_on_linux -q`

Expected: FAIL because `worker.py` imports `pythoncom` unconditionally.

- [ ] **Step 2: Implement conditional COM lifecycle and adapt the existing balance test**

```python
from contextlib import contextmanager
import sys

if sys.platform == "win32":
    import pythoncom
else:
    pythoncom = None


@contextmanager
def _com_context():
    if pythoncom is None:
        yield
        return
    pythoncom.CoInitialize()
    try:
        yield
    finally:
        pythoncom.CoUninitialize()
```

Wrap the body of `_run` with `with _com_context():`. Update `test_stop_cancels_build_marks_interrupted_and_balances_com` to monkeypatch `video_workflow.worker.pythoncom` with a `SimpleNamespace` holding the two fake functions. Add a Linux no-op test that monkeypatches `pythoncom = None` and verifies a job completes.

- [ ] **Step 3: Run worker tests**

Run: `python -m pytest tests/test_worker.py -q`

Expected: all worker tests PASS on Linux; the fake Windows COM lifecycle remains balanced.

- [ ] **Step 4: Write the failing native-folder-opening tests**

```python
def test_open_output_folder_uses_xdg_open_on_linux(tmp_path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr("video_workflow.ui.sys.platform", "linux")
    monkeypatch.setattr("video_workflow.ui.subprocess.Popen", lambda argv: calls.append(argv))
    _open_directory(tmp_path)
    assert calls == [["xdg-open", str(tmp_path)]]


def test_open_output_folder_uses_startfile_on_windows(tmp_path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr("video_workflow.ui.sys.platform", "win32")
    monkeypatch.setattr("video_workflow.ui.os.startfile", lambda path: calls.append(path), raising=False)
    _open_directory(tmp_path)
    assert calls == [tmp_path]
```

Run: `python -m pytest tests/test_ui.py -q`

Expected: FAIL because `_open_directory` does not exist and the UI calls `os.startfile` directly.

- [ ] **Step 5: Implement native folder opening and use it from the UI**

Add `subprocess` and `sys` imports. Implement Windows with `os.startfile(path)`, Linux with `subprocess.Popen(["xdg-open", str(path)])`, and raise `OSError` for unsupported systems. Replace the direct call inside `open_selected_folder` with `_open_directory(job.output_directory)`.

- [ ] **Step 6: Run worker and UI regressions, then commit**

Run: `python -m pytest tests/test_worker.py tests/test_ui.py tests/test_queue_controller.py -q`

Expected: all tests PASS.

```bash
git add video_workflow/worker.py video_workflow/ui.py tests/test_worker.py tests/test_ui.py
git commit -m "fix: make desktop queue runtime cross-platform"
```

### Task 4: Add Ubuntu setup and launch scripts

**Files:**
- Create: `setup.sh`
- Create: `run-ui.sh`
- Create: `tests/test_linux_scripts.py`

**Interfaces:**
- Consumes: Ubuntu `apt-get`, system `python3`, and the project package metadata.
- Produces: `./setup.sh [--check-only]` and `./run-ui.sh`.

- [ ] **Step 1: Write failing script existence and syntax tests**

```python
ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("name", ["setup.sh", "run-ui.sh"])
def test_linux_script_exists_is_executable_and_has_valid_syntax(name) -> None:
    script = ROOT / name
    assert script.is_file()
    assert script.stat().st_mode & stat.S_IXUSR
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
```

Run: `python -m pytest tests/test_linux_scripts.py -q`

Expected: FAIL because both scripts are absent.

- [ ] **Step 2: Create `setup.sh` with explicit checks and idempotent installation**

Implement:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
CHECK_ONLY=0

if [[ "${1:-}" == "--check-only" ]]; then
  CHECK_ONLY=1
  shift
fi
if (( $# != 0 )); then
  echo "Usage: ./setup.sh [--check-only]" >&2
  exit 2
fi
```

Add `report_check NAME STATUS DETAIL`, `version_is_supported`, `find_office`, `check_dependencies` and `main`. Source `/etc/os-release`, require `ID=ubuntu`, and accept exactly `VERSION_ID=24.04` or `VERSION_ID=26.04`; verify `python3` reports at least 3.12. In normal mode run:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-tk ffmpeg \
  libreoffice-impress-nogui poppler-utils fonts-liberation fonts-noto-core
python3 -m venv "$REPO_ROOT/.venv"
"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install -e "$REPO_ROOT"
```

Only create the venv when `$VENV_PYTHON` does not exist. In check-only mode call no installation or venv commands. Report PASS/FAIL for Ubuntu, Python, Tkinter, FFmpeg, ffprobe, LibreOffice, pdftocairo and virtual-environment package import; return non-zero if any fail.

- [ ] **Step 3: Create `run-ui.sh` with a clear missing-venv failure**

```bash
#!/usr/bin/env bash
set -Eeuo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON="$REPO_ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Virtual environment not found. Run ./setup.sh first." >&2
  exit 1
fi
cd "$REPO_ROOT"
exec "$PYTHON" -m video_workflow.ui "$@"
```

Set executable bits with `chmod +x setup.sh run-ui.sh`.

- [ ] **Step 4: Write the failing no-mutation and missing-venv tests**

```python
def test_setup_check_only_never_calls_sudo(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sentinel = tmp_path / "sudo-called"
    sudo = fake_bin / "sudo"
    sudo.write_text(f"#!/bin/sh\ntouch '{sentinel}'\nexit 99\n", encoding="utf-8")
    sudo.chmod(0o755)
    env = os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}"}
    subprocess.run(["bash", str(ROOT / "setup.sh"), "--check-only"], env=env, capture_output=True, text=True)
    assert not sentinel.exists()


def test_run_ui_without_venv_has_clear_error(tmp_path) -> None:
    script = tmp_path / "run-ui.sh"
    script.write_bytes((ROOT / "run-ui.sh").read_bytes())
    script.chmod(0o755)
    result = subprocess.run([str(script)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "Run ./setup.sh first" in result.stderr
```

- [ ] **Step 5: Run script tests and check-only manually**

Run: `python -m pytest tests/test_linux_scripts.py -q`

Expected: all tests PASS.

Run: `bash -n setup.sh run-ui.sh && ./setup.sh --check-only`

Expected on a not-yet-provisioned machine: syntax check PASS; check-only prints individual PASS/FAIL lines and exits non-zero for missing LibreOffice without invoking `sudo`.

- [ ] **Step 6: Commit Task 4**

```bash
git add setup.sh run-ui.sh tests/test_linux_scripts.py
git commit -m "feat: add Ubuntu setup and launch scripts"
```

### Task 5: Document Linux usage and prove the real pipeline

**Files:**
- Modify: `README.md`
- Modify: `pyproject.toml`
- Create: `tests/test_linux_integration.py`
- Test: all files under `tests/`

**Interfaces:**
- Consumes: the public `build_video(BuildRequest)` pipeline plus installed LibreOffice, Poppler and FFmpeg.
- Produces: documented Windows/Ubuntu setup paths and an optional real Linux integration test.

- [ ] **Step 1: Write the real Linux integration test before documentation changes**

Mark `integration` in `[tool.pytest.ini_options]`. The test skips unless `sys.platform.startswith("linux")` and `libreoffice`/`soffice`, `pdftocairo`, `ffmpeg` and `ffprobe` are present. Generate short fixtures with FFmpeg, use the real 20-slide PPTX and logo, then run the whole pipeline:

```python
@pytest.mark.integration
def test_linux_pipeline_builds_short_video(tmp_path: Path) -> None:
    office = shutil.which("libreoffice") or shutil.which("soffice")
    required = [office, shutil.which("pdftocairo"), shutil.which("ffmpeg"), shutil.which("ffprobe")]
    if not sys.platform.startswith("linux") or not all(required):
        pytest.skip("Linux integration dependencies are not installed")

    source = tmp_path / "source.wav"
    outro = tmp_path / "outro.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(source)], check=True)
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=720x1280:d=1:r=24",
        "-f", "lavfi", "-i", "sine=frequency=660:duration=1", "-shortest",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(outro),
    ], check=True)
    timeline = tmp_path / "timeline.txt"
    timeline.write_text("Slide 01: 00:00.000 --> 00:01.000\nSlide 02: 00:01.000 --> 00:02.000\n", encoding="utf-8")
    request = BuildRequest(
        source_media=source,
        pptx=FIXTURES / "TOAN7_C4_B12_T36_2_fixed.pptx",
        timeline=timeline,
        logo=FIXTURES / "logo.png",
        outro=outro,
        output=tmp_path / "final.mp4",
    )
    report = build_video(request)
    assert request.output.stat().st_size > 0
    assert Path(f"{request.output}.report.json").is_file()
    assert report.slide_count == 2
```

- [ ] **Step 2: Run the integration test to establish its current result**

Run: `python -m pytest tests/test_linux_integration.py -q`

Expected before system dependencies are installed: SKIP with the explicit dependency reason. After `setup.sh` provisions the machine: PASS and a valid short H.264/AAC video/report are produced in pytest's temporary directory.

- [ ] **Step 3: Rewrite README setup and usage sections for both platforms**

Keep the existing workflow description and add exact commands:

````markdown
### Windows

Requires Microsoft PowerPoint, Python 3.12+ and Windows 10/11.

```bat
setup.bat
run-ui.bat
```

### Ubuntu Linux

Supports Ubuntu Desktop 24.04/26.04 LTS. Setup installs Python/Tkinter,
FFmpeg, LibreOffice Impress headless, Poppler and fallback fonts.

```bash
chmod +x setup.sh run-ui.sh
./setup.sh
./run-ui.sh
```

Check without changing the system:

```bash
./setup.sh --check-only
```
````

Add `.venv/bin/python -m video_workflow.ui`, the Bash CLI example, the LibreOffice/font fidelity warning and the recommendation to use Windows/PowerPoint when exact PowerPoint rendering is required.

- [ ] **Step 4: Run focused documentation and import checks**

Run: `rg -n "Ubuntu|setup.sh|run-ui.sh|LibreOffice|pdftocairo|PowerPoint" README.md`

Expected: both platform paths, Linux tools and fidelity warning are present.

Run: `python -c "import video_workflow.worker, video_workflow.ui, video_workflow.slide_export"`

Expected: exit code 0 on Linux without pywin32.

- [ ] **Step 5: Run the complete suite**

Run: `python -m pytest tests -q`

Expected: all unit tests PASS; the Linux integration test either PASSes when dependencies are installed or reports one named SKIP when they are absent. No unexpected warnings or failures.

- [ ] **Step 6: Provision the current Ubuntu machine and run final system verification**

Run: `./setup.sh`

Expected: Ubuntu dependency checks all PASS and `.venv` contains the editable package. This command requires user approval for `sudo apt-get` and network/package installation.

Run: `./setup.sh --check-only`

Expected: exit code 0 with PASS for Ubuntu, Python, Tkinter, FFmpeg, ffprobe, LibreOffice, pdftocairo and the package import.

Run: `.venv/bin/python -m pytest tests/test_linux_integration.py -q`

Expected: PASS, not SKIP.

- [ ] **Step 7: Commit Task 5**

```bash
git add README.md pyproject.toml tests/test_linux_integration.py
git commit -m "docs: add Ubuntu workflow instructions"
```

- [ ] **Step 8: Review final diff and status**

Run: `git diff origin/main...HEAD --check && git status --short --branch`

Expected: no whitespace errors; branch contains only the approved spec, plan and Linux-support implementation commits, with no unrelated working-tree changes.
