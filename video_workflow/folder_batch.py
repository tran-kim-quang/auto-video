from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .job_models import JobRecord
    from .queue_controller import QueueController


@dataclass(frozen=True, slots=True)
class FolderJob:
    pptx: Path
    script: Path
    artifact_directory: Path
    output_name: str


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


def _ignored_directory(path: Path) -> bool:
    name = path.name
    return name.casefold() == "output" or name.startswith(".") or path.is_symlink()


def discover_folder_jobs(root: Path) -> FolderScanResult:
    root = Path(root)
    if not root.is_dir():
        return FolderScanResult((), (FolderIssue(root, "input root is not a directory"),))

    jobs: list[FolderJob] = []
    issues: list[FolderIssue] = []

    def record_walk_error(error: OSError) -> None:
        issues.append(
            FolderIssue(
                Path(error.filename) if error.filename else root,
                str(error),
            )
        )

    for current, directories, filenames in os.walk(
        root, topdown=True, onerror=record_walk_error, followlinks=False
    ):
        folder = Path(current)
        directories[:] = sorted(
            (
                name
                for name in directories
                if not _ignored_directory(folder / name)
            ),
            key=str.casefold,
        )
        grouped: dict[str, dict[str, list[Path]]] = {}
        for filename in filenames:
            path = folder / filename
            suffix = path.suffix.casefold()
            if suffix not in {".pptx", ".txt"}:
                continue
            try:
                if path.is_symlink() or not path.is_file():
                    continue
            except OSError as exc:
                issues.append(FolderIssue(folder, f"cannot inspect {filename}: {exc}"))
                continue
            key = path.stem.casefold()
            grouped.setdefault(key, {".pptx": [], ".txt": []})[suffix].append(path)

        for stem, kinds in sorted(grouped.items()):
            decks = sorted(kinds[".pptx"], key=lambda path: path.name.casefold())
            scripts = sorted(kinds[".txt"], key=lambda path: path.name.casefold())
            if len(decks) == 1 and len(scripts) == 1:
                deck = decks[0]
                jobs.append(
                    FolderJob(
                        pptx=deck,
                        script=scripts[0],
                        artifact_directory=folder / "output" / deck.stem,
                        output_name=f"{deck.stem}.mp4",
                    )
                )
            elif not decks:
                issues.append(FolderIssue(folder, f"{stem}: missing PPTX"))
            elif not scripts:
                issues.append(FolderIssue(folder, f"{stem}: missing TXT"))
            else:
                issues.append(
                    FolderIssue(
                        folder,
                        f"{stem}: ambiguous files ({len(decks)} PPTX, {len(scripts)} TXT)",
                    )
                )

    jobs.sort(key=lambda job: (str(job.pptx.parent).casefold(), job.pptx.name.casefold()))
    issues.sort(key=lambda issue: (str(issue.folder).casefold(), issue.message.casefold()))
    return FolderScanResult(tuple(jobs), tuple(issues))


def queue_folder_jobs(controller: QueueController, root: Path) -> FolderQueueResult:
    """Queue discovered lessons through the narration-only controller contract."""
    scan = discover_folder_jobs(root)
    queued: list[JobRecord] = []
    issues = list(scan.issues)
    for candidate in scan.jobs:
        try:
            queued.append(
                controller.enqueue(
                    pptx=candidate.pptx,
                    script=candidate.script,
                    artifact_directory=candidate.artifact_directory,
                    output_name=candidate.output_name,
                )
            )
        except (OSError, ValueError, TypeError) as exc:
            issues.append(FolderIssue(candidate.pptx.parent, str(exc)))
    return FolderQueueResult(tuple(queued), (), tuple(issues))
