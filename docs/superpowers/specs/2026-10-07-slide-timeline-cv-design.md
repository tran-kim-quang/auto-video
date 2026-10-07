# Slide Timeline CV Design Spec

**Date:** 2026-10-07

**Status:** Ready for written-spec review

**Scope:** CPU-only slide timeline detection and Batch Folder integration for sequential lecture videos

**Renderer timeline output:** Existing JSON-array schema

**Diagnostic output:** Detailed JSON object

**Detector family:** Classical computer vision only; no VLM, no GPU dependency

## 1. Purpose

Build a production-ready subsystem that receives one lecture video and one PowerPoint deck, then returns the time ranges during which each slide is visibly shown. Integrate it into the existing **Batch Folder** flow so a valid timeline is generated automatically when a leaf-folder video/PPTX pair has no timeline file.

The subsystem detects **visual slide presence**, not speech-to-slide alignment.

Example:

```text
Slide 01: 00:00.000 --> 00:34.667
Slide 02: 00:34.667 --> 01:15.333
Slide 04: 01:15.333 --> 01:52.083
```

Slide 03 is skipped in this example.

Success means:

- Batch Folder continues to use an existing timeline without modifying it.
- A missing timeline for a video source is generated, validated, atomically published, and then consumed by the existing renderer in the same slide job.
- Audio-only sources without a timeline remain explicit scan issues because visual alignment is impossible.
- Low-confidence detection never becomes a normal render timeline.

## 2. Agreed Constraints

### 2.1 Slide order

Slides are monotonic.

Allowed:

```text
1 → 2 → 3 → 4
1 → 3 → 4
5 → 8
```

Forbidden:

```text
7 → 6
8 → 3
```

Slides may be skipped, but the video never navigates backward.

### 2.2 Video layouts

The detector must support:

1. slide nearly full-screen,
2. resized/cropped slide with borders, subtitles, or logos,
3. split-screen/presenter layouts where the slide occupies only part of the frame.

### 2.3 Animation

Incremental reveals/simple slide animations may occur but are uncommon. Internal animation must not be treated as a slide transition unless there is positive evidence for a later slide.

### 2.4 Runtime

- CPU only.
- One video processed per queue worker at a time.
- Accuracy has priority over maximum throughput.
- Use coarse-to-fine analysis to minimize runtime.
- No VLM, CLIP, CNN, YOLO, external inference API, or required GPU.

### 2.5 Low-confidence behavior

Use two passes:

1. normal pass,
2. aggressive retry if confidence is insufficient.

If aggressive retry also fails, fail the job with diagnostics rather than silently emitting an unreliable timeline.

### 2.6 Existing codebase compatibility

The implementation extends the current `video_workflow/folder_batch.py` → `QueueController` → `QueueWorker` → `build_video` path. It does not introduce a second batch system or a new timeline job type.

The existing filename contract remains in force:

- a batch PPTX stem ends in `_1` or `_2`,
- media has the same stem as the PPTX,
- an existing timeline may be `<stem>.txt`, `<stem>.json`, `timeline_slide_<stem>.txt`, or `timeline_slide_<stem>.json`,
- generated timelines use `timeline_slide_<stem>.json`,
- slide IDs are local to each PPTX and run from `1..N`; part `_2` does not start at slide 21.

An existing timeline is user-owned and authoritative. Automatic generation only applies when no matching timeline exists. The workflow never silently replaces an existing but invalid timeline.

## 3. High-Level Architecture

```text
video.mp4 + deck.pptx
        │
        ▼
┌──────────────────────┐
│ 1. PPT Preprocessor  │
│ render PNG           │
│ compute SIFT         │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ 2. Video Sampler     │
│ coarse sampling      │
│ cheap stability      │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ 3. Slide Matcher     │
│ SIFT + FLANN         │
│ RANSAC homography    │
│ geometry scoring     │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ 4. Sequence Solver   │
│ monotonic DP         │
│ allow skip           │
│ persistence          │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ 5. Boundary Refiner  │
│ narrow window scan   │
│ frame-level boundary │
└──────────┬───────────┘
           │
           ▼
       confidence
        /       \
      high      low
       │         │
       │         ▼
       │   ┌──────────────────┐
       │   │ Aggressive Retry │
       │   └────────┬─────────┘
       │            │
       └──────┬─────┘
              ▼
┌─────────────────────────┐
│ 6. Timeline Validator   │
│ ordering / anomalies    │
│ overall confidence      │
└────────────┬────────────┘
             ▼
      validated result
```

### 3.1 Batch Folder integration

```text
Scan leaf folder
      │
      ├── one timeline ───────────────► queue normal SLIDE job
      │
      ├── multiple timelines ─────────► report ambiguous input; do not queue
      │
      ├── no timeline + audio-only ───► report unsupported input; do not queue
      │
      └── no timeline + video
                 │
                 ▼
        queue SLIDE job with
        generate_timeline_if_missing=true
                 │
                 ▼
        worker re-checks target
          │                 │
          │ exists/valid    │ still missing
          ▼                 ▼
        render       detect → validate → atomic publish
                                   │
                                   ▼
                                 render
```

Timeline generation is a stage of the existing `SLIDE` job, not a separate `JobKind`. This keeps serial execution, cancellation, retry, persistence, and UI reporting inside the current queue lifecycle.

The worker re-runs matching-timeline discovery immediately before generation and again before publication because the user may create a timeline after scanning, or two queued scans may target the same file. If exactly one matching timeline now exists, it is authoritative and follows normal validation/rendering; if more than one exists, the job fails with a conflict. A generated file must never overwrite a timeline that appeared after enqueue.

### 3.2 Discovery decisions

For each valid `_1`/`_2` PPTX in a leaf folder, discovery makes exactly one of these decisions:

| Matching media | Matching timelines | Result |
|---|---:|---|
| None | Any | Existing `missing media` issue |
| More than one | Any | Existing `multiple media files` issue |
| One | More than one | Existing `multiple timeline files` issue |
| One | One | Queue with `generate_timeline_if_missing=false` |
| One video (`.mp4`, `.mov`, `.mkv`) | None | Queue intended generated path with `generate_timeline_if_missing=true` |
| One audio-only file (`.mp3`, `.wav`, `.m4a`, `.aac`) | None | `timeline generation requires video` issue; do not queue |

Discovery does not overwrite or repair an existing timeline. The existing render-validation path reports malformed, out-of-range, or duration-invalid user timelines. Only absence enables generation.

## 4. Component Contracts

```python
PptPreprocessor.prepare(pptx_path) -> list[SlideReference]

VideoSampler.sample(video_path) -> Iterator[VideoFrame]

SlideMatcher.match(
    frame,
    candidate_slides,
) -> list[SlideMatch]

SequenceSolver.solve(
    observations,
    slide_count,
) -> SlideSequence

BoundaryRefiner.refine(
    video_path,
    before_slide,
    after_slide,
    rough_window,
) -> Transition

TimelineValidator.validate(
    sequence,
    transitions,
    duration,
    slide_count,
) -> ValidationResult
```

Folder discovery and queue integration add these contracts:

```python
FolderJob(
    source_media: Path,
    pptx: Path,
    timeline: Path,  # existing path or intended generated path
    generate_timeline_if_missing: bool,
    output: Path,
    part: int,
)

JobRecord(
    ...,
    timeline: Path,
    generate_timeline_if_missing: bool = False,
)
```

`generate_timeline_if_missing` defaults to `False` when deserializing older jobs. Manual single jobs retain their present requirement that a timeline file already exists.

Add `JobStage.GENERATING_TIMELINE`. The worker resolves a timeline before constructing `BuildRequest`; `build_video` itself continues to receive a real, validated timeline path and does not need to know whether it was generated.

Diagnostic match object:

```python
SlideMatch(
    slide_index=8,
    score=0.87,
    good_matches=73,
    ransac_inliers=58,
    inlier_ratio=0.79,
    spatial_coverage=0.68,
    reprojection_error=2.3,
    projected_area_ratio=0.34,
)
```

## 5. PPT Preprocessing

### 5.1 Render

Render every PPT slide to a deterministic image representation.

Recommended reference size:

```text
1920×1080 or original 16:9 equivalent
```

### 5.2 Precompute SIFT

For each slide, compute once:

- grayscale image,
- optional CLAHE-normalized image,
- SIFT keypoints,
- SIFT descriptors,
- dimensions,
- keypoint count,
- edge density,
- image entropy.

```python
SlideReference(
    index=8,
    image=...,
    keypoints=...,
    descriptors=...,
    width=1920,
    height=1080,
    complexity=...,
)
```

### 5.3 Adaptive slide complexity

A title-only slide may have few useful features while a dense diagram slide may have hundreds.

Thresholds must adapt to:

```text
keypoint_count
descriptor_count
entropy
edge_density
```

Do not use one absolute minimum-inlier threshold for all slides.

## 6. Feature Cache

Cache PPT artifacts using:

```text
SHA256(PPTX bytes)
+
feature-config version
```

Store cache under the application's existing data root, outside the selected source tree:

```text
.workflow_data/
  timeline-cache/
    <deck_hash>/
      manifest.json
      slide_001.png
      slide_001.sift.npz
      ...
```

Reuse cache on retries or repeated processing. Changing render/SIFT configuration invalidates old cache entries.

Do not create a cache directory below a lesson/source folder. `_leaf_directories()` treats any non-`output` child directory as structural input, so an in-tree cache would change which directories are considered leaves.

## 7. Video Sampling

### 7.1 Normal pass

Default coarse sampling:

```text
~1 FPS
```

For a 15-minute 24 FPS video:

```text
raw frames    ≈ 21,600
coarse frames ≈ 900
```

### 7.2 Cheap stability detector

Downscale frames, e.g.:

```text
1920×1080 → 320×180
```

Compute:

- grayscale absolute difference,
- histogram distance.

If stable and the current slide has a recent high-confidence match, reuse the current state instead of running SIFT.

Force a real feature match every configurable interval, initially around `3–5 seconds`.

## 8. Core Slide Matching

### 8.1 Primary matcher

Use:

```text
SIFT + FLANN KNN + Lowe ratio + RANSAC homography
```

SIFT is the v1 production core because it is robust to scaling, crop, perspective distortion, and split-screen layout. ORB may be benchmarked as a speed optimization but is not required.

### 8.2 Descriptor matching

```python
matches = flann.knnMatch(
    slide.descriptors,
    frame.descriptors,
    k=2,
)
```

Apply Lowe ratio:

```python
good = [
    m for m, n in matches
    if m.distance < ratio_threshold * n.distance
]
```

Initial normal-pass ratio:

```text
0.72
```

Allow adaptive tuning around `0.68–0.78`.

These are initial defaults, not fixed production constants.

## 9. Homography and Slide Localization

Estimate slide-to-frame homography:

```python
H, mask = cv2.findHomography(
    src_points,
    dst_points,
    cv2.RANSAC,
    ransacReprojThreshold=4.0,
)
```

Project slide corners through `H`.

This localizes slides without a fixed ROI, including split-screen presenter layouts.

## 10. Geometry Validation

Reject or strongly penalize:

- self-intersecting polygon,
- non-convex projection,
- implausibly tiny projected area,
- extreme skew,
- mostly out-of-frame projection,
- invalid aspect distortion.

Initial projected-area expectation:

```text
normal pass:     >= ~0.08
aggressive retry >= ~0.03
```

Exact thresholds are benchmark-tuned.

## 11. Spatial Coverage

Raw match count is insufficient because recurring logos/templates can create false matches.

Divide each slide into a `4×4` grid and measure the fraction of cells containing RANSAC inliers.

Optionally combine with inlier bounding-box coverage.

This explicitly penalizes cases where many matches cluster only around a logo/header.

## 12. Match Score

Normalize to `0..1`.

Initial form:

```text
score =
    0.35 * inlier_ratio
  + 0.25 * match_strength
  + 0.25 * spatial_coverage
  + 0.10 * reprojection_quality
  + 0.05 * geometry_quality
```

Weights and normalization constants must be calibrated on the benchmark dataset before production lock.

## 13. Candidate Search

Because slide order is monotonic, do not compare each frame to all slides by default.

If current slide is 8:

```text
normal candidates:
8, 9, 10, 11, 12
```

Default:

```text
lookahead = 4
```

If all candidate scores are weak, expand to all remaining slides. Backward candidates are never considered.

## 14. Animation Handling

Key rule:

> Weak evidence for the current slide does not imply evidence for a new slide.

Internal reveal may lower current confidence, but state changes only when a later slide obtains strong persistent evidence.

## 15. Observation Model

Coarse matching emits observations rather than final labels:

```python
FrameObservation(
    timestamp_ms=341000,
    candidates=[
        SlideMatch(slide=8, score=0.31),
        SlideMatch(slide=9, score=0.88),
        SlideMatch(slide=10, score=0.22),
    ],
    frame_change_score=0.74,
)
```

## 16. Sequence Solver

Use Viterbi-like dynamic programming rather than only a greedy state machine.

Constraint:

```text
state[t+1] >= state[t]
```

Path score includes:

```text
observation evidence
+ persistence bonus
+ transition prior
- skip penalty
- instability penalty
```

Backward transitions are forbidden.

### 16.1 Skips

Use a moderate penalty such as:

```text
skip_penalty = λ * log(1 + skipped_count)
```

This discourages implausible skips without blocking real large skips.

### 16.2 Persistence

Initial normal behavior:

```text
new slide wins >= 2 coarse samples
```

or approximately `1.5–2 seconds`.

The DP solver may encode this as a duration preference rather than a rigid rule.

## 17. Transition Detection

Initial defaults:

```text
new_score >= ~0.65
new_score - current_score >= ~0.12
```

and the new slide must remain stable.

These values are tunable.

## 18. Boundary Refinement

For a rough `Slide 8 → Slide 9` transition, compare only Slide 8 and Slide 9.

Initial window:

```text
rough boundary ± 2 seconds
```

Two stages:

1. narrow at around `6 FPS`,
2. scan at native FPS.

For crossfades:

> Boundary = first frame where the new slide becomes visually dominant and remains dominant for subsequent frames.

Initial stability:

```text
>= 3 subsequent frames
```

## 19. Homography-Normalized SSIM

SSIM is not the raw full-frame matcher.

Use it only after SIFT/RANSAC has established geometry:

```text
SIFT
→ homography
→ warp ROI into slide coordinates
→ SSIM / normalized pixel similarity
```

Primarily for aggressive retry or uncertain boundaries.

## 20. Normal Pass Configuration

Initial profile:

```text
coarse sample              ~1 FPS
SIFT nfeatures             ~1500
candidate lookahead        4
forced real match interval 3–5 sec
normal min area ratio      ~0.08
```

All are config-driven.

## 21. Aggressive Retry

Retry must increase evidence rather than repeat the same pass.

Suggested retry:

```text
coarse sample              4 FPS
SIFT nfeatures             ~3500
candidate search           all remaining slides
Lowe ratio                 adaptive ~0.65–0.82
min projected area ratio   ~0.03
transition windows         wider
homography-normalized SSIM enabled
```

If retry still fails, return a failed job.

## 22. Confidence Model

Three levels:

### 22.1 Match confidence

Per frame/slide candidate.

### 22.2 Transition confidence

Based on:

```text
evidence before boundary
evidence after boundary
new-vs-old dominance margin
temporal stability
refinement consistency
```

### 22.3 Overall confidence

Include:

- median transition confidence,
- minimum transition confidence,
- fraction of matched coarse frames,
- sequence consistency,
- duration coverage,
- anomaly penalties.

Do not use simple mean only.

## 23. Retry Triggers

Initial triggers include any of:

```text
overall_confidence < ~0.80
minimum_transition_confidence < ~0.55
unmatched_video_ratio > ~0.15
ambiguous sequence path
critical validation anomaly
```

Thresholds are tunable.

## 24. Timeline Validation

Successful output must satisfy:

```text
first segment begins at 0 unless explicit pre-roll is modeled
timestamps strictly increase
no negative durations
no unintended overlap
no unintended gaps between slide segments
slide index increases monotonically
all timestamps <= source audio duration
```

Additional anomaly signals:

- extremely short slide,
- implausibly dominant single slide,
- very high skipped-slide ratio,
- large unmatched regions,
- weak refined boundary.

Detection samples the video stream, but renderer validation uses `probe_media(...).audio_duration_ms`, matching `validate_timeline()` and `build_video()`. Before publishing, the detector must reject a source without both a decodable video stream and an audio duration. If video and audio durations differ beyond the configured tolerance, fail with diagnostics instead of silently clipping timestamps.

## 25. Non-Slide Regions

Model a special `NON_SLIDE` state for intro/pre-roll/outro/blank/logo regions.

For v1, non-slide regions are expected only:

- before first detected slide,
- after last detected slide.

Normal v1 does not model slide → non-slide → slide in the middle.

`NON_SLIDE` is an internal detector state and is not written into the renderer timeline. The current renderer requires the first slide to start at `0`, contiguous slide rows, and all rows to fit within audio duration. Therefore the publication adapter extends the first accepted slide to `0` and the last accepted slide to the source audio duration. If the leading or trailing non-slide interval exceeds configured confidence limits, publication fails rather than stretching a slide over a large unmatched region.

## 26. Missing Slides

If detected sequence is:

```text
1 → 2 → 4 → 5
```

return:

```json
"skipped_slides": [3]
```

Do not create zero-duration placeholders and do not force video time onto unobserved slides.

## 27. Output Schemas

### 27.1 Renderer timeline

The file consumed by `parse_timeline()` remains a non-empty top-level JSON array. This is the durable output placed beside the PPTX/media pair:

```json
[
  {
    "slide_id": 1,
    "start_ms": 0,
    "end_ms": 38500,
    "duration_ms": 38500
  },
  {
    "slide_id": 2,
    "start_ms": 38500,
    "end_ms": 66750,
    "duration_ms": 28250
  },
  {
    "slide_id": 4,
    "start_ms": 66750,
    "end_ms": 104417,
    "duration_ms": 37667
  }
]
```

The generated path is:

```text
<leaf>/timeline_slide_<pptx-stem>.json
```

The file is published only after `parse_timeline()` and `validate_timeline()` pass with the actual PPTX slide count, source audio duration, and configured output FPS. Write to a unique temporary sibling file, flush and close it, then publish with a no-clobber same-filesystem operation such as `os.link(temp, destination)`. A `FileExistsError` means another job won; discard the temporary file and re-run matching-timeline discovery. After publication, re-scan once more: if a different user timeline appeared concurrently, remove only the just-generated file and use the user file; if ambiguity remains, fail without modifying user files. Do not implement publication as a check followed by `Path.replace()`, because that can overwrite a file created between those operations.

TXT and VTT are optional derived formats and are not required by Batch Folder v1.

### 27.2 Diagnostic report

Detailed detector metadata uses a separate JSON object and is never passed to `parse_timeline()`:

```json
{
  "status": "success",
  "detector_version": "slide-cv-v1",
  "config_version": "2026-10-07",
  "video_duration_ms": 738166,
  "slide_count": 20,
  "retry_used": false,
  "overall_confidence": 0.93,
  "segments": [
    {
      "slide_index": 1,
      "start_ms": 0,
      "end_ms": 38500,
      "confidence": 0.97
    },
    {
      "slide_index": 2,
      "start_ms": 38500,
      "end_ms": 66750,
      "confidence": 0.95
    },
    {
      "slide_index": 4,
      "start_ms": 66750,
      "end_ms": 104417,
      "confidence": 0.91
    }
  ],
  "skipped_slides": [3],
  "transitions": [
    {
      "from_slide": 1,
      "to_slide": 2,
      "timestamp_ms": 38500,
      "confidence": 0.96
    }
  ]
}
```

Store it at:

```text
<leaf>/output/.timeline-diagnostics/<pptx-stem>.alignment.json
```

The diagnostic file may be rewritten on retry. It contains the detector/config versions, confidence values, skipped slides, transition evidence, and failure details. The renderer timeline remains the source of truth for rendering.

## 28. Failure Contract

After failed aggressive retry:

```json
{
  "status": "failed",
  "error_code": "SLIDE_ALIGNMENT_LOW_CONFIDENCE",
  "retry_used": true,
  "best_overall_confidence": 0.61,
  "uncertain_regions": [
    {
      "start_ms": 320000,
      "end_ms": 356000,
      "candidate_slides": [8, 9, 10]
    }
  ]
}
```

A best-effort timeline may be retained only in the diagnostic report. The intended `timeline_slide_<stem>.json` must remain absent, the slide job becomes `FAILED`, and rendering must not start.

Failure codes required by the integration:

```text
SLIDE_ALIGNMENT_LOW_CONFIDENCE
SLIDE_ALIGNMENT_VIDEO_REQUIRED
SLIDE_ALIGNMENT_DURATION_MISMATCH
SLIDE_ALIGNMENT_TIMELINE_CONFLICT
SLIDE_ALIGNMENT_INVALID_OUTPUT
```

Cancellation during detection follows the current worker cancellation contract: no renderer timeline is published, temporary files are removed where possible, and the job becomes `INTERRUPTED`.

## 29. Determinism and Versioning

For identical:

```text
video
PPTX
detector version
config version
```

the output should be reproducible.

Version:

```text
detector_version
config_version
feature_cache_version
```

Threshold changes must not silently alter behavior under the same config version.

## 30. Performance Budget

Reference workload:

```text
10–20 minute video
1080p
24–30 FPS
~20 slides
```

Targets:

```text
normal processing <= 0.35 × video duration
stretch target    <= 0.20 × video duration
retry             <= 0.80 × video duration
```

These assume one queued video per CPU worker.

## 31. Memory Budget

Do not load the full video into RAM.

Hold only:

- current frame,
- previous downscaled frame,
- current descriptors,
- precomputed slide descriptors,
- small observation buffers.

Target:

```text
peak RAM < 1 GB/job
preferred < 500 MB/job
```

for the expected 20–50 slide range.

## 32. Observability

Record stage timing:

```json
{
  "timing_ms": {
    "ppt_render": 1830,
    "feature_precompute": 920,
    "coarse_video_scan": 25100,
    "sequence_solve": 18,
    "boundary_refinement": 8420,
    "validation": 4,
    "total": 36302
  }
}
```

Record job summary:

```json
{
  "detector_version": "slide-cv-v1",
  "video_duration_ms": 738166,
  "slide_count": 20,
  "coarse_frames_sampled": 739,
  "sift_frames_processed": 284,
  "transitions_found": 18,
  "skipped_slides": [12],
  "retry_used": false,
  "overall_confidence": 0.94,
  "processing_ms": 88640
}
```

Record transition diagnostics:

```json
{
  "from_slide": 7,
  "to_slide": 8,
  "rough_timestamp_ms": 308000,
  "refined_timestamp_ms": 309833,
  "confidence": 0.93,
  "inliers": 61,
  "spatial_coverage": 0.69,
  "reprojection_error": 2.1
}
```

## 33. Debug Artifacts

Persist debug artifacts only for retry, low-confidence, failed, or explicit debug-mode jobs.

Example:

```text
debug/
  transition_07_08/
    before.jpg
    boundary.jpg
    after.jpg
    slide_07.png
    slide_08.png
    homography_overlay.jpg
  observations.json
  sequence_path.json
```

Overlay should show:

- projected slide polygon,
- matched inliers,
- candidate identity,
- score diagnostics.

### 33.1 Codebase change map

Implementation is limited to the existing workflow modules and their tests:

- `video_workflow/folder_batch.py`: distinguish video from audio-only media, choose the generated timeline target, and pass the generation flag.
- `video_workflow/job_models.py`: persist `generate_timeline_if_missing` with backward-compatible default `False`; add the generation stage.
- `video_workflow/queue_controller.py`: permit a missing timeline only for flagged slide jobs; still require source, PPTX, and output directory.
- `video_workflow/worker.py`: resolve/generate the timeline before creating `BuildRequest`, propagate cancel events, and emit the generation stage.
- `video_workflow/slide_alignment.py`: evolve the current alignment API behind a stable callable that returns a validated renderer timeline and writes a separate report.
- `video_workflow/ui.py`: do not label an intentionally missing generated timeline as a missing dependency; display the generation stage normally.
- `pyproject.toml` and Windows packaging/build checks: add and package `numpy` plus headless OpenCV/SIFT support.
- `README.md`: document automatic generation, supported video types, naming, output location, and failure behavior.

`video_workflow/pipeline.py`, `timeline.py`, and `compose.py` keep their current public input contract. The older `batch_workflow.py` flow may reuse the detector, but changing its 40-slide merged-deck conventions is outside this feature.

## 34. Benchmark Dataset

Create a manually verified golden dataset of approximately `30–50 videos`.

Cover:

- full-screen clean,
- resized/bordered,
- presenter split-screen,
- crop/zoom,
- occasional animation,
- skipped slides,
- fade transitions,
- unusual valid layouts.

Ground truth uses frame numbers:

```json
{
  "video": "T6_1.mp4",
  "slides": [
    {
      "slide": 1,
      "start_frame": 0,
      "end_frame": 933
    }
  ]
}
```

Timestamps are derived from the video time base/FPS.

## 35. Accuracy Metrics

### Sequence exact match

```text
target >= 98%
```

### Skipped-slide precision/recall

```text
target >= 0.98
```

### Boundary error

Track:

```text
median
P95
maximum
```

Initial targets:

```text
median <= 100 ms
P95 <= 250 ms
```

### Incorrect high-confidence rate

```text
incorrect high-confidence successful results
/
all high-confidence successful results
```

Initial target:

```text
< 0.5%
```

A small explicit failure rate is preferable to silent high-confidence errors.

## 36. Unit Tests

### Matcher

Test transformed slides with:

- resize,
- crop,
- mild rotation,
- perspective warp,
- compression,
- subtitle overlay,
- logo overlay.

Assert the intended slide outranks incorrect slides.

### Geometry

Test:

- valid quadrilateral,
- tiny quadrilateral,
- self-intersecting geometry,
- extreme skew,
- mostly out-of-frame projection.

### Sequence solver

Examples:

```text
1,1,2,2,3 → 1,2,3
1,1,8,1,2,2 → 1,2
1,1,4,4 → 1,4
5,5,4,6 → never produce 5→4
```

### Validator

Test:

- overlap,
- unintended gap,
- negative duration,
- time beyond source audio duration,
- duplicate/backward slide,
- malformed outro.

### Folder and queue integration

Test:

- a video/PPTX pair without a timeline becomes one flagged slide job,
- the generated target is `timeline_slide_<stem>.json`,
- an existing timeline is queued without generation and is not modified,
- multiple matching timelines remain an ambiguity issue,
- audio-only media without a timeline remains an issue,
- an old serialized job loads with generation disabled,
- queue validation accepts only an intentionally missing generated timeline,
- UI readiness does not report that intended path as a missing dependency,
- cache paths under `.workflow_data` do not affect leaf-folder discovery.

## 37. Integration Tests

Use short real/synthetic `PPTX + MP4` fixtures around 30–60 seconds.

Assert:

- correct sequence,
- correct skipped slides,
- boundary within tolerance,
- expected confidence,
- expected retry behavior.

Also assert the complete Batch Folder lifecycle:

- missing timeline → generate → validate → atomically publish → render,
- low confidence leaves no renderer timeline and never calls `build_video`,
- cancellation leaves no renderer timeline and marks the job interrupted,
- a render retry reuses the already generated valid timeline,
- repeated scans/jobs targeting the same path do not overwrite it,
- a user timeline appearing after enqueue wins over generated output,
- mixed `_1`/`_2` inputs generate local `1..N` slide IDs independently,
- a generated JSON array round-trips through `parse_timeline()` and `validate_timeline()`,
- diagnostic JSON is not mistaken for a renderer timeline.

Full benchmark runs nightly, before release, or whenever detector config changes.

## 38. Regression Process

Any change to:

- SIFT config,
- sample rate,
- score weights,
- thresholds,
- sequence penalties,
- geometry constraints,

must compare old vs new on:

```text
sequence accuracy
skip precision/recall
median boundary error
P95 boundary error
incorrect high-confidence rate
runtime
```

## 39. Production Acceptance Criteria

### Functional

- monotonic order enforced,
- skipped slides supported,
- full-screen supported,
- resized/cropped supported,
- presenter split-screen supported,
- occasional animation tolerated,
- intro/outro non-slide supported,
- low-confidence retry implemented,
- Batch Folder generates only missing timelines for video sources,
- existing timelines are never auto-overwritten,
- generated timelines are directly accepted by the existing renderer.

### Accuracy

```text
exact sequence >= 98%
skip precision >= 98%
skip recall >= 98%
median boundary error <= 100 ms
P95 boundary error <= 250 ms
incorrect high-confidence rate < 0.5%
```

### Performance

```text
normal median runtime <= 0.35× video duration
peak RAM < 1 GB
GPU not required
```

### Reliability

- no silent success after critical validation failure,
- aggressive retry on low confidence,
- diagnostics retained,
- same input/config yields reproducible output,
- interrupted/failed detection cannot leave a partial timeline,
- retries and repeated scans safely reuse an already published valid timeline,
- source-tree leaf discovery is unaffected by detector cache files.

## 40. Explicit v1 Non-Goals

v1 does not solve:

- backward slide navigation,
- arbitrary/random navigation,
- independently tracking two simultaneous slides,
- real-world camera footage of a projector,
- very heavy slide occlusion,
- extensive handwritten annotations covering most of the slide,
- VLM fallback,
- CLIP/image embeddings,
- speech-to-slide semantic alignment,
- ASR/subtitle generation.

## 41. Recommended v1 Technology

```text
Python
opencv-python-headless
SIFT
FLANN
RANSAC homography
NumPy
FFmpeg/OpenCV video decode
LibreOffice or existing PPT render path
```

No model weights are required.

Add explicit compatible lower/upper bounds for `opencv-python-headless` and `numpy` after validation on the supported Python 3.12 and Windows/PyInstaller build. The packaged executable acceptance check must exercise `cv2.SIFT_create()` rather than only importing `cv2`.

## 42. Final Design Summary

Selected design:

```text
CPU-only
classical CV
PPT render + cached SIFT features
cheap frame stability detector
SIFT + FLANN candidate matching
RANSAC homography
geometry + spatial validation
monotonic DP sequence solver
skip-slide support
coarse-to-fine scan
frame-level boundary refinement
aggressive retry with denser scan + SSIM confirmation
confidence model
strict timeline validation
renderer-compatible JSON-array timeline
separate diagnostic JSON object
single SLIDE job generation + render lifecycle
atomic no-clobber publication
application-data feature cache
diagnostic artifacts on retry/failure
benchmark-driven threshold tuning
```

The design deliberately exploits the known sequential-slide constraint to improve both speed and reliability, while avoiding VLM/GPU dependencies.
