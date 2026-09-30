# Desktop Job Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a lightweight Windows desktop UI that persists global logo/outro settings and a sequential queue of slide-video jobs using either video or audio sources.

**Architecture:** Tkinter owns the main thread while one background worker thread claims queued jobs and calls the existing PowerPoint/FFmpeg pipeline. Versioned JSON files persist settings and jobs through atomic replacement; UI events cross threads through `queue.Queue`. The render pipeline gains stage callbacks and cooperative cancellation while retaining the existing CLI and validated 720p output.

**Tech Stack:** Python 3.12+, Tkinter, JSON, threading, pywin32/PowerPoint COM, FFmpeg/ffprobe, pytest, Windows batch and PowerShell.

**Spec:** `docs/superpowers/specs/2026-09-30-desktop-job-queue-design.md`

## Global Constraints

- Run locally on Windows with Microsoft PowerPoint; do not add a web server, account system, cloud storage, or file upload service.
- Process exactly one job at a time and start automatically when a waiting job exists.
- Store only source paths. Do not copy source media, PPTX, timeline, logo, or outro.
- Persist data under `.workflow_data`; JSON writes must use a same-directory temporary file plus `os.replace`.
- Retain `waiting`, `running`, `completed`, `failed`, and `interrupted` history. Convert stale `running` jobs to `interrupted` on startup.
- Use the latest persisted global logo/outro when a job starts. Missing global assets pause claiming jobs.
- Accept any ffprobe-readable source with an adequate audio stream; keep `--video` as a CLI alias.
- Preserve all existing render guarantees: 1280x720, H.264/yuv420p, AAC stereo, default 24 fps, slide overscan, flush lower-right logo, trimmed source tail, and complete outro.
- `setup.bat` must create `.venv`, install Python runtime dependencies, install missing FFmpeg with `winget`, and verify PowerPoint without trying to install Office.
- This folder is not a Git repository. Do not initialize one; replace commit steps with the test evidence recorded in the existing SDD ledger.

## Review Focus

- Paths with spaces, Vietnamese text, and backslashes must round-trip through JSON and reach PowerPoint/FFmpeg unchanged; Tasks 1, 3, and 6 test this.
- A truncated or corrupt `jobs.json` must be preserved as a timestamped backup and must not prevent the UI from opening; Task 1 tests this.
- Closing during FFmpeg or between PowerPoint slide exports must stop external work, publish no output, and persist `interrupted`; Tasks 3 and 4 test this.
- Missing global assets with waiting jobs must pause the queue and resume automatically after valid paths are saved; Tasks 2 and 4 test this.
- An output created after enqueue but before execution must never be overwritten; Tasks 2 and 3 test this second validation point.

## File Map

| File | Responsibility |
| --- | --- |
| `video_workflow/job_models.py` | Persisted settings/job models, statuses, stages, output-name validation. |
| `video_workflow/json_store.py` | Locked, versioned, atomic JSON persistence and corrupt-file recovery. |
| `video_workflow/queue_controller.py` | Queue state transitions, sequential claims, retry/remove, startup recovery. |
| `video_workflow/worker.py` | Background loop, COM thread lifecycle, pipeline calls, cancellation, UI events. |
| `video_workflow/ui.py` | Tkinter layout, file dialogs, event polling, close behavior, desktop entry point. |
| `video_workflow/pipeline.py` | Source-media request, phase reporting, cancellation propagation, final validation. |
| `video_workflow/compose.py` | Cancellable/logged FFmpeg subprocess execution. |
| `video_workflow/powerpoint.py` | Cooperative cancellation between slide exports. |
| `video_workflow/cli.py` | `--source-media` with backward-compatible `--video` alias. |
| `scripts/setup.ps1`, `setup.bat`, `run-ui.bat` | Dependency installation, environment checks, and UI launch. |
| `tests/test_job_store.py`, `tests/test_queue_controller.py`, `tests/test_worker.py`, `tests/test_ui.py` | New unit and integration coverage. |
| `tests/test_pipeline.py`, `tests/test_compose.py`, `tests/test_probe_powerpoint.py` | Extended source-audio, phase, cancellation, and regression coverage. |

---

### Task 1: Persisted settings and job models

**Files:**
- Create: `video_workflow/job_models.py`
- Create: `video_workflow/json_store.py`
- Create: `tests/test_job_store.py`

**Interfaces:**
- Produces: `JobStatus(StrEnum)`, `JobStage(StrEnum)`, `GlobalSettings`, `JobRecord`, `validate_output_name(name: str) -> str`, and `JobRecord.output_path: Path`.
- Produces: `JsonStore(root: Path)` with `load_settings()`, `save_settings(settings)`, `load_jobs()`, `save_jobs(jobs)`, and `warnings: tuple[str, ...]`.
- JSON models expose explicit `to_dict()` and `from_dict()` methods and require `schema_version == 1`.

- [ ] **Step 1: Write failing model tests**

  Test all five statuses and six stages, automatic `.mp4`, a Unicode output name, rejection of separators/Windows reserved names/trailing dots, and construction of `output_directory / output_name`.

- [ ] **Step 2: Run the model tests and confirm RED**

  Run: `python -m pytest tests/test_job_store.py -q`

  Expected: import failure for `video_workflow.job_models`.

- [ ] **Step 3: Implement the model contract**

  Use timezone-aware ISO timestamps, UUID strings, `Path` at runtime, and plain strings in JSON. Keep records immutable and update them with `dataclasses.replace` in later tasks.

- [ ] **Step 4: Write failing persistence tests**

  Assert settings and a mixed-status job list round-trip paths containing spaces and Vietnamese characters. Patch `os.replace` to fail and assert the previous JSON remains readable. Write truncated JSON and assert it is renamed to `jobs.json.corrupt-<timestamp>`, an empty list is returned, and a warning is recorded.

- [ ] **Step 5: Implement locked atomic JSON storage**

  Use one `threading.RLock` per store, UTF-8, `ensure_ascii=False`, a temporary file in `root`, `flush` plus `os.fsync`, and `os.replace`. Never silently discard an unreadable file.

- [ ] **Step 6: Verify Task 1**

  Run: `python -m pytest tests/test_job_store.py -q`

  Expected: all Task 1 tests pass.

### Task 2: Queue state machine

**Files:**
- Create: `video_workflow/queue_controller.py`
- Create: `tests/test_queue_controller.py`
- Modify: `video_workflow/job_models.py`

**Interfaces:**
- Consumes: `JsonStore`, `GlobalSettings`, `JobRecord`, `JobStatus`, and `JobStage` from Task 1.
- Produces: `QueueController(store: JsonStore)` with `settings`, `jobs()`, `set_global_assets(logo, outro)`, `enqueue(...)`, `claim_next()`, `set_stage()`, `mark_completed()`, `mark_failed()`, `mark_interrupted()`, `retry()`, `remove()`, and `recover_startup()`.
- `claim_next() -> JobRecord | None` atomically changes only the oldest eligible `waiting` job to `running`.

- [ ] **Step 1: Write failing startup and sequential-claim tests**

  Assert `recover_startup()` changes every stale `running` record to `interrupted`, preserves all other records, and persists once. Enqueue three jobs and assert repeated claims never create two `running` jobs and preserve creation order.

- [ ] **Step 2: Write failing global-pause and output-race tests**

  Assert missing logo/outro returns no claim without changing `waiting`. After valid global paths are saved, assert the same job can be claimed. Create the output after enqueue and assert claim marks that job `failed` without overwriting it, then makes the next waiting job eligible.

- [ ] **Step 3: Write failing transition tests**

  Cover stage updates only for `running`, completion timestamps, failure error text, retry of only `failed`/`interrupted`, removal of only `waiting`, and paths that were moved after enqueue.

- [ ] **Step 4: Implement `QueueController` under one lock**

  Validate source media, PPTX, timeline, output directory, global assets, and output collision at the transitions specified by the tests. Persist after each state mutation and return immutable snapshots to callers.

- [ ] **Step 5: Verify Task 2**

  Run: `python -m pytest tests/test_queue_controller.py -q`

  Expected: all Task 2 tests pass.

### Task 3: Audio/video source contract, stage reporting, and cancellation

**Files:**
- Modify: `video_workflow/pipeline.py`
- Modify: `video_workflow/compose.py`
- Modify: `video_workflow/powerpoint.py`
- Modify: `video_workflow/cli.py`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_compose.py`
- Modify: `tests/test_probe_powerpoint.py`

**Interfaces:**
- Produces: `BuildRequest(source_media: Path, pptx: Path, timeline: Path, logo: Path, outro: Path, output: Path, fps: int = 24, logo_width_ratio: float = 0.12, margin_px: int = 0)`.
- Produces: `build_video(request, *, on_stage: Callable[[str], None] | None = None, cancel_event: threading.Event | None = None, log_path: Path | None = None) -> BuildReport`.
- Produces: `WorkflowCancelled(WorkflowError)`; cancellation leaves no published MP4 or report.
- Existing CLI accepts either `--source-media PATH` or legacy `--video PATH`, both mapped to `source_media`.

- [ ] **Step 1: Write failing source-audio tests**

  Generate short WAV and M4A fixtures, pass them directly to `render_lecture`, and assert valid AAC lecture output. Update pipeline tests to assert a source with audio and no video succeeds through mocked phases, while no-audio and short-audio inputs fail before PowerPoint export.

- [ ] **Step 2: Write failing phase-order and compatibility tests**

  Capture callbacks and assert exact order: `validating`, `exporting_slides`, `rendering_lecture`, `preparing_outro`, `joining`, `verifying`. Assert both CLI spellings construct the same request and paths with Vietnamese characters survive.

- [ ] **Step 3: Write failing FFmpeg cancellation tests**

  Mock `subprocess.Popen` with a long-running process, set the event, and assert terminate then bounded wait/kill behavior, a `WorkflowCancelled`, log output, and no target. Add a test where output appears after enqueue but before build and assert the pipeline still refuses overwrite.

- [ ] **Step 4: Write failing PowerPoint cancellation test**

  Set cancellation after the first fake slide export; assert no second export occurs and both `Close()` and `Quit()` run.

- [ ] **Step 5: Implement the new pipeline contract**

  Rename request/report input semantics to source media, emit each stage immediately before work, and check cancellation before and after every phase. Preserve existing staging and final verification behavior.

- [ ] **Step 6: Make FFmpeg and PowerPoint cooperatively cancellable**

  Replace `subprocess.run` inside composition with a `Popen` polling loop that writes stderr to the job log when supplied. Add cancellation checks between PowerPoint slide exports. Keep argument arrays and `shell=False`.

- [ ] **Step 7: Verify Task 3 and renderer regressions**

  Run: `python -m pytest tests/test_pipeline.py tests/test_compose.py tests/test_probe_powerpoint.py -q`

  Expected: all targeted tests pass, including existing border/logo/outro tests.

### Task 4: Sequential background worker

**Files:**
- Create: `video_workflow/worker.py`
- Create: `tests/test_worker.py`

**Interfaces:**
- Consumes: `QueueController` from Task 2 and cancellable `build_video` from Task 3.
- Produces: `WorkerEvent(kind: str, job_id: str | None, message: str | None)`.
- Produces: `QueueWorker(controller, *, build=build_video, events: queue.Queue[WorkerEvent] | None = None)` with `start()`, `wake()`, `stop(timeout: float = 10.0)`, `is_alive()`, and `current_job_id`.

- [ ] **Step 1: Write failing sequential and auto-continue tests**

  Inject a blocking fake build function, enqueue multiple jobs, and assert only one build call is active. Release it and assert the next job starts automatically. Make the first build fail and assert the next job still runs.

- [ ] **Step 2: Write failing stage/event tests**

  Have the fake builder emit every stage and assert the controller persists it while `WorkerEvent` instances arrive in order for UI polling.

- [ ] **Step 3: Write failing pause/resume tests for global assets**

  Start with missing global files and assert no build call. Save valid assets, call `wake()`, and assert the waiting job starts without re-enqueueing.

- [ ] **Step 4: Write failing stop/interruption and COM lifecycle tests**

  Stop during a fake cancellable build and assert the event is set, the current job is persisted as `interrupted`, the thread exits within the timeout, and COM initialize/uninitialize each run exactly once on the worker thread.

- [ ] **Step 5: Implement the worker loop**

  Use a condition/event to avoid busy polling. Initialize COM inside the worker, always uninitialize in `finally`, update the controller from builder callbacks, and never access Tkinter widgets.

- [ ] **Step 6: Verify Task 4**

  Run: `python -m pytest tests/test_worker.py tests/test_queue_controller.py -q`

  Expected: all worker and controller tests pass without PowerPoint or a display.

### Task 5: Tkinter desktop UI

**Files:**
- Create: `video_workflow/ui.py`
- Create: `tests/test_ui.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `JsonStore`, `QueueController`, and `QueueWorker`.
- Produces: `WorkflowApp(root: tkinter.Tk, controller: QueueController, worker: QueueWorker)` and `main() -> int` runnable as `python -m video_workflow.ui`.
- Adds console script `video-workflow-ui = "video_workflow.ui:main"`.

- [ ] **Step 1: Write failing UI validation and action tests**

  With a hidden Tk root and fake controller/worker, populate the five job fields and assert submit calls `enqueue` then `wake`. Assert invalid/missing fields display one error and do not enqueue. Assert retry, remove, and open-folder actions target the selected job.

- [ ] **Step 2: Write failing settings and event-poll tests**

  Mock file dialogs, save valid logo/outro, and assert controller settings update and worker wakes. Put stage/completion/failure events in the UI queue and assert the matching row refreshes without direct worker-thread widget calls.

- [ ] **Step 3: Write failing close test**

  Invoke the close handler with a live fake worker and assert it disables submission, calls `stop`, drains the final state, and destroys the root only after the worker reports stopped.

- [ ] **Step 4: Implement the three-section Tkinter layout**

  Build global settings, add-job form, and `ttk.Treeview` queue sections from the approved spec. Poll worker events with `root.after`, keep paths fully visible through entry fields/tooltips or horizontal scrolling, and surface the store's recovery warnings at startup.

- [ ] **Step 5: Verify Task 5**

  Run: `python -m pytest tests/test_ui.py tests/test_worker.py -q`

  Expected: all UI behavior tests pass. On Windows, open the app once with `python -m video_workflow.ui`, add no jobs, and close cleanly.

### Task 6: Windows setup, launcher, documentation, and end-to-end verification

**Files:**
- Create: `scripts/setup.ps1`
- Create: `setup.bat`
- Create: `run-ui.bat`
- Modify: `README.md`
- Modify: `pyproject.toml`
- Modify: `.superpowers/sdd/2026-09-30-slide-video-workflow/progress.md`

**Interfaces:**
- `setup.bat` forwards arguments to `scripts/setup.ps1` and returns its exit code.
- `scripts/setup.ps1` supports normal installation and `-CheckOnly`; normal mode may invoke `winget install --id Gyan.FFmpeg --exact --accept-source-agreements --accept-package-agreements`.
- `run-ui.bat` launches `.venv\Scripts\pythonw.exe -m video_workflow.ui` and gives a clear error when setup has not run.

- [ ] **Step 1: Implement setup and launcher scripts**

  Resolve every path relative to the script directory. Check Python >=3.12, create `.venv`, install `pip install -e .`, import Tkinter and pywin32, locate both FFmpeg programs, install missing FFmpeg through winget in normal mode, and open/quit only a new PowerPoint COM instance for verification.

- [ ] **Step 2: Exercise safe installer branches**

  Run: `powershell -ExecutionPolicy Bypass -File scripts/setup.ps1 -CheckOnly`

  Expected: explicit PASS/FAIL lines for Python, Tkinter, pywin32, FFmpeg, ffprobe, and PowerPoint; check-only must never invoke winget or change the environment.

- [ ] **Step 3: Update user documentation**

  Document clone/setup/launch, global asset selection, adding video/audio jobs, statuses, retry, persisted paths, output collision behavior, logs, and the CLI fallback. State that moved source files must be reselected.

- [ ] **Step 4: Run the complete automated suite**

  Run: `python -m pytest tests -q`

  Expected: every old and new test passes.

- [ ] **Step 5: Run desktop queue integration with real fixtures**

  Configure the existing logo/outro, enqueue one source-video job and one generated WAV-source job with valid PPTX/timelines, and verify only one runs at a time. During a disposable third job, close the app during FFmpeg, reopen, confirm `interrupted`, and retry.

- [ ] **Step 6: Inspect both completed outputs**

  Use ffprobe to assert 1280x720, 24 fps, H.264/yuv420p, AAC stereo 48 kHz, and expected duration. Sample lecture edges/logo and first/last outro frames. Confirm the output directory and names exactly match each job and no source file changed.

- [ ] **Step 7: Record final evidence**

  Update the SDD ledger with automated counts, installer check results, UI smoke test, interruption/retry result, and paths to retained review outputs. Do not create a Git repository or commit.

## Execution Handoff

Implement tasks in order. Tasks 1-2 establish the persistent state machine; Task 3 makes the existing pipeline safe for a desktop worker; Task 4 connects both without Tkinter; Task 5 adds the UI after behavior is testable; Task 6 packages and verifies the complete Windows workflow.
