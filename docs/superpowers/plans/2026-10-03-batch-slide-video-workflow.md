# Batch Slide Video Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a resumable native CLI that turns every two-part lesson under `gen_video` into a merged slide deck, generated timelines, two rendered slide videos, and one final joined video.

**Architecture:** Keep discovery/orchestration, flattened PPTX creation, and visual alignment in focused modules. Reuse the existing slide exporter, `build_video`, `merge_videos`, timeline validation, and media probing; completion is determined only by a valid final video.

**Tech Stack:** Python 3.12, Pillow, python-pptx, FFmpeg/ffprobe, LibreOffice, pytest

**Spec:** `docs/superpowers/specs/2026-10-03-batch-slide-video-workflow-design.md`

## Global Constraints

- Scan direct `T<number>` children of `/home/meconlonton/Documents/gen_video`.
- P1 uses merged slide IDs 1-20; P2 uses merged slide IDs 21-40.
- P1 receives the logo only; P2 receives the logo and outro.
- A valid existing final video skips the entire lesson; there is no force option.
- Preserve valid intermediate outputs and continue the batch after a lesson failure.
- The first delivery is CLI-only and Linux/Ubuntu-oriented.

## Review Focus

- Ambiguous or missing P1/P2/PPTX inputs must report one lesson failure without stopping siblings (Task 1).
- A corrupt existing final MP4 must not count as complete (Task 4).
- Letterboxing, video controls, or cursor overlays must not cause backward slide transitions (Task 3).
- A transition visible for only a few sampled frames must be debounced rather than emitted as a tiny cue (Task 3).
- A stopped run with only some valid intermediates must reuse those files and execute only missing stages (Task 4).

---

### Task 1: Lesson discovery and output contract

**Files:**
- Create: `video_workflow/batch_models.py`
- Create: `video_workflow/batch_discovery.py`
- Test: `tests/test_batch_discovery.py`

**Interfaces:**
- Produces: `LessonInputs`, `LessonOutputs`, `LessonResult`, `discover_lessons(root: Path) -> tuple[list[LessonInputs], list[LessonResult]]`, and `lesson_outputs(lesson: LessonInputs) -> LessonOutputs`.
- `LessonInputs` contains lesson name/root, P1/P2 videos, P1/P2 PPTX files; `LessonOutputs` contains deterministic paths for merged PPTX, reference images, two timeline JSON files, confidence JSON, two rendered MP4s, final MP4, and reports beneath `<lesson>/output`.

- [ ] **Step 1: Write failing discovery tests**

Cover exact pairing for `P1_T8_V8.mp4`, `P2_T8_V8.mp4`, `T8_1_Slide.pptx`, and `T8_2_Slide.pptx`; deterministic output names; sorted `T8`, `T9`, `T10`; and isolated errors for missing or duplicate inputs.

- [ ] **Step 2: Run tests and verify the module is missing**

Run: `pytest tests/test_batch_discovery.py -v`
Expected: FAIL during import.

- [ ] **Step 3: Implement immutable models and direct-child discovery**

Use anchored, case-insensitive filename patterns tied to the folder's `T<number>` identity. Never recurse into `output`.

- [ ] **Step 4: Run discovery tests**

Run: `pytest tests/test_batch_discovery.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add video_workflow/batch_models.py video_workflow/batch_discovery.py tests/test_batch_discovery.py
git commit -m "feat: discover two-part video lessons"
```

### Task 2: Flatten and merge the two PPTX decks

**Files:**
- Create: `video_workflow/pptx_merge.py`
- Modify: `pyproject.toml`
- Test: `tests/test_pptx_merge.py`

**Interfaces:**
- Consumes: existing `export_slides(pptx, slide_ids, target_dir, width, height)` and `count_pptx_slides(pptx)`.
- Produces: `merge_pptx_as_images(first: Path, second: Path, output: Path, image_dir: Path) -> tuple[Path, ...]`; returned paths are the 40 ordered 1280x720 reference PNGs used by alignment.

- [ ] **Step 1: Write failing merge tests**

Mock slide export to assert part order, require exactly 20 slides per source, create a readable 40-slide output deck, place every image edge-to-edge on a 16:9 blank slide, and reject an existing incomplete/corrupt output instead of silently reusing it.

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/test_pptx_merge.py -v`
Expected: FAIL during import.

- [ ] **Step 3: Add runtime dependencies**

Add `pillow>=11` and `python-pptx>=1.0` to `[project].dependencies`; keep pytest in the dev extra.

- [ ] **Step 4: Implement flattened deck generation**

Export both source decks, then use `python-pptx` to create a 13.333 x 7.5 inch presentation with one full-slide PNG per slide. Write to a sibling temporary file and atomically replace only after reopening and verifying 40 slides.

- [ ] **Step 5: Run merge tests**

Run: `pytest tests/test_pptx_merge.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml video_workflow/pptx_merge.py tests/test_pptx_merge.py
git commit -m "feat: merge lesson decks as flattened slides"
```

### Task 3: Visual slide alignment and timeline generation

**Files:**
- Create: `video_workflow/slide_alignment.py`
- Test: `tests/test_slide_alignment.py`

**Interfaces:**
- Produces: `AlignmentConfig(sample_interval_ms=1000, refine_interval_ms=100, min_stable_ms=1500, min_confidence=0.70)`, `AlignmentReport`, and `align_video_to_slides(video: Path, slide_images: Sequence[Path], slide_ids: Sequence[int], timeline_path: Path, report_path: Path, config: AlignmentConfig = AlignmentConfig()) -> AlignmentReport`.
- Timeline output is the JSON array accepted by existing `parse_timeline`; every row has `slide_id`, `start_ms`, `end_ms`, and `duration_ms`.

- [ ] **Step 1: Write failing pure matching/cleanup tests**

Use synthetic PIL images and sampled-frame fixtures to assert correct best-match scores, monotonic slide IDs, removal of a sub-1500 ms false transition, exact P1 IDs 1-20/P2 IDs 21-40, a zero start, contiguous positive cues, and rejection below confidence 0.70.

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/test_slide_alignment.py -v`
Expected: FAIL during import.

- [ ] **Step 3: Implement normalized visual comparison**

Crop a small outer border, resize to a fixed grayscale thumbnail, normalize contrast, and score mean absolute pixel difference as `[0, 1]` similarity. Restrict candidates to the supplied ordered slide range and use dynamic programming so selected slide indices never decrease.

- [ ] **Step 4: Implement coarse sampling, boundary refinement, and timeline writing**

Use FFmpeg to extract coarse frames every 1000 ms, refine changed boundaries at 100 ms, debounce states shorter than 1500 ms, extend the last cue to the source audio duration from `probe_media`, validate with existing `validate_timeline`, and atomically write timeline/report JSON.

- [ ] **Step 5: Run alignment tests**

Run: `pytest tests/test_slide_alignment.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add video_workflow/slide_alignment.py tests/test_slide_alignment.py
git commit -m "feat: derive slide timelines from video frames"
```

### Task 4: Resumable batch orchestration and CLI

**Files:**
- Create: `video_workflow/batch_workflow.py`
- Create: `video_workflow/batch_cli.py`
- Modify: `pyproject.toml`
- Test: `tests/test_batch_workflow.py`
- Test: `tests/test_batch_cli.py`

**Interfaces:**
- Consumes: Tasks 1-3, `BuildRequest`/`build_video`, `MergeRequest`/`merge_videos`, and `probe_media`.
- Produces: `BatchRequest(source_root: Path, assets_dir: Path, fps: int = 24)`, `run_batch(request: BatchRequest) -> BatchReport`, and console command `build-video-batch`.

- [ ] **Step 1: Write failing orchestration tests**

Mock stage boundaries and assert: valid final skips all work; corrupt final continues; existing valid merged deck/timelines/part videos are reused; P1 request has no outro; P2 request uses `Outro720.mp4`; logo is used by both; final merge is last; one lesson failure does not stop another; and exit status is nonzero when any lesson fails.

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/test_batch_workflow.py tests/test_batch_cli.py -v`
Expected: FAIL during import.

- [ ] **Step 3: Implement artifact validity checks and orchestration**

Validate reusable PPTX by slide count, timelines by parse/validation against video duration, and MP4s with `probe_media`; never treat temporary or zero-byte files as complete. Catch errors per lesson and return structured completed/skipped/failed results.

- [ ] **Step 4: Implement CLI entry point**

Accept optional `--source-root` and `--assets-dir` with the approved absolute paths as defaults plus `--fps`; do not expose a force flag. Print one concise status line per lesson and a final summary.

- [ ] **Step 5: Run task tests**

Run: `pytest tests/test_batch_workflow.py tests/test_batch_cli.py -v`
Expected: PASS.

- [ ] **Step 6: Run the complete automated suite**

Run: `pytest -q`
Expected: all non-environmental tests pass.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml video_workflow/batch_workflow.py video_workflow/batch_cli.py tests/test_batch_workflow.py tests/test_batch_cli.py
git commit -m "feat: add resumable batch slide video command"
```

### Task 5: Ubuntu smoke verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: installed `build-video-batch` command.
- Produces: documented command and verified behavior on the current sample tree.

- [ ] **Step 1: Document installation, command, output layout, skip behavior, and failure report**

- [ ] **Step 2: Run discovery without rendering through the tested discovery API**

Run: `.venv/bin/python -c "from pathlib import Path; from video_workflow.batch_discovery import discover_lessons; lessons, errors = discover_lessons(Path('/home/meconlonton/Documents/gen_video')); print([x.name for x in lessons], errors)"`
Expected: lessons `T8` through `T13`, with no discovery errors.

- [ ] **Step 3: Run one lesson end-to-end in a temporary single-lesson source root**

Run the command against a temporary root containing only symlinks to one sample lesson, leaving the real completed-output policy untouched.
Expected: merged deck has 40 slides; timelines use 1-20 and 21-40; P1 has no outro; P2 has the outro; final MP4 is valid 1280x720 H.264/AAC at 24 fps.

- [ ] **Step 4: Run the real batch**

Run: `.venv/bin/build-video-batch`
Expected: every processable lesson completes or reports a precise alignment/input failure, and rerunning skips every valid completed lesson.

- [ ] **Step 5: Re-run tests and inspect repository state**

Run: `pytest -q && git status --short`
Expected: tests pass; only intended README change is uncommitted.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: explain batch slide video workflow"
```
