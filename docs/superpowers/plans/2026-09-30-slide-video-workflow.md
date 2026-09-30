# Slide Video Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local command that converts a PPTX and slide timeline into a 720p MP4 with audio from a source video, a supplied logo, and a supplied full-frame outro.

**Architecture:** Parse and validate timing before touching media. Use a separate PowerPoint COM instance to export referenced slides to temporary PNGs, then FFmpeg to render the timed lecture, normalize the outro, and join both parts. Probe the final output and publish it only after checks pass.

**Tech Stack:** Python 3.12, pywin32, pytest, Microsoft PowerPoint on Windows, FFmpeg/ffprobe 8.

**Spec:** `docs/superpowers/specs/2026-09-30-slide-video-workflow-design.md`

## Global Constraints

- Create or modify files only inside `D:\work\pyramid\workflow_capcut`; treat all other projects as read-only.
- Required inputs on every run: source MP4, PPTX, TXT or JSON timeline, logo image, outro video. Output: 1280×720 MP4, H.264/yuv420p and AAC, 24 fps by default.
- Do not use Canva, CapCut, agent, VLM, STT, or one audio file per slide.
- Use absolute timeline boundaries rounded to output frames. Remove the source video's trailing `Outro/blank` segment; keep the separate outro's full image, duration, and audio.
- Keep the logo at lower right only during slide content; preserve its aspect ratio. Fit portrait outro inside 16:9 with black side padding.
- Do not overwrite an existing output or alter uploaded inputs. Use argument arrays for subprocesses and a temporary staging directory.
- This folder has no Git repository. Do not initialize one as part of implementation; omit commit steps until version control exists.

## Review Focus

- A Windows path containing spaces or Vietnamese characters must survive COM and FFmpeg calls unchanged; Task 3 and Task 5 test this.
- Missing or too-short source audio must fail before publishing output; Task 2 and Task 5 test this.
- RGB and RGBA logos must both overlay correctly without stretching; Task 3 tests this.
- A portrait outro, including one with no audio, must retain its whole image and produce a continuous audio stream; Task 4 tests this.
- Gaps, overlaps, unknown slide IDs, and a misplaced `Outro/blank` row must produce precise validation errors; Task 1 tests these.

## File Map

| File | Responsibility |
| --- | --- |
| `video_workflow/timeline.py` | Parse TXT/JSON, validate slide IDs and continuity, convert absolute milliseconds to frame spans. |
| `video_workflow/probe.py` | Run `ffprobe` and return typed stream metadata. |
| `video_workflow/powerpoint.py` | Export referenced PPTX slides through an isolated PowerPoint COM instance. |
| `video_workflow/compose.py` | Build FFmpeg commands for lecture, logo, outro normalization, and final join. |
| `video_workflow/pipeline.py` | Validate inputs, stage artifacts, run phases, verify output, write report, publish MP4. |
| `video_workflow/cli.py`, `video_workflow/__main__.py` | Parse CLI arguments and return useful exit codes. |
| `tests/test_timeline.py`, `tests/test_probe_powerpoint.py`, `tests/test_compose.py`, `tests/test_pipeline.py` | Unit tests and short synthetic media tests. |
| `README.md`, `pyproject.toml` | Install, usage, dependency, and smoke-test instructions. |

---

### Task 1: Timeline contract and frame spans

**Files:** Create `video_workflow/__init__.py`, `video_workflow/timeline.py`, `tests/test_timeline.py`; create `pyproject.toml` with Python 3.12, `pywin32`, and pytest development dependency.

**Interfaces:** Produce `SlideCue(slide_id: int, start_ms: int, end_ms: int)`, `Timeline(slides: tuple[SlideCue, ...], tail: tuple[int, int] | None)`, and `FrameSpan(slide_id: int, start_frame: int, end_frame: int)`. Expose `parse_timeline(path: Path) -> Timeline` and `validate_timeline(timeline: Timeline, slide_count: int, audio_duration_ms: int, fps: int) -> tuple[FrameSpan, ...]`.

- [ ] Write failing tests: parse the real TXT fixture into 20 slides with last `end_ms == 580791` and tail `(580791, 584400)`; parse JSON with repeated/reordered IDs; assert slide 1 ends at frame 350 and final boundary is frame 13939 at 24 fps. Test `duration_ms` mismatch over 20 ms, gap/overlap over 20 ms, unknown slide ID, nonpositive duration, and `Outro/blank` before the final slide.
- [ ] Run `python -m pytest tests/test_timeline.py -q`; expect failures for missing module/functions.
- [ ] Implement the typed parser and validator in `timeline.py`. Accept UTF-8 TXT with CRLF and the JSON format in the spec. Normalize joins within 20 ms by replacing the later start with the prior end; round each absolute boundary using integer arithmetic before deriving frame counts.
- [ ] Run `python -m pytest tests/test_timeline.py -q`; expect all Task 1 tests to pass.

### Task 2: Media probe and PowerPoint slide export

**Files:** Create `video_workflow/probe.py`, `video_workflow/powerpoint.py`, `tests/test_probe_powerpoint.py`.

**Interfaces:** Produce `MediaInfo(duration_ms: int, width: int | None, height: int | None, fps: Fraction | None, audio_duration_ms: int | None, has_audio: bool)` from `probe_media(path: Path) -> MediaInfo`. Expose `export_slides(pptx: Path, slide_ids: Collection[int], target_dir: Path, width: int = 1280, height: int = 720) -> dict[int, Path]`.

- [ ] Write failing tests using captured `ffprobe` JSON for source, portrait outro, and missing audio. Mock `win32com.client.DispatchEx` to assert each requested PPTX slide exports once, the tool calls `Quit()` only on its own COM instance after success or failure, and paths with spaces are passed verbatim. Check that missing slide IDs fail before export.
- [ ] Run `python -m pytest tests/test_probe_powerpoint.py -q`; expect missing-module/function failures.
- [ ] Implement `probe_media` with `subprocess.run(argv, shell=False)` and precise errors for absent streams or tools. Implement PowerPoint export with `DispatchEx('PowerPoint.Application')`, opening read-only, `Slide.Export(..., 'PNG', width, height)`, and `try/finally` cleanup. Preserve source slide numbering in output filenames.
- [ ] Run `python -m pytest tests/test_probe_powerpoint.py -q`; expect all Task 2 tests to pass.

### Task 3: Render the slide lecture and logo

**Files:** Create `video_workflow/compose.py`, `tests/test_compose.py`.

**Interfaces:** Expose `render_lecture(slides: Mapping[int, Path], spans: Sequence[FrameSpan], source_video: Path, logo: Path, target: Path, *, fps: int, logo_width_ratio: float, margin_px: int) -> None`. It consumes Task 1 frame spans and Task 2 media paths.

- [ ] Write failing synthetic-media tests with three solid-color PNGs and a short audio source: verify output is 1280×720/24 fps, the expected colors appear immediately before and after both frame boundaries, the last slide lasts to its final boundary, and source audio remains one continuous stream. Include a 4:3 slide and assert its full image remains visible with side padding. Test an RGB logo and an RGBA logo for preserved aspect ratio and lower-right placement; use a temporary path containing spaces and Vietnamese characters.
- [ ] Run `python -m pytest tests/test_compose.py -q -k lecture`; expect failure for missing renderer.
- [ ] Implement concat-list generation from frame-span durations, repeating the final PNG entry. Render at explicit frame count and fps, using scale/pad for non-16:9 slides, scale/overlay for the logo, source audio trimmed once at the final slide boundary, and H.264/yuv420p plus AAC 48 kHz stereo. Avoid `-shortest`.
- [ ] Run `python -m pytest tests/test_compose.py -q -k lecture`; expect pass and inspect `ffprobe` output inside the tests.

### Task 4: Preserve and append the complete outro

**Files:** Modify `video_workflow/compose.py`, `tests/test_compose.py`.

**Interfaces:** Add `normalize_outro(outro: Path, target: Path, *, fps: int) -> None` and `join_parts(lecture: Path, outro: Path, target: Path) -> None`.

- [ ] Write failing tests using synthetic portrait and landscape outros. Assert the portrait corners remain visible after 1280×720 normalization with black side bars; assert full source outro duration survives within one frame plus AAC packet tolerance; assert outro audio begins after lecture audio. Repeat with silent/no-audio outro and require a valid continuous AAC stream.
- [ ] Run `python -m pytest tests/test_compose.py -q -k outro`; expect missing-function failures.
- [ ] Implement FFmpeg scale-to-fit/pad, fps, H.264/yuv420p, AAC 48 kHz stereo normalization. Generate silence when the outro has no audio. Join normalized parts with FFmpeg concat; verify monotonic timestamps and use a re-encode join only if stream-copy cannot satisfy the tests.
- [ ] Run `python -m pytest tests/test_compose.py -q -k outro`; expect pass.

### Task 5: CLI, publication, reporting, and real fixture

**Files:** Create `video_workflow/pipeline.py`, `video_workflow/cli.py`, `video_workflow/__main__.py`, `tests/test_pipeline.py`, `README.md`; update `pyproject.toml` entry point.

**Interfaces:** Produce `BuildRequest(video: Path, pptx: Path, timeline: Path, logo: Path, outro: Path, output: Path, fps: int = 24, logo_width_ratio: float = 0.12, margin_px: int = 0)` and `build_video(request: BuildRequest) -> BuildReport`; expose `main(argv: Sequence[str] | None = None) -> int` and `python -m video_workflow`.

- [ ] Write failing pipeline tests with mocked phase boundaries: missing input or audio shorter than final slide fails before export; existing output remains untouched; one phase failure leaves no published MP4; success writes MP4 plus JSON report. Assert expected final duration uses `last_slide_end + full_outro_duration` and the trailing `Outro/blank` is excluded. Test the CLI with all five required path flags and a path containing spaces.
- [ ] Run `python -m pytest tests/test_pipeline.py -q`; expect missing-function failures.
- [ ] Implement orchestration and CLI. Stage in a temporary directory beside the output, verify final `ffprobe` streams and duration, then move validated MP4 into place and write `<output>.report.json`. Report the trimmed source tail and exact input paths. Document requirements and one command using the files in `test/` without embedding those paths in code.
- [ ] Run `python -m pytest tests -q`; expect all tests to pass.
- [ ] Run one full build against `test/` into a new output path and inspect the report, `ffprobe`, frame samples at selected slide boundaries, logo corner, and the first/last outro frames. Expected test fixture: 20 slides, source narration cut at 09:40.791, no Gemini Notebook tail, complete portrait outro, total about 09:48.492. Retain the final MP4 and report for user review.

## Execution Handoff

After the plan is reviewed, implement tasks in order. Task 3 depends on Tasks 1–2; Task 4 shares the FFmpeg composition module with Task 3; Task 5 integrates every interface. The local folder has no Git history, so each task ends with its passing verification instead of a commit.
