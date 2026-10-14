# Batch TTS Slide Video Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every source-video desktop workflow with one recursive Batch Folder workflow that turns same-stem PPTX/TXT pairs into verified narrated slide videos and retained per-slide artifacts through a fixed VoiceStudio profile.

**Architecture:** Keep batch discovery, script parsing, VoiceStudio process supervision, HTTP transport, resumable narration generation, and video orchestration in separate modules with explicit dataclass contracts. Reuse the existing PowerPoint exporter, timeline validator, media probe, FFmpeg lecture renderer, logo overlay, and outro join primitives; route them through a new narration-only pipeline, persistent serial queue, and batch-only desktop UI.

**Tech Stack:** Python 3.12+, standard-library HTTP/process/filesystem APIs, Tkinter, python-pptx, Pillow, FFmpeg/ffprobe, LibreOffice/Poppler on Linux, PowerPoint COM on Windows, pytest 9+.

**Spec:** `docs/superpowers/specs/2026-10-14-batch-tts-slide-video-design.md`

## Global Constraints

- The desktop application exposes only recursive Batch Folder processing; no original video, manual audio, manual timeline, visual alignment, STT, OCR, forced alignment, or arbitrary two-video merge workflow remains reachable.
- Each lesson is one exact case-folded same-stem `.pptx` + `.txt` pair; output is `<leaf>/output/<stem>/` and multiple pairs per folder are supported.
- Global logo and outro are both required and captured into every queued job.
- VoiceStudio uses fixed profile ID `599fc048` (`Teacher_male`), engine `omnivoice`, language `Vietnamese`, `32` steps, maximum chunk length `150`, post-processing enabled, and `50` ms crossfade.
- VoiceStudio defaults to `http://127.0.0.1:3900`, is started with CUDA when unavailable, and receives one memory flush plus one same-quality retry after CUDA OOM; it never silently falls back to CPU, eight steps, another voice, or shortened text.
- Only one queue worker may synthesize or render at a time. Cancellation is observed after the current synchronous generation request and preserves validated WAV cache entries.
- Published video is 1280x720 H.264 with AAC stereo at configured FPS (default `24`), with logo over the lecture only and the global outro appended exactly once.
- All final artifacts are staged on the destination filesystem and replace an existing artifact directory only after validation; failure, cancellation, or changed captured inputs preserve the last valid published output.
- The package remains Python 3.12+ and adds no runtime HTTP dependency; use the standard library for VoiceStudio requests.
- Linux retains LibreOffice/Poppler export and Windows retains PowerPoint COM export.

## Review Focus

- A huge or malicious non-audio VoiceStudio response must be bounded in logs, rejected before publication, and must not replace a previously validated WAV; Task 3 pins response limits and Task 5 pins atomic audio publication.
- Case-insensitive filename collisions, symlinked directories, hidden/application folders, and nested `output` trees must not duplicate jobs or recurse into generated content; Task 2 tests all four conditions.
- Inputs or global assets modified while a job is running must be detected immediately before publication so an older queued job cannot overwrite a newer build; Tasks 6 and 7 test signature revalidation.
- Startup failure, missing profile, and pre-existing versus application-owned VoiceStudio processes must produce clear errors and must never terminate an external process; Task 4 tests ownership and log-path behavior.
- Disk-full/rename failure or a partial/corrupt manifest during final promotion must retain the previous valid output and permit recovery from validated cache entries; Tasks 5 and 6 test cache recovery and publication rollback.

---

### Task 1: Parse and validate slide narration scripts

**Files:**
- Create: `video_workflow/narration_script.py`
- Create: `tests/test_narration_script.py`

**Interfaces:**
- Consumes: UTF-8 TXT content and the slide count returned by `slide_export.count_pptx_slides(path)`.
- Produces: `SlideScriptSegment(slide_id: int, text: str, text_sha256: str)`, `ScriptError`, `parse_slide_script_text(text: str) -> tuple[SlideScriptSegment, ...]`, `load_slide_script(path: Path) -> tuple[SlideScriptSegment, ...]`, and `validate_script_slide_count(segments: Sequence[SlideScriptSegment], slide_count: int) -> None`.

- [ ] **Step 1: Write parser tests that establish the marker grammar**

```python
def test_parses_inline_markers_and_implicit_slide_one():
    segments = parse_slide_script_text("Mở đầu. Slide 2: Hai. Slide 3: Ba.")
    assert [(s.slide_id, s.text) for s in segments] == [(1, "Mở đầu."), (2, "Hai."), (3, "Ba.")]

def test_accepts_explicit_slide_one_and_multiline_text():
    segments = parse_slide_script_text("Slide 1:\nDòng một.\nSlide 2: Dòng hai.")
    assert [s.text for s in segments] == ["Dòng một.", "Dòng hai."]
```

- [ ] **Step 2: Write validation and normalization tests**

```python
def test_preserves_math_semicolons_and_normalizes_unicode_nfc():
    segments = parse_slide_script_text("Điểm A (0; 6; 3). Slide 2: Cafe\u0301.")
    assert segments[0].text == "Điểm A (0; 6; 3)."
    assert segments[1].text == "Café."

@pytest.mark.parametrize("source", ["", "Slide 1: ", "Slide 1: A Slide 3: C", "Slide 2: B Slide 2: C"])
def test_rejects_empty_nonconsecutive_or_duplicate_slides(source): ...

def test_requires_script_count_to_equal_pptx_count():
    with pytest.raises(ScriptError, match="3 slides.*2 narration"):
        validate_script_slide_count(parse_slide_script_text("A. Slide 2: B."), 3)
```

- [ ] **Step 3: Run the new tests and confirm the module is missing**

Run: `pytest tests/test_narration_script.py -q`

Expected: FAIL during import because `video_workflow.narration_script` does not exist.

- [ ] **Step 4: Implement the parser and validator**

Use a Unicode-aware, case-insensitive `Slide N:` marker regex; trim outer whitespace while preserving punctuation; NFC-normalize before hashing; treat unmarked prefix as slide 1; reject content before an explicit `Slide 1:` marker, gaps, duplicates, non-positive IDs, and empty segment text. Read TXT strictly as UTF-8 and wrap decoding/read errors in `ScriptError` with the path.

- [ ] **Step 5: Run focused tests**

Run: `pytest tests/test_narration_script.py -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add video_workflow/narration_script.py tests/test_narration_script.py
git commit -m "feat: parse slide narration scripts"
```

### Task 2: Discover exact same-stem PPTX/TXT lessons recursively

**Files:**
- Replace: `video_workflow/folder_batch.py`
- Replace: `tests/test_folder_batch.py`

**Interfaces:**
- Consumes: a root `Path`; controller integration is deferred to Task 7.
- Produces: `FolderJob(pptx: Path, script: Path, artifact_directory: Path, output_name: str)`, `FolderIssue(folder: Path, message: str)`, `FolderScanResult(jobs: tuple[FolderJob, ...], issues: tuple[FolderIssue, ...])`, and `discover_folder_jobs(root: Path) -> FolderScanResult`.

- [ ] **Step 1: Replace old `_1`/`_2` discovery tests with same-stem contract tests**

```python
def test_discovers_multiple_pairs_in_any_recursive_directory(tmp_path):
    # root/a/one.pptx + one.txt and root/a/two.PPTX + TWO.TXT
    result = discover_folder_jobs(tmp_path)
    assert [(j.pptx.stem.casefold(), j.script.stem.casefold()) for j in result.jobs] == [("one", "one"), ("two", "two")]
    assert result.jobs[0].artifact_directory == tmp_path / "a" / "output" / "one"

def test_reports_each_orphan_and_ambiguous_casefolded_stem(tmp_path): ...
```

- [ ] **Step 2: Add traversal-hardening tests from Review Focus**

```python
def test_ignores_output_hidden_and_application_temporary_directories(tmp_path): ...
def test_does_not_follow_directory_symlinks_or_discover_same_file_twice(tmp_path): ...
def test_reports_unreadable_directory_and_continues_other_folders(tmp_path, monkeypatch): ...
```

Assert deterministic path ordering, no job for a collision such as `LESSON.pptx` plus `lesson.pptx`, and continued discovery outside the failing folder.

- [ ] **Step 3: Run discovery tests and observe failures against the old media/timeline scanner**

Run: `pytest tests/test_folder_batch.py -q`

Expected: FAIL because current `FolderJob` requires media/timeline/part and scans only leaf directories.

- [ ] **Step 4: Implement deterministic, non-following traversal and pair grouping**

Scan every visited directory with `os.walk(..., followlinks=False)`, prune case-insensitive `output`, dot-prefixed directories, and application temp names beginning `.video-workflow-`; group regular files by case-folded stem and suffix; accept exactly one PPTX plus one TXT; return issues for every orphan, collision, unreadable directory, or non-directory root.

- [ ] **Step 5: Run focused tests**

Run: `pytest tests/test_folder_batch.py -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add video_workflow/folder_batch.py tests/test_folder_batch.py
git commit -m "feat: discover same-stem narration lessons"
```

### Task 3: Add a bounded VoiceStudio HTTP client

**Files:**
- Create: `video_workflow/voicestudio_client.py`
- Create: `tests/test_voicestudio_client.py`

**Interfaces:**
- Consumes: VoiceStudio HTTP endpoints `/profiles`, `/generate`, and `/system/flush-memory?unload_model=false`.
- Produces: `VoiceStudioConfig(api_url: str = "http://127.0.0.1:3900", profile_id: str = "599fc048", engine: str = "omnivoice", language: str = "Vietnamese", steps: int = 32, seed: int = 42, max_chunk_chars: int = 150, postprocess: bool = True, crossfade_ms: int = 50, config_version: str = "tts-v1")`; `VoiceProfile`; `GenerationMetadata`; `VoiceStudioError`; `VoiceStudioOutOfMemory`; and `VoiceStudioClient.list_profiles()`, `.require_profile(profile_id)`, `.generate(text, destination, config)`, `.flush_memory()`.

- [ ] **Step 1: Write tests against a local in-process HTTP stub**

```python
def test_generate_sends_literal_semicolons_and_fixed_quality(http_stub, tmp_path):
    metadata = VoiceStudioClient(http_stub.url).generate("A (0; 6; 3).", tmp_path / "x.partial", VoiceStudioConfig(api_url=http_stub.url))
    assert http_stub.form["text"] == "A (0; 6; 3)."
    assert http_stub.form["profile_id"] == "599fc048"
    assert http_stub.form["steps"] == "32"
    assert (tmp_path / "x.partial").read_bytes().startswith(b"RIFF")

def test_require_profile_matches_identifier_not_display_name(http_stub): ...
def test_flush_memory_uses_unload_model_false(http_stub): ...
```

- [ ] **Step 2: Add failure-boundary tests**

```python
def test_oom_response_raises_typed_error_with_bounded_detail(http_stub): ...
def test_huge_html_error_is_truncated_and_never_written_to_destination(http_stub, tmp_path): ...
def test_rejects_200_non_wav_content_type_or_invalid_riff(http_stub, tmp_path): ...
def test_interrupted_download_leaves_only_partial_for_caller_cleanup(http_stub, tmp_path): ...
```

Assert diagnostic text is at most 8192 characters and profile/error JSON is capped at 1 MiB before parsing.

- [ ] **Step 3: Run the client tests and confirm the module is missing**

Run: `pytest tests/test_voicestudio_client.py -q`

Expected: FAIL during import.

- [ ] **Step 4: Implement the standard-library client**

Build multipart/form-data with `email.message`/byte streams or an explicit safe boundary, never shell commands; use connect/read timeout values supplied to `VoiceStudioClient(base_url: str, *, timeout_seconds: float = 30.0, max_error_bytes: int = 8192)`; stream successful audio to the caller-provided `.partial`; capture `X-Audio-ID`, generation time, and routing headers when present; classify case-insensitive CUDA/out-of-memory error bodies as `VoiceStudioOutOfMemory`.

- [ ] **Step 5: Run focused tests**

Run: `pytest tests/test_voicestudio_client.py -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add video_workflow/voicestudio_client.py tests/test_voicestudio_client.py
git commit -m "feat: add VoiceStudio HTTP client"
```

### Task 4: Supervise VoiceStudio startup and process ownership

**Files:**
- Create: `video_workflow/voicestudio_supervisor.py`
- Create: `tests/test_voicestudio_supervisor.py`

**Interfaces:**
- Consumes: `VoiceStudioClient`, `VoiceStudioConfig`, an optional backend override path, application log directory, platform-specific install candidates.
- Produces: `VoiceStudioSupervisor(config: VoiceStudioConfig, *, backend_override: Path | None, log_directory: Path, startup_timeout_seconds: float = 120.0)`, `.ensure_ready() -> VoiceStudioClient`, `.close() -> None`, and `VoiceStudioStartupError`.

- [ ] **Step 1: Write readiness, profile, and ownership tests with injected client/process factories**

```python
def test_returns_existing_ready_api_without_starting_or_owning_process(): ...
def test_starts_cuda_backend_waits_then_validates_exact_profile(): ...
def test_missing_profile_fails_without_searching_by_display_name(): ...
def test_close_terminates_only_the_process_started_by_this_supervisor(): ...
```

Assert the launched environment contains `OMNIVOICE_DEVICE=cuda`, `TORCH_COMPILE_DISABLE=1`, and `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` without discarding the parent environment.

- [ ] **Step 2: Write startup failure tests from Review Focus**

```python
def test_early_process_exit_reports_backend_log_path(): ...
def test_startup_timeout_terminates_owned_process_and_reports_timeout(): ...
def test_existing_unhealthy_external_process_is_never_terminated(): ...
def test_missing_install_lists_checked_locations_and_override(): ...
```

- [ ] **Step 3: Run tests and confirm the supervisor is missing**

Run: `pytest tests/test_voicestudio_supervisor.py -q`

Expected: FAIL during import.

- [ ] **Step 4: Implement discovery, launch, polling, and cleanup**

Prefer an explicit backend override; on Linux check the installed runtime under `~/.config/VoiceStudio/runtime/project` and `/opt/VoiceStudio/voicestudio-electron`; add equivalent Windows executable/runtime candidates without changing the public interface. Launch the Python backend when available, redirect stdout/stderr to an owned log file, poll `/profiles` until ready or timeout, and track ownership separately from API readiness.

- [ ] **Step 5: Run focused tests**

Run: `pytest tests/test_voicestudio_supervisor.py -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add video_workflow/voicestudio_supervisor.py tests/test_voicestudio_supervisor.py
git commit -m "feat: supervise local VoiceStudio service"
```

### Task 5: Build resumable per-slide narration, manifest, and timeline

**Files:**
- Create: `video_workflow/narration.py`
- Create: `tests/test_narration.py`
- Modify: `video_workflow/probe.py`
- Modify: `tests/test_probe_powerpoint.py`

**Interfaces:**
- Consumes: `Sequence[SlideScriptSegment]`, a ready `VoiceStudioClient`, `VoiceStudioConfig`, artifact staging directory, persistent cache directory, progress callback, and cancellation event.
- Produces: `AudioArtifact`, `NarrationResult(slides, timeline_path, combined_audio_path, manifest_path, total_duration_ms)`, `NarrationError`, `NarrationCancelled`, and `build_narration(segments, client, artifact_directory, cache_directory, config, *, on_progress=None, cancel_event=None, log_path=None) -> NarrationResult`.

- [ ] **Step 1: Extend media probing so audio cache validation has exact metadata**

Add `sample_rate: int | None` and `channels: int | None` to `MediaInfo`; update ffprobe fields and tests so a WAV probe reports positive `duration_ms`, `has_audio`, sample rate, and channel count without requiring a video stream.

- [ ] **Step 2: Write successful narration and timeline tests with a fake client**

```python
def test_builds_one_validated_wav_per_slide_and_contiguous_renderer_timeline(tmp_path): ...
def test_combined_wav_orders_slides_without_inserted_gaps(tmp_path): ...
def test_manifest_records_text_profile_config_media_hash_and_generation_metadata(tmp_path): ...
```

Assert `timeline.json` uses the existing array schema with integer `slide_id/start_ms/end_ms/duration_ms`, starts at zero, and each start equals the previous end. Assert output names are `audio/slide_001.wav`, etc.

- [ ] **Step 3: Write cache, cancellation, OOM, and recovery tests**

```python
def test_unchanged_manifest_reuses_valid_wav_without_generation(tmp_path): ...
def test_editing_one_text_regenerates_only_that_slide(tmp_path): ...
def test_profile_or_config_version_change_invalidates_all_slides(tmp_path): ...
def test_corrupt_wav_or_partial_manifest_is_regenerated(tmp_path): ...
def test_oom_flushes_and_retries_once_at_32_steps(tmp_path): ...
def test_second_oom_fails_without_quality_or_cpu_fallback(tmp_path): ...
def test_cancel_after_current_request_preserves_validated_cache_and_stops_next_slide(tmp_path): ...
def test_rename_or_disk_failure_never_replaces_previous_validated_wav(tmp_path, monkeypatch): ...
```

- [ ] **Step 4: Run the narration tests and observe missing implementation**

Run: `pytest tests/test_narration.py tests/test_probe_powerpoint.py -q`

Expected: FAIL because narration APIs and audio metadata fields do not exist.

- [ ] **Step 5: Implement atomic generation, cache identity, OOM retry, concatenation, and manifest writes**

Generate to `.partial`, use `probe_media` plus a RIFF/WAVE header check, hash the complete validated WAV, then `os.replace` into cache. Materialize cached files into the staged `audio/` directory with copy semantics safe across filesystems. Write JSON through an fsync + replace helper. Concatenate via FFmpeg concat demuxer into PCM WAV and verify the duration is within 20 ms per slide of the summed measured duration. Check cancellation only before and after each synchronous request.

- [ ] **Step 6: Run focused tests**

Run: `pytest tests/test_narration.py tests/test_probe_powerpoint.py -q`

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add video_workflow/narration.py video_workflow/probe.py tests/test_narration.py tests/test_probe_powerpoint.py
git commit -m "feat: build resumable slide narration"
```

### Task 6: Replace the pipeline with narrated artifact-tree publication

**Files:**
- Replace: `video_workflow/pipeline.py`
- Replace: `tests/test_pipeline.py`
- Modify: `video_workflow/compose.py`
- Modify: `tests/test_compose.py`

**Interfaces:**
- Consumes: parser/count validator from Task 1, narration builder from Task 5, existing `export_slides`, `parse_timeline`/`validate_timeline`, media probe, and composition primitives.
- Produces: `NarratedBuildRequest(pptx, script, logo, outro, artifact_directory, profile_config, fps=24, logo_width_ratio=0.12, margin_px=0, pipeline_version="narrated-v1")`; `NarratedBuildReport`; `build_input_signature(request) -> dict[str, object]`; `build_narrated_video(request, *, client, cache_root, on_stage=None, on_progress=None, cancel_event=None, log_path=None) -> NarratedBuildReport`; `WorkflowError`; and `WorkflowCancelled`.

- [ ] **Step 1: Rename the renderer's misleading audio parameter without changing behavior**

Change `render_lecture(..., source_video: Path, ...)` to `render_lecture(..., narration_audio: Path, ...)` and update tests to prove a WAV audio input still yields 1280x720 H.264/AAC output with the requested FPS and logo.

- [ ] **Step 2: Write orchestration tests for the complete artifact contract**

```python
def test_build_publishes_video_report_timeline_audio_manifest_wavs_and_images(tmp_path): ...
def test_logo_is_on_lecture_and_outro_is_appended_exactly_once(tmp_path): ...
def test_report_contains_absolute_input_hashes_profile_settings_slide_times_and_media_checks(tmp_path): ...
```

Use fakes for synthesis/export/composition in orchestration tests and retain the focused FFmpeg tests in `test_compose.py` for codec/dimensions/duration.

- [ ] **Step 3: Add skip, stale rebuild, publication, and input-race tests**

```python
def test_matching_signature_and_valid_artifacts_skip_all_build_steps(tmp_path): ...
def test_stale_or_missing_artifact_rebuilds_but_may_reuse_audio_cache(tmp_path): ...
def test_failed_or_cancelled_staging_preserves_previous_published_directory(tmp_path): ...
def test_promotion_failure_rolls_back_previous_directory(tmp_path, monkeypatch): ...
def test_changed_pptx_txt_logo_or_outro_before_publish_aborts_as_stale(tmp_path): ...
```

Assert a changed input raises `WorkflowError` containing `inputs changed while job was running` and leaves the previous published directory byte-for-byte intact.

- [ ] **Step 4: Run focused pipeline tests and observe old BuildRequest failures**

Run: `pytest tests/test_pipeline.py tests/test_compose.py -q`

Expected: FAIL because the source-media `BuildRequest` and output-file publication model are still present.

- [ ] **Step 5: Implement request validation, signature, orchestration, report, and rollback-safe promotion**

Require readable PPTX/TXT/logo/outro files; hash all four before work; parse and compare narration count to PPTX count; create staging beside `artifact_directory`; preserve exported PNGs; render lecture from `combined.wav`; normalize/join one outro; verify H.264/AAC/1280x720/FPS/duration; hash inputs again before promotion. Promote by renaming an existing artifact directory to a unique backup, renaming staging into place, then deleting backup; if the second rename fails, restore backup and surface the failure. Do not place reusable cache inside the replaceable published directory.

- [ ] **Step 6: Run focused tests**

Run: `pytest tests/test_pipeline.py tests/test_compose.py -q`

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add video_workflow/pipeline.py video_workflow/compose.py tests/test_pipeline.py tests/test_compose.py
git commit -m "feat: build narrated slide artifact trees"
```

### Task 7: Migrate persistence and wire the serial narration queue

**Files:**
- Replace: `video_workflow/job_models.py`
- Modify: `video_workflow/json_store.py`
- Replace: `video_workflow/queue_controller.py`
- Replace: `video_workflow/worker.py`
- Modify: `video_workflow/folder_batch.py`
- Replace: `tests/test_job_store.py`
- Replace: `tests/test_queue_controller.py`
- Replace: `tests/test_worker.py`
- Modify: `tests/test_folder_batch.py`

**Interfaces:**
- Consumes: `FolderJob`, `NarratedBuildRequest`, `VoiceStudioSupervisor`, pipeline callbacks and exceptions.
- Produces: schema-v2 `GlobalSettings`, narration-only `JobRecord`, `JobStage`, `QueueController.enqueue(pptx, script, artifact_directory, output_name)`, `QueueController.set_progress(job_id, current, total)`, `queue_folder_jobs(controller, root)`, and a single-worker `QueueWorker` that owns one shared supervisor lifecycle.

- [ ] **Step 1: Write schema-v2 model and migration tests**

```python
def test_v1_settings_migrate_assets_and_add_fixed_voicestudio_defaults(tmp_path): ...
def test_v1_jobs_are_archived_and_new_v2_queue_starts_empty(tmp_path): ...
def test_v2_job_round_trip_captures_pptx_script_logo_outro_profile_config_and_signature(tmp_path): ...
```

Assert the archive is named `jobs.legacy-v1-<UTC timestamp>.json`, contains the untouched v1 payload plus a migration-reason sidecar or envelope, and old slide/merge jobs are never deserialized as narration jobs.

- [ ] **Step 2: Write controller and batch enqueue tests**

```python
def test_enqueue_requires_saved_existing_logo_and_outro_and_captures_their_paths(tmp_path): ...
def test_batch_scan_enqueues_every_valid_same_stem_pair_with_its_artifact_directory(tmp_path): ...
def test_claim_next_is_strictly_serial_and_progress_is_persisted(tmp_path): ...
def test_retry_preserves_job_inputs_and_returns_to_waiting(tmp_path): ...
```

- [ ] **Step 3: Write worker lifecycle and stale-input tests**

```python
def test_worker_ensures_voicestudio_then_builds_jobs_serially_and_emits_all_stages(tmp_path): ...
def test_worker_close_stops_owned_supervisor_after_queue_thread_exits(tmp_path): ...
def test_cancel_waits_for_current_builder_then_marks_interrupted(tmp_path): ...
def test_older_queued_job_with_changed_captured_signature_fails_before_publication(tmp_path): ...
def test_one_failed_lesson_does_not_prevent_next_waiting_lesson(tmp_path): ...
```

- [ ] **Step 4: Run focused persistence and queue tests**

Run: `pytest tests/test_job_store.py tests/test_queue_controller.py tests/test_worker.py tests/test_folder_batch.py -q`

Expected: FAIL because schema v1 and slide/merge job kinds remain.

- [ ] **Step 5: Implement schema-v2 records and safe one-time migration**

`GlobalSettings` must include required-but-initially-unset logo/outro plus VoiceStudio API URL, profile ID, optional backend override, and inference config version. `JobRecord` must store immutable captured asset paths/config/signature and mutable status/stage/progress timestamps; remove `JobKind`, source media, timeline, secondary media, per-job outro toggles, and merge construction.

- [ ] **Step 6: Implement narration-only controller, batch enqueue, and worker**

Require both global assets before enqueue/claim; create but do not pre-delete artifact directories; retain fresh queue records on repeated scans; share one supervisor/client for the worker loop; map callbacks to `VALIDATING`, `STARTING_VOICESTUDIO`, `VALIDATING_PROFILE`, `SYNTHESIZING_AUDIO`, `ASSEMBLING_NARRATION`, `EXPORTING_SLIDES`, `RENDERING_LECTURE`, `PREPARING_OUTRO`, `JOINING`, `VERIFYING`, and `PUBLISHING`; close only the supervisor-owned process after the worker stops.

- [ ] **Step 7: Run focused tests**

Run: `pytest tests/test_job_store.py tests/test_queue_controller.py tests/test_worker.py tests/test_folder_batch.py -q`

Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add video_workflow/job_models.py video_workflow/json_store.py video_workflow/queue_controller.py video_workflow/worker.py video_workflow/folder_batch.py tests/test_job_store.py tests/test_queue_controller.py tests/test_worker.py tests/test_folder_batch.py
git commit -m "feat: migrate to narration batch queue"
```

### Task 8: Replace the desktop UI with the single Batch Folder workflow

**Files:**
- Replace: `video_workflow/ui.py`
- Replace: `tests/test_ui.py`
- Modify: `video_workflow/__main__.py`

**Interfaces:**
- Consumes: `QueueController`, `queue_folder_jobs`, `QueueWorker`, schema-v2 settings/jobs/events.
- Produces: `WorkflowApp` with only required Global Assets, Batch Folder, and Queue sections; `python -m video_workflow` starts this desktop application.

- [ ] **Step 1: Replace form tests with batch-only behavior tests**

```python
def test_save_assets_rejects_blank_or_missing_logo_and_outro(tmp_path): ...
def test_batch_submit_requires_assets_then_queues_same_stem_pairs_and_wakes_worker(tmp_path): ...
def test_fixed_profile_is_displayed_read_only_as_teacher_male_and_599fc048(): ...
def test_queue_rows_show_lesson_stage_slide_progress_status_and_artifact_folder(): ...
def test_retry_cancel_and_open_output_actions_target_selected_job(tmp_path): ...
```

- [ ] **Step 2: Add structural regression tests proving obsolete flows are gone**

```python
def test_ui_has_no_source_media_timeline_single_build_or_merge_controls(): ...
def test_main_module_launches_ui_main(monkeypatch): ...
```

Assert `WorkflowApp` has no `submit_job`, `submit_merge_job`, `source_var`, `timeline_var`, `first_video_var`, or `second_video_var`, and the rendered widget labels contain neither `Build slide video` nor `Merge 2 videos`.

- [ ] **Step 3: Run UI tests and observe failures against the notebook UI**

Run: `pytest tests/test_ui.py -q`

Expected: FAIL because old single-build and merge controls still exist and blank assets are allowed.

- [ ] **Step 4: Implement the batch-only window**

Remove `JobFormData`, `MergeFormData`, notebook tabs, manual source/timeline/output forms, and merge callbacks. Keep required logo/outro selectors and Save, one root-folder selector with Scan & Add All, concise same-stem guidance, fixed profile status, queue stage/progress/status, retry/cancel/open-folder actions, warnings, and orderly worker shutdown.

- [ ] **Step 5: Run focused tests**

Run: `pytest tests/test_ui.py -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add video_workflow/ui.py video_workflow/__main__.py tests/test_ui.py
git commit -m "feat: simplify desktop app to batch workflow"
```

### Task 9: Remove obsolete entry points, update installers/docs, and run the real smoke test

**Files:**
- Delete: `video_workflow/cli.py`
- Delete: `video_workflow/batch_cli.py`
- Delete: `video_workflow/batch_discovery.py`
- Delete: `video_workflow/batch_models.py`
- Delete: `video_workflow/batch_workflow.py`
- Delete: `video_workflow/merge_pipeline.py`
- Delete: `video_workflow/pptx_merge.py`
- Delete: `video_workflow/slide_alignment.py`
- Delete: `tests/test_batch_cli.py`
- Delete: `tests/test_batch_discovery.py`
- Delete: `tests/test_batch_workflow.py`
- Delete: `tests/test_merge_pipeline.py`
- Delete: `tests/test_pptx_merge.py`
- Delete: `tests/test_slide_alignment.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `setup.sh`
- Modify: `scripts/setup.ps1`
- Modify: `run-ui.bat`
- Modify: `tests/test_linux_scripts.py`
- Modify: `tests/test_windows_setup.py`
- Modify: `tests/test_windows_build.py`
- Modify: `tests/test_linux_integration.py`

**Interfaces:**
- Consumes: completed narration workflow from Tasks 1-8 and the installed VoiceStudio service/profile.
- Produces: one supported launcher `video-workflow-ui`, dependency/install checks that explain how to locate VoiceStudio, updated operator documentation, and a verified real end-to-end artifact tree.

- [ ] **Step 1: Write packaging and installer regression tests**

```python
def test_pyproject_exposes_only_desktop_workflow_entrypoint(): ...
def test_linux_setup_checks_ffmpeg_libreoffice_poppler_and_voicestudio_without_installing_voicestudio(): ...
def test_windows_setup_checks_ffmpeg_powerpoint_and_voicestudio_without_installing_voicestudio(): ...
def test_pyinstaller_bundle_imports_new_narration_and_voicestudio_modules(): ...
```

- [ ] **Step 2: Remove obsolete orchestration modules/tests and update package entry points**

Delete only old source-media, visual-alignment, `_1/_2` batch, PPTX merge, and arbitrary video-merge layers listed above. Keep `compose.py`, `timeline.py`, `slide_export.py`, `probe.py`, `libreoffice.py`, `powerpoint.py`, and `live_path_dialog.py` because the new pipeline still consumes them. In `pyproject.toml`, remove `build-video` and `build-video-batch`; retain `video-workflow-ui = "video_workflow.ui:main"`. Remove the `batch`/`--batch` branches from `run-ui.bat` so both launchers open the same desktop UI only.

- [ ] **Step 3: Update setup scripts and README**

Document the only input layout, required logo/outro, fixed profile, auto-start behavior, output tree, cache/resume rules, OOM retry, cancellation boundary, and platform prerequisites. Setup checks must report discovered VoiceStudio/API/backend locations and remediation, but must never download or install VoiceStudio automatically.

- [ ] **Step 4: Run all automated verification**

Run:

```bash
pytest -q
git diff --check
python -m compileall -q video_workflow
```

Expected: all tests PASS, no whitespace errors, and compilation exits 0.

- [ ] **Step 5: Run the Linux real-service smoke test on one matched lesson**

Create a disposable smoke root containing one real same-stem PPTX/TXT pair whose marker count equals the deck slide count, plus the approved logo/outro paths. Start the desktop app or call the same controller/worker interfaces; allow the supervisor to reuse or start VoiceStudio on GPU. Do not substitute fake audio or an eight-step request.

Verify with `ffprobe` and file inspection:

```text
<smoke-root>/output/<stem>/<stem>.mp4
<smoke-root>/output/<stem>/report.json
<smoke-root>/output/<stem>/timeline.json
<smoke-root>/output/<stem>/combined.wav
<smoke-root>/output/<stem>/audio-manifest.json
<smoke-root>/output/<stem>/audio/slide_001.wav ...
<smoke-root>/output/<stem>/slide-images/slide_001.png ...
```

Expected: timeline is contiguous from zero; WAV count and image count equal PPTX slide count; MP4 is 1280x720 H.264/AAC at 24 FPS; measured duration matches narration plus one outro within pipeline tolerance; `nvidia-smi`/VoiceStudio log shows CUDA inference with 32 steps; listen to the beginning, one middle slide, one boundary, and the ending to confirm intelligible `Teacher_male` narration without repeated phrases and one outro.

- [ ] **Step 6: Commit cleanup and verified documentation**

```bash
git add -A
git commit -m "chore: retire legacy video workflows"
```

