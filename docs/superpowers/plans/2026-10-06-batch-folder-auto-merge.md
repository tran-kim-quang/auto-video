# Batch Folder Auto-Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically merge each complete Batch Folder `_1`/`_2` render pair into a retained third `<lesson>.mp4` output.

**Architecture:** Persist render-job IDs as dependencies on a queued batch merge job. The FIFO scheduler skips dependency-blocked merges, claims them only after their own two renders complete, and leaves them waiting through part failure/retry. Batch discovery creates isolated render/render/merge groups, while the existing merge pipeline gains the same safe overwrite and report-suppression policy already used by batch slide renders.

**Tech Stack:** Python 3.12+, frozen dataclasses, JSON persistence, Tkinter, FFmpeg/ffprobe, pytest

**Spec:** `docs/superpowers/specs/2026-10-06-batch-folder-auto-merge-design.md`

## Global Constraints

- Pair parts only inside the same leaf folder when stems differ only by the final `_1`/`_2` suffix.
- Name outputs `output/<lesson>_1.mp4`, `output/<lesson>_2.mp4`, and `output/<lesson>.mp4`; retain all three.
- Queue each complete group in part 1, part 2, merge order; never run renders in parallel.
- A merge is satisfied only by its persisted render-job IDs, never by stale file existence or prior scan history.
- Keep a dependent merge waiting while either part is waiting, running, failed, or interrupted; a successful part retry makes it eligible automatically.
- Batch render and merge jobs use `overwrite_output=True` and `write_report=False`; failure/cancellation preserves prior MP4 files.
- Part 1 has no outro, part 2 has the configured outro, and merge adds no extra outro.
- Standalone slide and **Merge 2 videos** jobs remain non-overwriting and report-producing.
- A single valid part still renders, produces a missing-counterpart warning, and creates no merge job.
- Existing `jobs.json` files remain readable; missing dependency metadata defaults to no dependencies.
- Support the existing Linux and Windows setups without adding dependencies or a new UI toggle.

## Review Focus

- Similar lesson stems and multiple pairs in one leaf must never cross-pair; Task 2 pins exact grouping and order.
- A repeated scan with old MP4 files and old completed jobs must create fresh dependency IDs; Task 2 proves isolation.
- A dependency marked completed whose output was deleted must fail merge path validation, not use history as proof; Task 1 covers this.
- Restart with a running part must interrupt only that part and keep its merge waiting for retry; Task 1 covers recovery.
- An old report or merged MP4 appearing before publication must be removed/replaced only for batch merge after successful verification; Task 3 covers success, failure, and cancellation.

---

### Task 1: Persisted Job Dependencies and Scheduler Rules

**Files:**
- Modify: `video_workflow/job_models.py:84-218`
- Modify: `video_workflow/queue_controller.py:50-290`
- Test: `tests/test_job_store.py`
- Test: `tests/test_queue_controller.py`

**Interfaces:**
- Produces: `JobRecord.dependency_job_ids: tuple[str, ...]` with an empty default and JSON list serialization.
- Produces: `JobRecord.new_merge(..., write_report: bool = True, overwrite_output: bool = False, dependency_job_ids: tuple[str, ...] = ()) -> JobRecord`.
- Produces: `QueueController.enqueue_merge(..., write_report: bool = True, overwrite_output: bool = False, dependency_job_ids: tuple[str, ...] = ()) -> JobRecord`.
- Produces: `QueueController.dependency_state(job: JobRecord) -> tuple[bool, str | None]`, returning `(ready, fatal_error)`.

- [ ] **Step 1: Write failing persistence tests**

Add `test_merge_job_round_trips_dependencies_and_batch_flags` and `test_legacy_job_defaults_to_no_dependencies` in `tests/test_job_store.py`. Assert tuple round-trip, JSON list storage, batch flag round-trip, and `()` when `dependency_job_ids` is absent.

- [ ] **Step 2: Run the persistence tests and verify RED**

Run: `python -m pytest tests/test_job_store.py -q`

Expected: FAIL because `dependency_job_ids` and merge batch factory arguments do not exist.

- [ ] **Step 3: Implement dependency persistence**

Add the field and optional factory parameters in `video_workflow/job_models.py`; serialize as a list and load with `tuple(str(item) for item in data.get("dependency_job_ids", ()))`.

- [ ] **Step 4: Run persistence tests and verify GREEN**

Run: `python -m pytest tests/test_job_store.py -q`

Expected: all tests pass.

- [ ] **Step 5: Write failing scheduler tests**

Add focused tests in `tests/test_queue_controller.py` asserting:

- a dependent merge may be enqueued before its future input MP4s exist;
- it stays waiting while either dependency is non-completed and an unrelated ready job is claimed;
- after a failed/interrupted part is retried, produces its output, and completes, the merge is claimed;
- two fresh groups with the same output paths are isolated by their own IDs;
- a persisted waiting merge with a bogus dependency ID fails with `missing dependency` in its error;
- removing a referenced waiting part raises `QueueStateError` mentioning removal of the dependent first;
- after startup recovery, a running part becomes interrupted and its merge remains waiting;
- completed dependencies with a deleted part output cause merge path validation failure.

- [ ] **Step 6: Run scheduler tests and verify RED**

Run: `python -m pytest tests/test_queue_controller.py -q`

Expected: FAIL because enqueue validates future files immediately and the scheduler ignores dependencies.

- [ ] **Step 7: Implement scheduler semantics**

Implement `dependency_state()` with these exact results: no dependencies or all completed → `(True, None)`; unknown ID → `(False, "missing dependency: <id>")`; any known non-completed dependency → `(False, None)`. Allow missing merge inputs only during enqueue when dependencies are present, but always validate output directory/output policy and require every supplied dependency ID to reference an existing job. In `claim_next()`, fail fatal dependency errors loaded from persisted history, skip blocked jobs, and fully validate paths after dependencies complete. Reject `remove()` when another waiting job references the selected ID.

- [ ] **Step 8: Run model/controller tests and verify GREEN**

Run: `python -m pytest tests/test_job_store.py tests/test_queue_controller.py -q`

Expected: all tests pass.

- [ ] **Step 9: Commit Task 1**

```bash
git add video_workflow/job_models.py video_workflow/queue_controller.py tests/test_job_store.py tests/test_queue_controller.py
git commit -m "feat: add persisted queue dependencies"
```

### Task 2: Batch Pair Grouping and Dependent Merge Enqueue

**Files:**
- Modify: `video_workflow/folder_batch.py:16-205`
- Test: `tests/test_folder_batch.py`

**Interfaces:**
- Consumes: dependency-aware `QueueController.enqueue_merge()` from Task 1.
- Produces: `FolderJob.lesson_stem: str` derived by removing the final `_1`/`_2` suffix.
- Produces: private `_group_parts(jobs: Iterable[FolderJob]) -> list[tuple[Path, str, dict[int, FolderJob]]]`, returning leaf, display stem, and part map in deterministic order.
- Preserves: `discover_folder_jobs(root: Path) -> FolderScanResult` and `queue_folder_jobs(controller: QueueController, root: Path) -> FolderQueueResult`.

- [ ] **Step 1: Write failing pairing and enqueue tests**

Update/add tests in `tests/test_folder_batch.py` asserting:

- one complete pair queues exactly `[part1 slide, part2 slide, merged merge]` with output names `TOAN8_B1_T1_1.mp4`, `TOAN8_B1_T1_2.mp4`, `TOAN8_B1_T1.mp4`;
- the merge inputs equal the two part output paths and its dependency IDs equal the two new slide IDs in order;
- every queued job has `write_report is False` and `overwrite_output is True`, while only part 2 has `use_outro is True`;
- two similarly named pairs in one leaf remain separate and deterministic, including a base that itself ends in `_1`;
- a single valid part queues one slide, no merge, and an issue naming the missing `_1` or `_2` counterpart;
- two scans with existing outputs queue six jobs whose two merges reference only their own scan's render IDs;
- existing videos remain untouched and stale `*.report.json` files are deleted during scan.

- [ ] **Step 2: Run Batch Folder tests and verify RED**

Run: `python -m pytest tests/test_folder_batch.py -q`

Expected: FAIL because only slide jobs are currently queued and incomplete pairs are not reported.

- [ ] **Step 3: Implement exact lesson grouping**

Store `lesson_stem=deck.stem[:-2]` on each valid `FolderJob`. Implement `_group_parts()` with `(leaf path, lesson_stem.casefold())`, retain part 1's spelling for a complete pair's merged filename, and process groups deterministically by leaf/base. Have `discover_folder_jobs()` report one missing-counterpart issue for every group containing only one valid part; `queue_folder_jobs()` consumes the same grouping.

- [ ] **Step 4: Enqueue isolated render/render/merge groups**

For a complete group, enqueue part 1 and part 2 with existing batch settings, then enqueue merge using their output paths, IDs, `write_report=False`, and `overwrite_output=True`. Append all three records to `FolderQueueResult.queued`. For an incomplete group, enqueue only its valid slide job.

- [ ] **Step 5: Run Batch Folder tests and verify GREEN**

Run: `python -m pytest tests/test_folder_batch.py tests/test_queue_controller.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add video_workflow/folder_batch.py tests/test_folder_batch.py
git commit -m "feat: enqueue batch auto merge jobs"
```

### Task 3: Safe Batch Policy in the Merge Pipeline and Worker

**Files:**
- Modify: `video_workflow/merge_pipeline.py:14-145`
- Modify: `video_workflow/worker.py:120-152`
- Test: `tests/test_merge_pipeline.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: merge-job `write_report` and `overwrite_output` fields from Task 1.
- Produces: `MergeRequest.write_report: bool = True` and `MergeRequest.overwrite_output: bool = False`.
- Preserves: manual `MergeRequest(first_video, second_video, output)` behavior.

- [ ] **Step 1: Write failing merge-policy tests**

Add unit tests in `tests/test_merge_pipeline.py` for a batch request with an existing output and stale report. On verified success, assert the MP4 is replaced and the report removed. When normalize/join/verify fails or cancellation is set during final probe, assert the old MP4 remains. Retain the existing manual existing-output and late-output no-clobber coverage.

- [ ] **Step 2: Write the failing worker propagation test**

Extend `tests/test_worker.py` so a dependency-ready batch merge job reaches the injected merge builder with `write_report is False`, `overwrite_output is True`, first input `_1.mp4`, and second input `_2.mp4`.

- [ ] **Step 3: Run merge/worker tests and verify RED**

Run: `python -m pytest tests/test_merge_pipeline.py tests/test_worker.py -q`

Expected: FAIL because `MergeRequest` and the merge worker branch do not carry batch policy.

- [ ] **Step 4: Implement merge request policy**

Add the two defaulted fields, preserve them during request path normalization, permit existing output/report only under the batch policy, create a staged report only when enabled, remove a stale report only after final cancellation check, and publish through `_publish_video(..., overwrite=request.overwrite_output)`.

- [ ] **Step 5: Propagate job settings in the worker**

Pass `job.write_report` and `job.overwrite_output` when constructing `MergeRequest`; do not alter manual merge defaults.

- [ ] **Step 6: Run merge/worker tests and verify GREEN**

Run: `python -m pytest tests/test_merge_pipeline.py tests/test_worker.py -q`

Expected: all tests pass, including the real short FFmpeg integration test when FFmpeg is available.

- [ ] **Step 7: Commit Task 3**

```bash
git add video_workflow/merge_pipeline.py video_workflow/worker.py tests/test_merge_pipeline.py tests/test_worker.py
git commit -m "feat: publish batch merges safely"
```

### Task 4: Dependency-Aware UI, Guidance, and End-to-End Verification

**Files:**
- Modify: `video_workflow/ui.py:20-33,505-603`
- Modify: `README.md:76-118`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `QueueController.dependency_state()` from Task 1 and three-job batch results from Task 2.
- Produces: queue file summary text `Waiting for parts` for a non-fatal dependency block.

- [ ] **Step 1: Write failing UI tests**

Update `tests/test_ui.py` to assert a complete pair produces status `Batch: 3 added, 0 skipped, 0 issue(s)`. Add a dependent-merge UI test asserting missing future MP4s are not labeled missing, `_waiting_job_ready()` is false until both dependency jobs complete, and `_job_files_summary()` returns `Waiting for parts`. Extend the guide test with `output/TOAN7_C4_B13_T38.mp4`, retained three outputs, and automatic part 1 → part 2 merge text.

- [ ] **Step 2: Run UI tests and verify RED**

Run: `python -m pytest tests/test_ui.py -q`

Expected: FAIL because dependent future inputs are currently shown as missing and the guide lacks auto-merge behavior.

- [ ] **Step 3: Implement dependency-aware UI state**

For a waiting merge with dependencies, consult `dependency_state()`: return no source/video-2 missing labels while non-fatally blocked, display `Waiting for parts`, and keep readiness false. Preserve normal path readiness/status for manual merge jobs and for a dependency-ready batch merge.

- [ ] **Step 4: Update user documentation**

Update `BATCH_FOLDER_GUIDE` and the README example to show all three outputs, exact suffix removal, missing-part warning, fresh per-scan dependency groups, safe overwrite, retained part videos, and part-2-only outro.

- [ ] **Step 5: Run focused and full verification**

Run:

```bash
python -m pytest tests/test_job_store.py tests/test_queue_controller.py tests/test_folder_batch.py tests/test_merge_pipeline.py tests/test_worker.py tests/test_ui.py -q
python -m pytest tests -q
python -m compileall -q video_workflow
./setup.sh --check-only
git diff --check
```

Expected: every command exits 0; the full suite reports zero failures.

- [ ] **Step 6: Commit Task 4**

```bash
git add video_workflow/ui.py README.md tests/test_ui.py
git commit -m "docs: explain batch auto merge workflow"
```

- [ ] **Step 7: Review and restart the Linux UI safely**

Request a whole-branch code review against this spec and resolve all Critical/Important findings. Confirm `.workflow_data/jobs.json` has no running job before stopping the existing `video_workflow.ui` process; if a job is active, leave it running and report that restart is deferred. Otherwise launch `./run-ui.sh`, verify the new process remains alive, and manually confirm the Batch Folder guide is visible.
