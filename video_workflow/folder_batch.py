from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .job_models import JobRecord
from .queue_controller import QueueController


MEDIA_EXTENSIONS = {".mp4", ".mov", ".mkv", ".mp3", ".wav", ".m4a", ".aac"}
TIMELINE_EXTENSIONS = {".txt", ".json"}


@dataclass(frozen=True, slots=True)
class FolderJob:
    source_media: Path
    pptx: Path
    timeline: Path
    output: Path
    part: int


@dataclass(frozen=True, slots=True)
class FolderIssue:
    folder: Path
    message: str


@dataclass(frozen=True, slots=True)
class FolderScanResult:
    jobs: tuple[FolderJob, ...]
    issues: tuple[FolderIssue, ...]


@dataclass(frozen=True, slots=True)
class FolderQueueResult:
    queued: tuple[JobRecord, ...]
    skipped: tuple[Path, ...]
    issues: tuple[FolderIssue, ...]


def _leaf_directories(root: Path) -> list[Path]:
    leaves: list[Path] = []
    for current, directories, _files in os.walk(root):
        directories[:] = sorted(
            (name for name in directories if name.casefold() != "output"),
            key=str.casefold,
        )
        if not directories:
            leaves.append(Path(current))
    return leaves


def _matching_files(files: list[Path], stem: str, extensions: set[str]) -> list[Path]:
    expected = stem.casefold()
    return sorted(
        (
            path
            for path in files
            if path.stem.casefold() == expected and path.suffix.casefold() in extensions
        ),
        key=lambda path: path.name.casefold(),
    )


def _matching_timelines(files: list[Path], stem: str) -> list[Path]:
    accepted_stems = {stem.casefold(), f"timeline_slide_{stem}".casefold()}
    return sorted(
        (
            path
            for path in files
            if path.stem.casefold() in accepted_stems
            and path.suffix.casefold() in TIMELINE_EXTENSIONS
        ),
        key=lambda path: path.name.casefold(),
    )


def discover_folder_jobs(root: Path) -> FolderScanResult:
    root = Path(root)
    if not root.is_dir():
        return FolderScanResult((), (FolderIssue(root, "input root is not a directory"),))

    jobs: list[FolderJob] = []
    issues: list[FolderIssue] = []
    for leaf in _leaf_directories(root):
        files = [path for path in leaf.iterdir() if path.is_file()]
        decks = sorted(
            (path for path in files if path.suffix.casefold() == ".pptx"),
            key=lambda path: path.name.casefold(),
        )
        deck_stems = {deck.stem.casefold() for deck in decks}
        for deck in decks:
            part_match = re.fullmatch(r".+_([12])", deck.stem)
            if part_match is None:
                issues.append(
                    FolderIssue(
                        leaf, f"{deck.stem}: PPTX name must end with _1 or _2"
                    )
                )
                continue

            part = int(part_match.group(1))
            media = _matching_files(files, deck.stem, MEDIA_EXTENSIONS)
            timelines = _matching_timelines(files, deck.stem)
            if not media:
                issues.append(FolderIssue(leaf, f"{deck.stem}: missing media"))
                continue
            if len(media) > 1:
                issues.append(FolderIssue(leaf, f"{deck.stem}: multiple media files"))
                continue
            if not timelines:
                issues.append(FolderIssue(leaf, f"{deck.stem}: missing timeline"))
                continue
            if len(timelines) > 1:
                issues.append(
                    FolderIssue(leaf, f"{deck.stem}: multiple timeline files")
                )
                continue
            jobs.append(
                FolderJob(
                    source_media=media[0],
                    pptx=deck,
                    timeline=timelines[0],
                    output=leaf / "output" / f"{deck.stem}.mp4",
                    part=part,
                )
            )

        orphan_stems: dict[str, str] = {}
        for path in files:
            stem = path.stem
            if path.suffix.casefold() in TIMELINE_EXTENSIONS:
                if stem.casefold().startswith("timeline_slide_"):
                    stem = stem[len("timeline_slide_") :]
            elif path.suffix.casefold() not in MEDIA_EXTENSIONS:
                continue
            match = re.fullmatch(r"(.+)_([12])", stem)
            if match:
                orphan_stems[stem.casefold()] = stem
        for normalized_stem, stem in sorted(orphan_stems.items()):
            if normalized_stem not in deck_stems:
                issues.append(FolderIssue(leaf, f"{stem}: missing PPTX"))
    return FolderScanResult(tuple(jobs), tuple(issues))


def _delete_reports(root: Path) -> list[FolderIssue]:
    issues: list[FolderIssue] = []
    if not root.is_dir():
        return issues
    for leaf in _leaf_directories(root):
        output_directory = leaf / "output"
        if not output_directory.is_dir():
            continue
        try:
            reports = list(output_directory.glob("*.report.json"))
        except OSError as exc:
            issues.append(FolderIssue(output_directory, str(exc)))
            continue
        for report in reports:
            try:
                report.unlink()
            except OSError as exc:
                issues.append(FolderIssue(output_directory, str(exc)))
    return issues


def queue_folder_jobs(
    controller: QueueController, root: Path
) -> FolderQueueResult:
    root = Path(root)
    scan = discover_folder_jobs(root)
    queued: list[JobRecord] = []
    skipped: list[Path] = []
    issues = list(scan.issues)
    issues.extend(_delete_reports(root))

    for candidate in scan.jobs:
        output = candidate.output
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            job = controller.enqueue(
                source_media=candidate.source_media,
                pptx=candidate.pptx,
                timeline=candidate.timeline,
                output_name=output.name,
                output_directory=output.parent,
                write_report=False,
                use_outro=candidate.part == 2,
                overwrite_output=True,
            )
        except (OSError, ValueError) as exc:
            issues.append(FolderIssue(candidate.pptx.parent, str(exc)))
            continue
        queued.append(job)

    return FolderQueueResult(tuple(queued), tuple(skipped), tuple(issues))
