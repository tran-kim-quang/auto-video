# Leaf-Folder Automatic Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a validated slide timeline automatically for a Batch Folder video/PPTX pair when its leaf folder has no timeline, then render that part through the existing serial slide-job workflow without overwriting user timelines.

**Architecture:** Add three focused CV layers: cached PPTX slide features, robust frame-to-slide matching, and monotonic sequence/timeline solving. A timeline-generation adapter owns source validation, two-pass detection, diagnostics, conflict detection, and atomic no-clobber publication; `QueueWorker` invokes it only for flagged `SLIDE` jobs before constructing the unchanged `BuildRequest`.

**Tech Stack:** Python 3.12+, OpenCV/SIFT/FLANN/RANSAC, NumPy, Pillow, FFmpeg/ffprobe, existing PowerPoint/LibreOffice slide export, pytest, Tkinter, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-10-07-slide-timeline-cv-design.md`

## Global Constraints

- CPU only; no VLM, CLIP, CNN, YOLO, external inference API, model weights, or required GPU.
- Preserve the existing `JobKind.SLIDE`; timeline generation is a stage of that job, never a separate queue job.
- Existing matching timelines are user-owned and authoritative; never auto-repair or overwrite one.
- Generate only for `.mp4`, `.mov`, or `.mkv`; audio-only `.mp3`, `.wav`, `.m4a`, and `.aac` without timelines are scan issues.
- Generated filename is `timeline_slide_<pptx-stem>.json` in the leaf folder.
- Renderer timeline is a non-empty top-level JSON array with local `slide_id` values `1..N`, contiguous `start_ms`/`end_ms`, optional consistent `duration_ms`, first start `0`, and final end no later than source audio duration.
- Detailed success/failure data belongs in `output/.timeline-diagnostics/<pptx-stem>.alignment.json`, never in the renderer timeline.
- Feature cache belongs in `.workflow_data/timeline-cache/<deck_hash>`, never under a lesson/source tree.
- Cache identity is SHA-256 of PPTX bytes plus feature-config version; incomplete or incompatible cache entries are rebuilt.
- Normal defaults: 1 FPS coarse sampling, 1500 SIFT features, Lowe ratio `0.72`, lookahead `4`, forced match every `4_000 ms`, minimum projected area ratio `0.08`.
- Retry defaults: 4 FPS, 3500 SIFT features, all remaining slides, Lowe ratio range `0.65..0.82`, minimum projected area ratio `0.03`, and homography-normalized similarity enabled.
- Match score weights are `0.35` inlier ratio, `0.25` match strength, `0.25` spatial coverage, `0.10` reprojection quality, and `0.05` geometry quality.
- Retry when overall confidence `< 0.80`, minimum transition confidence `< 0.55`, unmatched-video ratio `> 0.15`, the path is ambiguous, or validation finds a critical anomaly.
- Use source video frames for detection and `audio_duration_ms` for renderer validation; reject a video/audio duration difference above `max(1_000 ms, 1% of audio duration)`.
- A leading or trailing `NON_SLIDE` run longer than `5_000 ms` is low confidence; otherwise extend the first/last accepted slide to `0`/audio duration for renderer compatibility.
- Keep at most one queue job running and avoid loading the full video into memory.
- Add `numpy>=2,<3` and `opencv-python-headless>=4.10,<5`; Windows/PyInstaller verification must execute `cv2.SIFT_create()`.
- Manual single-job behavior remains unchanged: its timeline is required before enqueue.
- Existing `batch_workflow.py` 40-slide merged-deck conventions remain compatible and are not migrated to local part numbering in this feature.

## Review Focus

- Low-feature/title slides and shared logos/templates must not create confident false matches; Task 2 adds adaptive-complexity and spatial-coverage tests.
- Crossfades and brief animation/reveal frames must not create duplicate or backward transitions; Tasks 3 and 4 add persistence and boundary-dominance tests.
- Missing video streams, missing audio streams, and video/audio duration drift must fail before publication; Task 4 tests all three.
- A user timeline created while detection is running must win without data loss or ambiguous rendering; Task 5 adds before/during/after-publication race tests.
- Corrupt, partial, or version-stale feature caches must be ignored and rebuilt outside the source tree; Task 1 tests each condition.

---

### Task 1: SIFT Feature Extraction and Versioned Deck Cache

**Files:**

- Create: `video_workflow/slide_features.py`
- Create: `tests/test_slide_features.py`
- Modify: `pyproject.toml:8-20`

**Interfaces:**

- Consumes: `slide_export.count_pptx_slides()`, `slide_export.export_slides()`, PPTX bytes, `.workflow_data/timeline-cache` path, and optional `threading.Event`.
- Produces: `FeatureConfig`, `SlideReference`, `FrameFeatures`, `prepare_slide_references(pptx: Path, cache_root: Path, config: FeatureConfig, cancel_event: threading.Event | None = None) -> tuple[SlideReference, ...]`, and `extract_frame_features(frame: numpy.ndarray, *, nfeatures: int) -> FrameFeatures`.

- [ ] **Step 1: Add the CV runtime dependencies and write failing feature/cache tests**

Add `numpy>=2,<3` and `opencv-python-headless>=4.10,<5` to project dependencies. In `tests/test_slide_features.py`, add tests named:

Use these exact test names and core assertions:

```python
# test_extract_frame_features_returns_sift_points_and_float32_descriptors
assert features.points_xy.shape[1] == 2
assert features.descriptors.dtype == numpy.float32

# test_prepare_slide_references_caches_by_pptx_bytes_and_config_version
assert [item.index for item in references] == [1, 2]
assert manifest["pptx_sha256"] == hashlib.sha256(pptx.read_bytes()).hexdigest()
assert manifest["feature_cache_version"] == "slide-cv-features-v1"

# test_prepare_slide_references_reuses_complete_cache_without_exporting
assert second_references[0].image_path == first_references[0].image_path
export_slides.assert_called_once()

# test_prepare_slide_references_rebuilds_partial_corrupt_or_stale_cache
assert export_slides.call_count == 4  # initial + partial + corrupt + stale
assert json.loads(manifest_path.read_text())["complete"] is True

# test_cache_is_created_only_below_supplied_application_cache_root
assert reference.image_path.is_relative_to(cache_root)
assert not any(pptx.parent.glob("timeline-cache"))

# test_prepare_slide_references_honors_cancellation
with pytest.raises(WorkflowCancelled):
    prepare_slide_references(pptx, cache_root, config, cancelled_event)
```

The manifest also records render size, SIFT settings, slide count, entropy, and edge density and is written last.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_slide_features.py -q`

Expected: FAIL because `video_workflow.slide_features` and the new types do not exist.

- [ ] **Step 3: Implement feature types, extraction, and cache preparation**

Use these public shapes:

```python
@dataclass(frozen=True, slots=True)
class FeatureConfig:
    feature_cache_version: str = "slide-cv-features-v1"
    render_width: int = 1920
    render_height: int = 1080
    nfeatures: int = 1500

@dataclass(frozen=True, slots=True)
class FrameFeatures:
    gray: numpy.ndarray
    points_xy: numpy.ndarray
    descriptors: numpy.ndarray | None

@dataclass(frozen=True, slots=True)
class SlideReference:
    index: int
    image_path: Path
    width: int
    height: int
    points_xy: numpy.ndarray
    descriptors: numpy.ndarray | None
    entropy: float
    edge_density: float
```

Serialize arrays as `slide_001.sift.npz`. Build a unique temporary cache sibling, write artifacts, write `manifest.json` last, then promote the complete directory. Treat a missing/invalid manifest, missing image/NPZ, mismatched version/config/digest, invalid array shape, or slide-count mismatch as a cache miss.

- [ ] **Step 4: Run feature/cache tests**

Run: `python -m pytest tests/test_slide_features.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit the feature-cache deliverable**

```bash
git add pyproject.toml video_workflow/slide_features.py tests/test_slide_features.py
git commit -m "feat: cache SIFT slide references"
```

### Task 2: Robust Frame-to-Slide Matcher

**Files:**

- Create: `video_workflow/slide_matcher.py`
- Create: `tests/test_slide_matcher.py`

**Interfaces:**

- Consumes: `FrameFeatures` and `SlideReference` from Task 1.
- Produces: `MatcherConfig`, `SlideMatch`, `candidate_slide_ids(current_slide_id: int, slide_count: int, *, lookahead: int, all_remaining: bool) -> tuple[int, ...]`, and `match_slide(frame: FrameFeatures, slide: SlideReference, config: MatcherConfig) -> SlideMatch | None`.

- [ ] **Step 1: Write failing matcher tests using deterministic synthetic transforms**

Add:

Build fixtures in memory, apply fixed OpenCV transforms, and use these core assertions:

```python
# test_match_slide_accepts_resized_cropped_and_perspective_slide
assert match is not None
assert match.slide_index == 1
assert 0.0 <= match.score <= 1.0

# test_match_slide_localizes_slide_inside_presenter_split_screen
assert match is not None
assert match.projected_area_ratio >= 0.08

# test_match_slide_rejects_logo_only_cluster_with_low_spatial_coverage
assert logo_only_match is None or logo_only_match.spatial_coverage < 0.25

# test_match_slide_adapts_minimum_inliers_for_low_complexity_slide
assert low_complexity_match is not None
assert low_complexity_match.ransac_inliers < dense_reference_required_inliers

# test_match_slide_rejects_non_convex_tiny_and_out_of_frame_projection
assert [match_slide(frame, item, config) for item in invalid_refs] == [None, None, None]

# test_candidate_search_uses_current_plus_four_then_can_expand_forward_only
assert candidate_slide_ids(8, 20, lookahead=4, all_remaining=False) == (8, 9, 10, 11, 12)
assert candidate_slide_ids(8, 10, lookahead=4, all_remaining=True) == (8, 9, 10)
```

For each transformed fixture, also assert the intended slide score exceeds every unrelated-reference score.

- [ ] **Step 2: Run matcher tests to verify they fail**

Run: `python -m pytest tests/test_slide_matcher.py -q`

Expected: FAIL because `slide_matcher` does not exist.

- [ ] **Step 3: Implement FLANN/RANSAC matching and scoring**

Use `matcher.knnMatch(slide.descriptors, frame.descriptors, k=2)`, Lowe ratio `0.72`, `cv2.findHomography(src_points, dst_points, cv2.RANSAC, 4.0)`, projected-corner validation, and `4×4` inlier-cell coverage. `SlideMatch` contains `slide_index`, `score`, `good_matches`, `ransac_inliers`, `inlier_ratio`, `spatial_coverage`, `reprojection_error`, and `projected_area_ratio`.

Normalize each score component to `0..1`, use the global score weights, derive minimum inliers from descriptor count/entropy/edge density, and return `None` when homography or geometry is invalid.

- [ ] **Step 4: Run matcher tests**

Run: `python -m pytest tests/test_slide_matcher.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit the matcher deliverable**

```bash
git add video_workflow/slide_matcher.py tests/test_slide_matcher.py
git commit -m "feat: match video frames to slide geometry"
```

### Task 3: Monotonic Sequence Solver and Confidence Model

**Files:**

- Create: `video_workflow/slide_sequence.py`
- Create: `tests/test_slide_sequence.py`

**Interfaces:**

- Consumes: timestamped `SlideMatch` candidates from Task 2.
- Produces: `NON_SLIDE = -1`, `FrameObservation`, `SequenceConfig`, `SequenceRun`, `SlideSequence`, and `solve_monotonic_sequence(observations: Sequence[FrameObservation], slide_count: int, config: SequenceConfig) -> SlideSequence`.

- [ ] **Step 1: Write failing DP and confidence tests**

Add:

Use fixed observations and these core assertions:

```python
# test_solver_never_moves_backward
assert sequence.slide_ids == (5, 6)
assert all(left <= right for left, right in pairwise(sequence.slide_ids))

# test_solver_allows_real_skips_with_logarithmic_penalty
assert sequence.slide_ids == (1, 4)
assert sequence.skipped_slides == (2, 3)

# test_solver_requires_two_coarse_samples_before_transition
assert solve(ids=[1, 2, 1]).slide_ids == (1,)
assert solve(ids=[1, 2, 2]).slide_ids == (1, 2)

# test_solver_ignores_one_frame_animation_or_logo_spike
assert solve(ids=[1, 1, 8, 1, 2, 2]).slide_ids == (1, 2)

# test_solver_models_only_leading_and_trailing_non_slide_runs
assert solve(ids=[NON_SLIDE, 1, 1, NON_SLIDE]).slide_ids == (1,)
with pytest.raises(SequenceError, match="interior NON_SLIDE"):
    solve(ids=[1, NON_SLIDE, 2])

# test_confidence_uses_minimum_transition_unmatched_ratio_and_path_margin
assert sequence.minimum_transition_confidence == pytest.approx(0.55)
assert sequence.unmatched_video_ratio == pytest.approx(0.20)

# test_ambiguous_path_requests_retry_even_when_mean_score_is_high
assert sequence.overall_confidence > 0.80
assert sequence.ambiguous is True
assert sequence.needs_retry is True
```

- [ ] **Step 2: Run sequence tests to verify they fail**

Run: `python -m pytest tests/test_slide_sequence.py -q`

Expected: FAIL because `slide_sequence` does not exist.

- [ ] **Step 3: Implement Viterbi-like forward-only DP**

Score observation evidence plus persistence/transition priors and subtract `lambda * log(1 + skipped_count)` plus instability penalties. Forbid backward transitions and interior `NON_SLIDE`; expose runs, skipped slide IDs, matched/unmatched ratios, best-vs-second path margin, per-transition confidence, minimum transition confidence, overall confidence, and `needs_retry`.

- [ ] **Step 4: Run sequence tests**

Run: `python -m pytest tests/test_slide_sequence.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit the solver deliverable**

```bash
git add video_workflow/slide_sequence.py tests/test_slide_sequence.py
git commit -m "feat: solve monotonic slide sequences"
```

### Task 4: Two-Pass Detection, Boundary Refinement, and Diagnostics

**Files:**

- Modify: `video_workflow/slide_alignment.py:1-380`
- Modify: `tests/test_slide_alignment.py:1-190`

**Interfaces:**

- Consumes: cached `SlideReference` values, matcher from Task 2, solver from Task 3, `probe_media()`, FFmpeg, FPS, and optional cancellation event.
- Produces: `AlignmentConfig`, `AlignmentResult(timeline: Timeline, report: dict[str, object])`, `AlignmentError(code: str, report: dict[str, object])`, `detect_slide_timeline(video: Path, references: Sequence[SlideReference], *, fps: int = 24, config: AlignmentConfig = AlignmentConfig(), cancel_event: threading.Event | None = None, debug_dir: Path | None = None) -> AlignmentResult`, and backward-compatible `align_video_to_slides(video: Path, slide_images: Sequence[Path], slide_ids: Sequence[int], timeline_path: Path, report_path: Path, config: AlignmentConfig = AlignmentConfig()) -> AlignmentReport` for `batch_workflow.py`.

- [ ] **Step 1: Replace pixel-similarity expectations with failing detector tests**

Keep compatibility tests for `align_video_to_slides`, then add:

Use fake frame iterators and fake matcher results for orchestration tests, with these core assertions:

```python
# test_detector_streams_frames_and_forces_feature_match_every_four_seconds
assert max(live_frame_counts) == 1
assert real_match_timestamps == [0, 4_000, 8_000]

# test_detector_retries_at_four_fps_with_all_remaining_candidates
assert sampled_intervals_ms == [1_000, 250]
assert retry_matcher_configs[0].nfeatures == 3_500
assert retry_matcher_configs[0].all_remaining is True

# test_detector_refines_transition_at_six_fps_then_native_fps
assert refinement_rates == [6, 24]
assert abs(result.timeline.slides[0].end_ms - 2_125) <= 42

# test_detector_uses_new_slide_dominance_for_crossfade_boundary
assert result.timeline.slides[0].end_ms == first_of_three_persistent_new_frames

# test_detector_returns_contiguous_local_id_timeline_covering_audio
assert [cue.slide_id for cue in result.timeline.slides] == [1, 3]
assert result.timeline.slides[0].start_ms == 0
assert result.timeline.slides[0].end_ms == result.timeline.slides[1].start_ms
assert result.timeline.slides[-1].end_ms == 10_000

# test_detector_rejects_missing_video_stream_missing_audio_and_duration_drift
assert raised_codes == [
    "SLIDE_ALIGNMENT_VIDEO_REQUIRED",
    "SLIDE_ALIGNMENT_VIDEO_REQUIRED",
    "SLIDE_ALIGNMENT_DURATION_MISMATCH",
]

# test_detector_rejects_long_edge_non_slide_regions
assert exc.value.code == "SLIDE_ALIGNMENT_LOW_CONFIDENCE"

# test_detector_low_confidence_raises_with_diagnostic_report
assert exc.value.report["status"] == "failed"
assert exc.value.report["retry_used"] is True
assert (debug_dir / "observations.json").is_file()
assert any(debug_dir.glob("transition_*/*.jpg"))

# test_detector_invalid_cues_use_coded_failure
assert exc.value.code == "SLIDE_ALIGNMENT_INVALID_OUTPUT"

# test_detector_cancellation_stops_sampling_and_raises_workflow_cancelled
with pytest.raises(WorkflowCancelled):
    detect_slide_timeline(video, references, cancel_event=cancelled_event)
```

Keep one small synthetic OpenCV test for homography-normalized comparison.

- [ ] **Step 2: Run alignment tests to verify the new tests fail**

Run: `python -m pytest tests/test_slide_alignment.py -q`

Expected: FAIL on missing two-pass/result/error interfaces.

- [ ] **Step 3: Implement streaming sampling and cheap stability filtering**

Replace the current all-images-in-memory sampling path with an iterator that reads extracted frames one at a time, releases each frame, checks cancellation between frames, and retains only `FrameObservation` values. Downscale to `320×180`; use grayscale absolute difference plus histogram distance; skip SIFT only while stable and a recent confident state exists; force a real match every `4_000 ms`.

- [ ] **Step 4: Implement normal/retry passes and candidate expansion**

Normal pass uses global defaults. If solver confidence triggers retry, run a genuinely denser 4 FPS profile with 3500 features, all forward candidates, retry geometry threshold, adaptive Lowe ratio, and normalized ROI similarity. Record which pass produced every observation and `retry_used`.

- [ ] **Step 5: Implement coarse-to-fine transition refinement and timeline adaptation**

For each rough transition, scan `±2_000 ms` at 6 FPS, then native FPS around the narrowed boundary. Select the first frame where the new slide dominates and remains dominant for at least three frames. Convert accepted runs to `SlideCue` values, extend acceptable leading/trailing non-slide runs to `0`/audio duration, and validate with `validate_timeline()`.

- [ ] **Step 6: Implement deterministic reports and compatibility adapter**

Include detector/config/cache versions, timings, sampled/SIFT frame counts, skipped slides, retry flag, overall/minimum confidence, unmatched ratio, segments, transitions, and error details. On retry/failure or when `debug_dir` is supplied, retain boundary frames, slide references, homography overlays, `observations.json`, and `sequence_path.json`; normal confident jobs do not retain frame artifacts. Preserve `align_video_to_slides(video, slide_images, slide_ids, timeline_path, report_path, config)` for `batch_workflow.py`; it may accept global IDs such as `21..40`, writes the existing JSON-array renderer timeline plus report, and delegates detection to the new engine.

- [ ] **Step 7: Run alignment and existing batch tests**

Run: `python -m pytest tests/test_slide_alignment.py tests/test_batch_workflow.py -q`

Expected: all tests PASS.

- [ ] **Step 8: Commit the detector deliverable**

```bash
git add video_workflow/slide_alignment.py tests/test_slide_alignment.py
git commit -m "feat: detect slide timelines with two-pass CV"
```

### Task 5: Timeline Naming, Conflict Resolution, and Atomic Publication

**Files:**

- Create: `video_workflow/timeline_generation.py`
- Create: `tests/test_timeline_generation.py`

**Interfaces:**

- Consumes: Task 1 reference preparation, Task 4 detector, existing `parse_timeline()`/`validate_timeline()`, and a leaf-folder PPTX/media pair.
- Produces: `find_matching_timelines(directory: Path, stem: str) -> tuple[Path, ...]`, `generated_timeline_path(pptx: Path) -> Path`, `diagnostic_path(pptx: Path) -> Path`, `publish_timeline_if_absent(staged: Path, destination: Path) -> None`, `TimelineResolver` protocol, and `resolve_or_generate_timeline(request: TimelineGenerationRequest, *, detector: DetectFunction = detect_slide_timeline, publisher: PublishFunction = publish_timeline_if_absent, cancel_event: threading.Event | None = None) -> Path`.

- [ ] **Step 1: Write failing naming, validation, and race tests**

Add:

Use these core assertions:

```python
# test_matching_timelines_accepts_both_existing_name_forms_case_insensitively
assert find_matching_timelines(leaf, "LESSON_1") == (direct_json, prefixed_txt)

# test_generated_and_diagnostic_paths_follow_the_spec
assert generated_timeline_path(pptx) == leaf / "timeline_slide_lesson_1.json"
assert diagnostic_path(pptx) == leaf / "output/.timeline-diagnostics/lesson_1.alignment.json"

# test_existing_single_timeline_wins_without_calling_detector
assert resolve_or_generate_timeline(request, detector=detector) == existing
detector.assert_not_called()

# test_invalid_existing_timeline_is_returned_not_replaced
assert resolve_or_generate_timeline(request, detector=detector) == invalid_existing
assert invalid_existing.read_bytes() == original_bytes

# test_more_than_one_existing_timeline_raises_conflict
with pytest.raises(AlignmentError) as exc:
    resolve_or_generate_timeline(request)
assert exc.value.code == "SLIDE_ALIGNMENT_TIMELINE_CONFLICT"

# test_success_publishes_renderer_array_only_after_validation
assert json.loads(result.read_text()) == expected_rows
assert isinstance(json.loads(result.read_text()), list)
assert validate_timeline(parse_timeline(result), 2, 4_000, 24)

# test_low_confidence_writes_diagnostics_but_no_renderer_timeline
assert json.loads(diagnostics.read_text())["status"] == "failed"
assert not intended_timeline.exists()

# test_publish_never_clobbers_destination_created_during_detection
assert destination.read_bytes() == b"user timeline"

# test_alternate_user_timeline_created_during_publish_wins
assert resolved == alternate_user_timeline
assert alternate_user_timeline.read_bytes() == b"user timeline"
assert not generated_timeline.exists()

# test_cancelled_generation_removes_temporary_timeline
assert not intended_timeline.exists()
assert list(leaf.glob(".*.tmp-*")) == []
```

The race tests mutate the folder through an injected pre-publication hook and assert exact user bytes survive.

- [ ] **Step 2: Run generation tests to verify they fail**

Run: `python -m pytest tests/test_timeline_generation.py -q`

Expected: FAIL because `timeline_generation` does not exist.

- [ ] **Step 3: Implement resolve/generate behavior**

Use:

```python
@dataclass(frozen=True, slots=True)
class TimelineGenerationRequest:
    source_media: Path
    pptx: Path
    intended_timeline: Path
    cache_root: Path
    diagnostics_path: Path
    debug_dir: Path
    fps: int = 24
```

Define `TimelineResolver` with the same `request` and keyword-only `cancel_event` call shape used by the worker. Re-scan all matching names before detection, immediately before publication, and after publication. One user timeline wins even if malformed; multiple candidates raise `SLIDE_ALIGNMENT_TIMELINE_CONFLICT`. Pass `debug_dir` and `cancel_event` to detection. Write diagnostics atomically and allow them to be replaced on retry.

- [ ] **Step 4: Implement no-clobber publication**

Write the renderer payload to a unique temporary sibling, flush and close it, then use Windows `rename` semantics or POSIX `os.link`/unlink as already established in `pipeline._publish_video()`. Never use check-then-`Path.replace()`. If an alternate matching timeline appears after this job publishes, remove only this job's generated path and return the user path; never remove a user-created file.

- [ ] **Step 5: Run generation tests**

Run: `python -m pytest tests/test_timeline_generation.py -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit the publication deliverable**

```bash
git add video_workflow/timeline_generation.py tests/test_timeline_generation.py
git commit -m "feat: publish generated timelines without clobbering"
```

### Task 6: Persist Generation Intent and Relax Queue Validation Safely

**Files:**

- Modify: `video_workflow/job_models.py:24-218`
- Modify: `video_workflow/queue_controller.py:50-99,210-230`
- Modify: `tests/test_job_store.py:15-190`
- Modify: `tests/test_queue_controller.py:1-260`

**Interfaces:**

- Consumes: existing `JobRecord` JSON and queue validation.
- Produces: `JobStage.GENERATING_TIMELINE`, `JobRecord.generate_timeline_if_missing: bool`, and `QueueController.enqueue(*, source_media: Path, pptx: Path, timeline: Path, output_name: str, output_directory: Path, write_report: bool = True, use_outro: bool = True, overwrite_output: bool = False, generate_timeline_if_missing: bool = False) -> JobRecord`.

- [ ] **Step 1: Write failing model and queue tests**

Add assertions that:

Use these core assertions:

```python
# test_generation_flag_round_trips_and_legacy_job_defaults_false
assert JobRecord.from_dict(flagged.to_dict()).generate_timeline_if_missing is True
legacy = flagged.to_dict(); legacy.pop("generate_timeline_if_missing")
assert JobRecord.from_dict(legacy).generate_timeline_if_missing is False

# test_job_stages_include_generating_timeline
assert JobStage.GENERATING_TIMELINE.value == "generating_timeline"

# test_flagged_slide_job_accepts_only_its_missing_timeline_path
job = _enqueue(controller, timeline=missing, generate_timeline_if_missing=True)
assert job.timeline == missing

# test_unflagged_manual_job_still_rejects_missing_timeline
with pytest.raises(ValueError, match="timeline file does not exist"):
    _enqueue(controller, timeline=missing, generate_timeline_if_missing=False)

# test_flag_does_not_relax_missing_source_pptx_or_output_directory
assert enqueue_error_labels == ["source_media", "pptx", "output directory"]

# test_retry_accepts_flagged_job_before_generation_and_after_publication
assert controller.retry(failed_before_generation.id).status is JobStatus.WAITING
intended.write_text(valid_timeline_json)
assert controller.retry(failed_after_render.id).status is JobStatus.WAITING
```

- [ ] **Step 2: Run focused tests to verify they fail**

Run: `python -m pytest tests/test_job_store.py tests/test_queue_controller.py -q`

Expected: FAIL on the missing flag/stage/signature.

- [ ] **Step 3: Implement backward-compatible persistence and validation**

Add the flag to `JobRecord.new()`, `to_dict()`, and `from_dict()` with default `False`. In `_validate_job_paths()`, omit only the timeline existence check when the job is a `SLIDE` job and the flag is true; require a non-`None` intended timeline path in every slide job. Do not change merge validation, global assets, FIFO behavior, output overwrite behavior, or missing-path restoration.

- [ ] **Step 4: Run model/queue tests**

Run: `python -m pytest tests/test_job_store.py tests/test_queue_controller.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit the queue-schema deliverable**

```bash
git add video_workflow/job_models.py video_workflow/queue_controller.py tests/test_job_store.py tests/test_queue_controller.py
git commit -m "feat: persist missing-timeline generation intent"
```

### Task 7: Discover Missing Timelines in Leaf Folders

**Files:**

- Modify: `video_workflow/folder_batch.py:10-195`
- Modify: `tests/test_folder_batch.py:1-164`

**Interfaces:**

- Consumes: `find_matching_timelines()` and `generated_timeline_path()` from Task 5, and generation-aware enqueue from Task 6.
- Produces: `FolderJob.generate_timeline_if_missing` and the exact discovery decision table in spec section 3.2.

- [ ] **Step 1: Update discovery tests to the approved behavior**

Replace the current `missing timeline` expectation for an MP4/PPTX pair and add:

Use these core assertions:

```python
# test_missing_video_timeline_creates_flagged_job_with_generated_json_target
assert result.jobs[0].timeline == leaf / "timeline_slide_LESSON_1.json"
assert result.jobs[0].generate_timeline_if_missing is True

# test_audio_only_media_without_timeline_is_an_issue_not_a_job
assert result.jobs == ()
assert result.issues[0].message == "LESSON_1: timeline generation requires video"

# test_existing_timeline_remains_unflagged_and_unchanged
assert result.jobs[0].timeline == existing
assert result.jobs[0].generate_timeline_if_missing is False
assert existing.read_bytes() == original_bytes

# test_multiple_timeline_forms_remain_ambiguous
assert result.jobs == ()
assert result.issues[0].message == "LESSON_1: multiple timeline files"

# test_part_one_and_two_each_use_local_generated_targets
assert [job.timeline.name for job in result.jobs] == [
    "timeline_slide_LESSON_1.json", "timeline_slide_LESSON_2.json"
]

# test_workflow_data_cache_outside_source_does_not_change_leaf_discovery
assert [job.pptx.parent for job in result.jobs] == [leaf]

# test_rescan_queues_duplicate_jobs_that_share_the_same_intended_timeline
assert len(controller.jobs()) == 2
assert controller.jobs()[0].timeline == controller.jobs()[1].timeline
```

- [ ] **Step 2: Run folder tests to verify they fail**

Run: `python -m pytest tests/test_folder_batch.py -q`

Expected: the old missing-timeline test fails and the new field/helper is absent.

- [ ] **Step 3: Implement video/audio-aware discovery**

Split `VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv"}` from audio-only extensions while retaining the combined media matcher. With zero timelines, queue video using `timeline_slide_<stem>.json` plus flag true; report `<stem>: timeline generation requires video` for audio-only media. With one timeline, flag false; with more than one, retain the ambiguity issue.

- [ ] **Step 4: Pass the generation flag through `queue_folder_jobs()`**

Preserve output naming, report deletion, overwrite behavior, and part-2 outro behavior. Do not deduplicate rescans; publication/worker resolution handles shared targets safely.

- [ ] **Step 5: Run folder and queue tests**

Run: `python -m pytest tests/test_folder_batch.py tests/test_queue_controller.py -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit the discovery deliverable**

```bash
git add video_workflow/folder_batch.py tests/test_folder_batch.py
git commit -m "feat: queue timeline generation for leaf videos"
```

### Task 8: Generate Within the Existing Worker Lifecycle

**Files:**

- Modify: `video_workflow/worker.py:35-160`
- Modify: `tests/test_worker.py:1-430`

**Interfaces:**

- Consumes: generation-aware `JobRecord` and `resolve_or_generate_timeline()` from Task 5.
- Produces: `QueueWorker(controller: QueueController, *, build: BuildFunction = build_video, merge_build: BuildFunction | None = merge_videos, timeline_resolver: TimelineResolver = resolve_or_generate_timeline, events: queue.Queue[WorkerEvent] | None = None)` and a real timeline path passed into the unchanged `BuildRequest`.

- [ ] **Step 1: Write failing worker lifecycle tests**

Add:

Inject a fake resolver; do not invoke OpenCV or PowerPoint. Use these core assertions:

```python
# test_flagged_job_generates_then_builds_with_resolved_timeline
assert resolver.call_count == 1
assert build_requests[0].timeline == resolved_timeline

# test_flagged_job_reuses_timeline_that_exists_when_worker_starts
assert resolver.call_count == 1
assert build_requests[0].timeline == existing_timeline

# test_render_retry_reuses_published_timeline_without_redetecting
assert resolver.call_count == 2
assert detector_calls == 1
assert len(build_requests) == 2

# test_generation_failure_never_calls_builder_and_marks_failed
assert controller.jobs()[0].status is JobStatus.FAILED
assert build_requests == []

# test_generation_cancellation_marks_interrupted_and_never_builds
assert controller.jobs()[0].status is JobStatus.INTERRUPTED
assert build_requests == []

# test_worker_emits_generating_timeline_stage_before_render_stages
assert emitted_stages.index("generating_timeline") < emitted_stages.index("validating")

# test_two_queued_jobs_for_same_target_generate_once_then_both_render
assert detector_calls == 1
assert len(build_requests) == 2

# test_part_two_generation_still_receives_global_outro
assert build_requests[0].outro == controller.settings.outro
```

- [ ] **Step 2: Run worker tests to verify they fail**

Run: `python -m pytest tests/test_worker.py -q`

Expected: FAIL because `QueueWorker` has no resolver or generation stage.

- [ ] **Step 3: Resolve timelines before creating `BuildRequest`**

For every flagged slide job, call the injected resolver so it re-checks all accepted timeline names and catches ambiguity. Emit `generating_timeline` when the pre-call matching set is empty. Build `TimelineGenerationRequest` with cache root `controller.store.root / "timeline-cache"`, diagnostics path from Task 5, debug directory `<diagnostics-parent>/<pptx-stem>.debug`, and FPS `24`; pass the worker cancel event as the resolver keyword argument. Use the returned path in `BuildRequest`; do not mutate or re-persist the job's intended target.

- [ ] **Step 4: Map generation errors and cancellation into existing job states**

Translate alignment cancellation into `WorkflowCancelled`; allow coded alignment errors to populate the existing job error string and `FAILED` state. Keep merge dispatch, COM lifecycle, stop boundaries, serial execution, and completion events unchanged.

- [ ] **Step 5: Run worker and queue tests**

Run: `python -m pytest tests/test_worker.py tests/test_queue_controller.py -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit the worker deliverable**

```bash
git add video_workflow/worker.py tests/test_worker.py
git commit -m "feat: generate timelines inside slide jobs"
```

### Task 9: Benchmark Harness and Release Metrics

**Files:**

- Create: `scripts/benchmark_slide_alignment.py`
- Create: `tests/test_slide_alignment_benchmark.py`
- Modify: `pyproject.toml:18-28`
- Modify: `README.md:120-160`

**Interfaces:**

- Consumes: a user-supplied golden dataset manifest containing video path, PPTX path, expected slide sequence, skipped slides, and ground-truth transition frames.
- Produces: `benchmark-slide-alignment` CLI and JSON summary containing exact-sequence accuracy, skip precision/recall, median/P95/max boundary error, incorrect-high-confidence rate, runtime/video-duration ratio, and peak RSS.

- [ ] **Step 1: Write failing benchmark aggregation tests**

Add deterministic unit tests:

Use these core assertions:

```python
# test_benchmark_computes_sequence_skip_and_boundary_metrics
assert summary.exact_sequence_accuracy == pytest.approx(0.5)
assert summary.skip_precision == pytest.approx(1.0)
assert summary.skip_recall == pytest.approx(0.5)
assert summary.median_boundary_error_ms == pytest.approx(100)
assert summary.p95_boundary_error_ms == pytest.approx(200)

# test_benchmark_counts_wrong_high_confidence_result
assert summary.incorrect_high_confidence_rate == pytest.approx(0.5)

# test_benchmark_reports_runtime_ratio_and_peak_memory
assert summary.median_runtime_ratio == pytest.approx(0.25)
assert summary.peak_rss_bytes == 400_000_000

# test_benchmark_rejects_malformed_or_missing_ground_truth
with pytest.raises(BenchmarkInputError):
    load_manifest(missing_transition_frames_manifest)
```

- [ ] **Step 2: Run benchmark tests to verify they fail**

Run: `python -m pytest tests/test_slide_alignment_benchmark.py -q`

Expected: FAIL because the benchmark module/CLI does not exist.

- [ ] **Step 3: Implement the benchmark CLI and document the release gate**

The CLI accepts `--manifest`, `--output`, and optional `--fail-on-target-miss`. It does not commit real course media. Document the expected 30–50-video private dataset and targets: exact sequence/skip precision/skip recall `>=98%`, median boundary error `<=100 ms`, P95 `<=250 ms`, incorrect high-confidence `<0.5%`, normal median runtime `<=0.35×`, and peak RAM `<1 GB`.

- [ ] **Step 4: Run benchmark unit tests**

Run: `python -m pytest tests/test_slide_alignment_benchmark.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit the benchmark deliverable**

```bash
git add scripts/benchmark_slide_alignment.py tests/test_slide_alignment_benchmark.py pyproject.toml README.md
git commit -m "test: add slide alignment benchmark harness"
```

### Task 10: UI Readiness, Setup, Packaging, Documentation, and Full Verification

**Files:**

- Modify: `video_workflow/ui.py:20-39,550-610`
- Modify: `tests/test_ui.py:234-315`
- Modify: `scripts/setup.ps1:92-145`
- Modify: `setup.sh:110-145`
- Modify: `windows_launcher.py:1-5`
- Modify: `scripts/build_windows.py:8-80`
- Modify: `tests/test_windows_build.py:1-75`
- Modify: `tests/test_windows_setup.py`
- Modify: `tests/test_linux_scripts.py`
- Modify: `README.md:39-125`

**Interfaces:**

- Consumes: `JobRecord.generate_timeline_if_missing`, `JobStage.GENERATING_TIMELINE`, OpenCV runtime, and generated output paths.
- Produces: accurate UI readiness/status text, dependency self-checks, packaged SIFT smoke check, and user-facing Batch Folder documentation.

- [ ] **Step 1: Write failing UI/setup/package tests**

Add:

Use these core assertions:

```python
# test_flagged_waiting_job_does_not_report_intended_timeline_missing
assert "timeline" not in app._missing_job_paths(flagged_job)
assert app._waiting_job_ready(flagged_job) is True

# test_batch_guide_explains_video_auto_generation_and_audio_only_limit
assert "tự tạo timeline" in BATCH_FOLDER_GUIDE
assert "audio-only" in BATCH_FOLDER_GUIDE
assert "timeline_slide_<tên PPTX>.json" in BATCH_FOLDER_GUIDE

# test_setup_scripts_check_cv2_sift_create
assert "cv2.SIFT_create()" in windows_setup
assert "cv2.SIFT_create()" in linux_setup

# test_windows_build_collects_cv2_and_runs_packaged_sift_self_check
assert command[command.index("--collect-all") + 1] == "cv2"
assert packaged_check_call[-1] == "--self-check-cv"

# test_windows_launcher_self_check_constructs_sift_without_starting_ui
assert windows_launcher.main(["--self-check-cv"]) == 0
ui_main.assert_not_called()
```

Update the existing Batch Folder UI test so an MP4/PPTX pair without a timeline queues successfully and reports zero issues.

- [ ] **Step 2: Run focused tests to verify they fail**

Run: `python -m pytest tests/test_ui.py tests/test_windows_build.py tests/test_windows_setup.py tests/test_linux_scripts.py -q`

Expected: FAIL on missing readiness exception, guide text, and CV checks.

- [ ] **Step 3: Update UI readiness and Batch Folder guidance**

`_missing_job_paths()` must ignore only a missing timeline on flagged jobs; all other file indicators remain unchanged. Explain generated filename, supported video suffixes, audio-only limitation, existing-timeline precedence, diagnostic location, and the `generating_timeline` stage in `BATCH_FOLDER_GUIDE` and README.

- [ ] **Step 4: Add development and packaged SIFT checks**

Both setup scripts run `import cv2; cv2.SIFT_create()` after Python dependencies are installed/checked. `windows_launcher.py --self-check-cv` performs the same check and exits without starting Tkinter. Add `--collect-all cv2` to PyInstaller and make `scripts/build_windows.py` run the built EXE with `--self-check-cv` before declaring success.

- [ ] **Step 5: Run all unit tests**

Run: `python -m pytest tests -q`

Expected: all tests PASS with zero failures.

- [ ] **Step 6: Run static/package metadata checks**

Run: `python -m compileall -q video_workflow scripts windows_launcher.py`

Expected: exit `0`.

Run: `python -m pip check`

Expected: `No broken requirements found.`

- [ ] **Step 7: Run one real short-fixture integration**

Run: `python -m pytest tests/test_linux_integration.py -m integration -q` on Linux with FFmpeg/LibreOffice, or build the Windows portable package and run its `--self-check-cv` plus a 30–60-second PPTX/MP4 fixture on Windows.

Expected: generated JSON passes `parse_timeline()`/`validate_timeline()`, diagnostic JSON is separate, and the final MP4 passes the existing media verification. If the repository has no distributable real fixture, record this as a release-environment verification rather than fabricating a pass.

- [ ] **Step 8: Commit the integration/documentation deliverable**

```bash
git add video_workflow/ui.py tests/test_ui.py scripts/setup.ps1 setup.sh windows_launcher.py scripts/build_windows.py tests/test_windows_build.py tests/test_windows_setup.py tests/test_linux_scripts.py README.md
git commit -m "docs: expose automatic timeline workflow"
```

### Final Branch Verification

- [ ] Run `python -m pytest tests -q` and confirm zero failures.
- [ ] Run `python -m compileall -q video_workflow scripts windows_launcher.py` and confirm exit `0`.
- [ ] Run `python -m pip check` and confirm no broken requirements.
- [ ] Run `git diff --check origin/feature/recursive-folder-video-generation...HEAD` and confirm no whitespace errors.
- [ ] Review every generated renderer fixture as a top-level array and every detector report as a separate top-level object.
- [ ] Confirm `git status --short` contains no unintended artifacts, cache files, generated timelines, diagnostics, or media outputs.
- [ ] Run the private golden benchmark before release; do not claim the production accuracy/performance targets from synthetic unit tests alone.
