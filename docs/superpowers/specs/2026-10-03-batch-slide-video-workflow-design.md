# Batch Slide Video Workflow Design

**Date:** 2026-10-03

## Goal

Add a native batch command to `auto-video` that processes the lesson folders in
`/home/meconlonton/Documents/gen_video`. Each lesson contains two source videos
and two 20-slide PPTX files. The command produces a merged 40-slide deck,
automatically derives slide timelines from the source-video imagery, renders the
two slide videos, and joins them into one final video.

## Input discovery

- Scan direct child directories whose names match `T<number>`.
- Pair `P1_Tn_V8.mp4` with `Tn_1_Slide.pptx` and `P2_Tn_V8.mp4` with
  `Tn_2_Slide.pptx`.
- Use `logo.png` and `Outro720.mp4` from
  `/home/meconlonton/work/auto-video/test/logo_and_outro`.
- A folder with missing or ambiguous inputs is reported and skipped without
  preventing other folders from running.

## Processing flow

1. Create the lesson's `output` directory.
2. Merge part 1 and part 2 PPTX files in order into one 40-slide PPTX saved in
   `output`.
3. Export the merged deck to reference images.
4. Sample source-video frames and compare them with the corresponding slide
   references using perceptual image similarity. Locate each transition more
   precisely around the coarse match, remove brief transition noise, and emit a
   contiguous timeline starting at zero.
5. Part 1 maps only to merged slide IDs 1-20. Part 2 maps only to IDs 21-40.
6. Render part 1 with the logo and without an outro.
7. Render part 2 with the logo and append the outro.
8. Join the two rendered parts into the final lesson video.

The implementation reuses the existing slide export, timeline validation,
rendering, outro, validation, and video-join components wherever their current
contracts fit.

## Outputs and restart behavior

Each `Tn/output` directory contains the merged PPTX, both generated timeline
files, a matching-confidence report, two rendered part videos, and the final
joined video.

If the final video already exists and passes media validation, the entire lesson
is considered complete and is skipped. There is no force/rebuild option. If a
previous run stopped midway, valid intermediate files are reused and missing
steps continue. Temporary files must not be mistaken for completed outputs.

## Matching confidence and failures

The confidence report records the chosen slide and similarity score for each
detected interval. Obviously invalid timelines (non-monotonic slide order,
missing usable matches, gaps, overlaps, or non-positive durations) are rejected
before rendering. Low-confidence matches are reported clearly; the lesson is
left incomplete so it can be inspected and retried, while the batch continues
with remaining lessons.

## Scope

This change adds a native command-line batch workflow only. It does not add a UI
flow, speech transcription, OCR-based alignment, a force option, or automatic
overwriting of completed lessons.

## Verification

- Unit coverage for input pairing, part-to-slide ranges, output completion
  detection, and timeline cleanup/validation.
- A focused integration test using short synthetic media and slide images.
- A dry discovery run against the current `gen_video` directory, followed by a
  single-lesson end-to-end run before processing all lessons.
