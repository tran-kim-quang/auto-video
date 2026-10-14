# Batch TTS Slide Video Design Spec

**Date:** 2026-10-14

**Status:** Ready for written-spec review

**Scope:** Replace the desktop application's source-media slide workflows with one recursive Batch Folder workflow that creates narrated slide videos from same-stem PPTX/TXT pairs.

## 1. Purpose

The application will build complete lecture videos without an original video, a manually supplied narration file, a manually authored timeline, speech-to-text, or visual slide alignment.

For every discovered lesson, the application will:

1. split a known script into one text segment per PowerPoint slide;
2. synthesize each segment with a fixed VoiceStudio voice profile;
3. measure the generated WAV files to derive exact slide timeframes;
4. render the corresponding PowerPoint slide images for those durations;
5. overlay the global logo, append the global outro, and publish a verified final video;
6. retain the per-slide audio, timeline, combined narration, slide images, and detailed report beside the final MP4.

The desktop UI will expose only this Batch Folder workflow. The former single slide-video form and manual two-video merge form will be removed.

## 2. User Inputs and Fixed Configuration

### 2.1 Batch inputs

The user selects:

- one root folder to scan recursively;
- one global logo image;
- one global outro video.

Every lesson is a same-stem pair in a scanned folder:

```text
lesson-name.pptx
lesson-name.txt
```

A folder may contain more than one pair. Matching is case-insensitive for extension and exact by case-folded stem. Ambiguous duplicate stems are reported and are not queued.

The scanner ignores every directory named `output` and application-owned temporary or hidden directories.

### 2.2 Script format

The TXT file contains markers of the form `Slide N:`. Markers may occur on the same line as narration and are removed before synthesis.

- Text before the first `Slide 2:` marker is slide 1.
- An explicit `Slide 1:` marker is also accepted.
- Slide identifiers must resolve to the consecutive sequence `1..N`.
- Every slide must have non-empty narration text.
- The resolved script segment count must exactly equal the PPTX slide count.
- Script text is NFC-normalized before hashing or synthesis.

The script is authoritative. The workflow does not run STT, OCR, forced alignment, or visual matching.

### 2.3 VoiceStudio configuration

The application uses a fixed VoiceStudio clone profile:

- display name: `Teacher_male`;
- profile identifier: globally configurable application setting with default `599fc048`;
- engine: `omnivoice`;
- language: `Vietnamese`;
- iterative decoding steps: `32`;
- seed: one fixed base seed, varied deterministically by VoiceStudio for chunks;
- maximum text chunk: `150` characters;
- post-processing: enabled;
- chunk crossfade: `50` milliseconds.

The profile is not a per-job input. The UI displays the fixed profile as read-only status and fails before synthesis if the configured profile identifier is absent.

## 3. Desktop Experience

The main window contains only:

1. **Global assets** — required logo and outro selectors plus Save assets;
2. **Batch Folder** — root-folder selector, Scan & Add All action, scan results, and concise input rules;
3. **Queue** — job status, current stage, progress, retry, cancellation, and output-folder access.

The former **Build slide video** and **Merge 2 videos** tabs and their source-media/timeline fields are removed. Source-video discovery, automatic visual timeline detection, two-part source-video merge orchestration, and manual merge job creation are no longer reachable application workflows.

The queue remains persistent and serial. At most one job synthesizes or renders at a time so multiple lessons cannot compete for GPU memory.

Progress stages include:

- validating inputs;
- starting or checking VoiceStudio;
- validating the fixed voice profile;
- synthesizing slide `i/N`;
- assembling narration and timeline;
- exporting slide images;
- rendering the lecture;
- preparing and appending the outro;
- verifying and publishing outputs.

Cancellation takes effect after the current synchronous VoiceStudio generation request returns. It prevents subsequent slide synthesis and rendering while preserving already validated WAV files.

## 4. Architecture

```text
Recursive Batch Scanner
        |
        v
same-stem PPTX/TXT jobs
        |
        v
Script Parser + PPTX Count Validator
        |
        v
VoiceStudio Supervisor ---> VoiceStudio HTTP API
        |                         |
        |                         v
        |                 per-slide WAV files
        |                         |
        +-------------------------+
                    |
                    v
Narration Manifest + Timeline Builder
        |
        +--> combined.wav
        +--> timeline.json
        +--> report data
                    |
                    v
Existing PPTX Slide Exporter
                    |
                    v
Existing FFmpeg Slide Renderer
        |
        +--> global logo overlay
        +--> global outro normalization/join
                    |
                    v
Final Media Verification + Atomic Publication
```

### 4.1 Component boundaries

The implementation will add focused components instead of embedding TTS behavior in the UI or FFmpeg renderer:

- **Batch discovery** owns same-stem pairing and scan issues.
- **Script parser** owns marker interpretation and normalized slide text.
- **VoiceStudio supervisor** owns health checks, process startup, startup timeout, and process ownership.
- **VoiceStudio client** owns profile validation, generation requests, API errors, and memory-flush requests.
- **Narration builder** owns resumable per-slide synthesis, WAV validation, manifests, concatenation, and timeframes.
- **Batch pipeline** orchestrates narration, existing slide export, existing composition primitives, verification, and publication.
- **Queue worker/UI** translate pipeline callbacks into persisted job stages and visible progress.

The existing slide export, media probing, logo overlay, outro normalization, H.264/AAC rendering, and final verification code should be reused where their contracts remain valid.

## 5. VoiceStudio Supervision

Before the first synthesis call, the supervisor probes the configured local VoiceStudio API (default `http://127.0.0.1:3900`) with a short timeout and retrieves the profile list.

If the API is unavailable, it locates the installed VoiceStudio backend, launches it with CUDA selected, captures its logs in application-owned job logs, and waits up to 120 seconds for the API to respond. Installation paths may be platform-specific and can be overridden in application settings; the current Linux installation under `/opt/VoiceStudio` is the default discovery target.

Process ownership rules:

- A VoiceStudio instance that was already running is never terminated by this application.
- A process launched by this application is tracked by the supervisor and shut down during an orderly application exit after the queue stops.
- A process that exits before readiness produces a startup error containing its log location.
- A responsive API without the configured profile fails validation and is not silently replaced by another same-name profile.

The supervisor does not load OmniVoice in the `auto-video` process. All model inference remains behind VoiceStudio's HTTP API.

## 6. Narration Generation and Timeframes

Each slide is synthesized separately. A successful response must be HTTP 200, identify WAV content, decode as non-empty audio, and have a positive duration reported by `ffprobe` or the existing media probe.

Output is first written as `slide_NNN.partial`. It is renamed to `slide_NNN.wav` only after validation. A failed or malformed response never replaces a validated WAV.

An `audio-manifest.json` records, for every slide:

- slide identifier;
- normalized narration text;
- text hash;
- profile identifier;
- inference settings and their version;
- WAV filename, size, sample rate, channel count, duration, and content hash;
- VoiceStudio audio ID, generation time, and routing headers when available.

A cached WAV is reused only when all identity fields and media checks still match. Editing one slide's text invalidates only that slide. Changing the voice profile or inference configuration invalidates every slide.

After all slides succeed, their measured WAV durations produce a contiguous timeline beginning at zero. The workflow writes:

- `timeline.json` in the existing renderer's array schema using integer milliseconds;
- `combined.wav`, concatenating slide WAVs in slide order without inserted gaps;
- report entries with seconds and formatted timestamps for inspection.

Frame conversion for rendering uses the existing timeline validator. Any millisecond-to-frame rounding is normalized by that validator; there are no timeline gaps or overlaps.

## 7. GPU Memory and Retry Policy

The approved 32-step quality configuration is never silently downgraded to 8 steps.

On a CUDA out-of-memory response for a slide:

1. request VoiceStudio memory flushing;
2. wait for the API to become responsive again;
3. retry the same slide once with the same quality and identity settings;
4. fail the lesson with a precise GPU-memory error if the retry fails.

The workflow does not silently switch to CPU, change voice, shorten the script, reduce quality, or publish partial narration. Other queued lessons remain available for retry after the failed job.

## 8. Output Contract

For `lesson-name.pptx` and `lesson-name.txt`, output is stored under:

```text
<leaf>/output/lesson-name/
  lesson-name.mp4
  report.json
  timeline.json
  combined.wav
  audio-manifest.json
  audio/
    slide_001.wav
    slide_002.wav
    ...
  slide-images/
    slide_001.png
    slide_002.png
    ...
```

`report.json` contains:

- absolute input paths and SHA-256 hashes for PPTX, TXT, logo, and outro;
- the fixed profile ID and complete inference settings;
- one entry per slide with text, audio file, start, end, duration, and generation metadata;
- total narration and outro durations;
- final expected and measured video durations;
- final media checks.

The final MP4 contract remains:

- 1280×720;
- configured FPS, default 24;
- H.264 video;
- AAC stereo audio;
- global logo over the narrated lecture only;
- normalized global outro appended once after the last slide.

## 9. Resume, Rebuild, and Publication

The application computes input hashes and a pipeline-configuration version. A completed lesson is skipped only when:

- its report matches the current PPTX, TXT, logo, outro, fixed voice profile, and inference/render settings;
- the final MP4 passes media validation;
- required retained artifacts exist and validate.

If the completed signature is stale, the lesson is rebuilt. Compatible slide WAVs may still be reused according to the audio manifest.

Final video, timeline, combined narration, and report are constructed in a staging directory on the destination filesystem. Existing published output is replaced only after the staged result passes all checks. Failure or cancellation preserves the previous valid published output and the reusable validated WAV cache.

Repeated scans create fresh queue records but must not allow an older queued record to overwrite a newer build with a different input signature. A job validates its captured inputs immediately before publication.

## 10. Discovery and Failure Behavior

Discovery reports issues per folder and continues scanning:

- PPTX without same-stem TXT;
- TXT without same-stem PPTX;
- duplicate case-folded stems;
- files inside ignored output/application directories;
- missing global logo or outro;
- unreadable input files.

Runtime failures are isolated per lesson. One invalid script, VoiceStudio error, export failure, or render failure does not stop unrelated queued lessons.

User-facing errors identify the lesson, failed stage, concise cause, and job log path. Backend response bodies and FFmpeg stderr are retained in logs without placing large diagnostic payloads in the queue state.

## 11. Persistence and Compatibility

Job persistence is migrated to a narration job schema containing PPTX, TXT, output folder, captured asset paths, input signature, status, stage, and progress. Existing saved source-media and merge jobs from the previous schema are not executed by the new workflow; they are archived during migration with an explanatory legacy status rather than interpreted as narration jobs.

Global settings retain logo and outro and add:

- fixed VoiceStudio profile ID;
- VoiceStudio API base URL;
- optional VoiceStudio installation/backend override;
- inference-config version.

The application remains a single-worker desktop queue on supported Linux and Windows systems. Linux uses LibreOffice/Poppler for slide export; Windows uses PowerPoint COM as before.

## 12. Verification

Automated tests will cover:

- recursive same-stem discovery, multiple pairs per folder, ignored output trees, and missing/ambiguous inputs;
- marker parsing on one line or multiple lines, explicit/implicit slide 1, consecutive numbering, empty text, Unicode NFC, and mathematical semicolons;
- exact script/PPTX slide-count validation;
- VoiceStudio health detection, managed startup, startup timeout, external-process ownership, and missing fixed profile;
- correct multipart generation requests and preservation of literal script text;
- atomic WAV publication, malformed responses, measured durations, and audio-manifest reuse/invalidation;
- one memory-flush retry on CUDA OOM without quality downgrade;
- contiguous timeline generation and combined narration ordering;
- cancellation after the current slide and resume from completed WAVs;
- logo overlay, one outro append, 1280×720 H.264/AAC output, and duration verification;
- completed-output skipping, stale-signature rebuilding, safe replacement, and preservation on failure;
- job-store migration, queue restart, removal of the two obsolete UI flows, and stage/progress display.

An end-to-end smoke test will use a real PPTX/TXT pair, the fixed VoiceStudio profile, global logo, and global outro. It must produce and verify every artifact in the output contract and confirm the resulting narration by human listening before batch-wide use.

## 13. Explicit Non-Goals

This feature does not:

- accept or inspect an original lecture video;
- use STT, forced alignment, OCR, visual matching, or source-video slide detection;
- expose per-job voice selection or inference-quality controls;
- run more than one synthesis/render job concurrently;
- merge two arbitrary videos;
- silently repair missing slide narration, invent text, or guess slide mappings;
- silently downgrade GPU inference quality or switch to another voice/model.
