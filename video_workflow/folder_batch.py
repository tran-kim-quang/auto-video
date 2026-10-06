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
            found_part_input = False
            for part in (1, 2):
                output_stem = f"{deck.stem}_{part}"
                media = _matching_files(files, output_stem, MEDIA_EXTENSIONS)
                timelines = _matching_files(
                    files, f"timeline_slide_{output_stem}", TIMELINE_EXTENSIONS
                )
                if not media and not timelines:
                    continue
                found_part_input = True
                if not media:
                    issues.append(
                        FolderIssue(leaf, f"{deck.stem} part {part}: missing media")
                    )
                    continue
                if len(media) > 1:
                    issues.append(
                        FolderIssue(
                            leaf, f"{deck.stem} part {part}: multiple media files"
                        )
                    )
                    continue
                if not timelines:
                    issues.append(
                        FolderIssue(leaf, f"{deck.stem} part {part}: missing timeline")
                    )
                    continue
                if len(timelines) > 1:
                    issues.append(
                        FolderIssue(
                            leaf, f"{deck.stem} part {part}: multiple timeline files"
                        )
                    )
                    continue
                jobs.append(
                    FolderJob(
                        source_media=media[0],
                        pptx=deck,
                        timeline=timelines[0],
                        output=leaf / "output" / f"{output_stem}.mp4",
                        part=part,
                    )
                )
            if not found_part_input:
                issues.append(
                    FolderIssue(leaf, f"{deck.stem}: no part 1 or 2 inputs")
                )

        orphan_parts: dict[tuple[str, int], str] = {}
        for path in files:
            stem = path.stem
            if path.suffix.casefold() in TIMELINE_EXTENSIONS and stem.casefold().startswith(
                "timeline_slide_"
            ):
                stem = stem[len("timeline_slide_") :]
            elif path.suffix.casefold() not in MEDIA_EXTENSIONS:
                continue
            match = re.fullmatch(r"(.+)_([12])", stem)
            if match:
                base, part_text = match.groups()
                orphan_parts[(base.casefold(), int(part_text))] = base
        for (normalized_base, part), base in sorted(orphan_parts.items()):
            if normalized_base not in deck_stems:
                issues.append(FolderIssue(leaf, f"{base} part {part}: missing PPTX"))
    return FolderScanResult(tuple(jobs), tuple(issues))


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.absolute()))


def queue_folder_jobs(
    controller: QueueController, root: Path
) -> FolderQueueResult:
    scan = discover_folder_jobs(root)
    queued: list[JobRecord] = []
    skipped: list[Path] = []
    issues = list(scan.issues)
    known_outputs = {_path_key(job.output_path) for job in controller.jobs()}

    for candidate in scan.jobs:
        output = candidate.output
        report = Path(f"{output}.report.json")
        try:
            if output.exists():
                report.unlink(missing_ok=True)
                skipped.append(output)
                continue
            if _path_key(output) in known_outputs:
                skipped.append(output)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            job = controller.enqueue(
                source_media=candidate.source_media,
                pptx=candidate.pptx,
                timeline=candidate.timeline,
                output_name=output.name,
                output_directory=output.parent,
                write_report=False,
                use_outro=candidate.part == 2,
            )
        except (OSError, ValueError) as exc:
            issues.append(FolderIssue(candidate.pptx.parent, str(exc)))
            continue
        queued.append(job)
        known_outputs.add(_path_key(output))

    return FolderQueueResult(tuple(queued), tuple(skipped), tuple(issues))
