from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .upload_export import ExportItem, build_cc_tree, build_video_tree


_PROGRESS = re.compile(r"^\[(\d+)/(\d+)\]")


@dataclass
class UploadProgress:
    phase: str
    current: int
    total: int
    errors: list[str]
    video_total: int | None = None
    video_ok: int | None = None
    cc_total: int | None = None
    cc_ok: int | None = None
    video_manifest: Path | None = None
    cc_manifest: Path | None = None


@dataclass(frozen=True, slots=True)
class UploadOutcome:
    status: str
    video_total: int
    video_ok: int
    cc_total: int
    cc_ok: int
    video_manifest: Path | None
    cc_manifest: Path | None
    errors: list[str]
    log_path: Path


def _reported_path(repo: Path, value: str) -> Path:
    path = Path(value.strip())
    return path if path.is_absolute() else repo / path


def redact_r2_values(value: str, env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    secrets = {
        secret
        for key, secret in source.items()
        if key.startswith("R2_") and secret
    }
    for secret in sorted(secrets, key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    return value


def _run_script(
    *,
    repo: Path,
    argv: list[str],
    env: dict[str, str],
    phase: str,
    log,
    errors: list[str],
    on_progress: Callable[[UploadProgress], None],
    on_process: Callable[[Any | None], None],
    popen: Callable[..., Any],
) -> Path | None:
    process = popen(
        argv,
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        text=True,
        bufsize=1,
    )
    on_process(process)
    manifest: Path | None = None
    errors_path: Path | None = None
    try:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = redact_r2_values(raw_line, env)
            log.write(line)
            log.flush()
            clean = line.strip()
            match = _PROGRESS.match(clean)
            if match:
                on_progress(
                    UploadProgress(
                        phase=phase,
                        current=int(match.group(1)),
                        total=int(match.group(2)),
                        errors=list(errors),
                    )
                )
            if "[ERROR]" in clean or "[SKIP]" in clean:
                errors.append(clean)
            if clean.startswith("Manifest:"):
                manifest = _reported_path(repo, clean.partition(":")[2])
            elif clean.startswith("Errors:"):
                errors_path = _reported_path(repo, clean.partition(":")[2])
        returncode = process.wait()
    finally:
        on_process(None)
    if returncode and manifest is None:
        errors.append(f"{phase}: tiến trình kết thúc với mã {returncode}")
    elif returncode and errors and errors_path is not None:
        errors.append(f"Chi tiết lỗi: {errors_path}")
    return manifest


def _read_items(path: Path, key: str, errors: list[str]) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        values = data[key]
        if not isinstance(values, list):
            raise TypeError(f"{key} không phải danh sách")
        return values
    except Exception as exc:
        errors.append(f"Không đọc được manifest {path}: {exc}")
        return []


def run_r2_upload(
    repo: Path,
    export_dir: Path,
    items: Sequence[ExportItem],
    *,
    dry_run: bool,
    log_path: Path,
    on_progress: Callable[[UploadProgress], None],
    popen: Callable[..., Any] = subprocess.Popen,
    node: str = "node",
    on_process: Callable[[Any | None], None] = lambda _process: None,
) -> UploadOutcome:
    repo = Path(repo).resolve()
    export_dir = Path(export_dir).resolve()
    log_path = Path(log_path).resolve()
    video_root = build_video_tree(export_dir, items)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    common_env = os.environ.copy()
    if dry_run:
        common_env["OUTPUT_DIR"] = str(export_dir / "dry-run-output")
    video_env = {**common_env, "SOURCE_DIR": str(video_root)}
    video_argv = [node, "scripts/local-bulk-upload.mjs"]
    if dry_run:
        video_argv.append("--dry-run")

    with log_path.open("w", encoding="utf-8", newline="") as log:
        video_manifest = _run_script(
            repo=repo,
            argv=video_argv,
            env=video_env,
            phase="video",
            log=log,
            errors=errors,
            on_progress=on_progress,
            on_process=on_process,
            popen=popen,
        )
        if video_manifest is None:
            on_progress(UploadProgress("done", 0, len(items), list(errors)))
            return UploadOutcome(
                status="failed",
                video_total=len(items),
                video_ok=0,
                cc_total=0,
                cc_ok=0,
                video_manifest=None,
                cc_manifest=None,
                errors=errors,
                log_path=log_path,
            )

        video_entries = _read_items(video_manifest, "videos", errors)
        uploaded = {
            str(value["relativePath"])
            for value in video_entries
            if isinstance(value, dict) and "relativePath" in value
        }
        successful_items = [
            item for item in items if f"{item.triple}.mp4" in uploaded
        ]
        video_ok = len(successful_items)
        on_progress(
            UploadProgress(
                "cc",
                0,
                video_ok,
                list(errors),
                video_total=len(items),
                video_ok=video_ok,
                cc_total=video_ok,
                cc_ok=0,
                video_manifest=video_manifest,
            )
        )
        try:
            cc_root = build_cc_tree(export_dir, successful_items)
            cc_env = {**common_env, "CC_SOURCE_DIR": str(cc_root)}
            cc_argv = [node, "scripts/local-cc-upload.mjs"]
            if dry_run:
                cc_argv.append("--dry-run")
            cc_manifest = _run_script(
                repo=repo,
                argv=cc_argv,
                env=cc_env,
                phase="cc",
                log=log,
                errors=errors,
                on_progress=on_progress,
                on_process=on_process,
                popen=popen,
            )
        except Exception as exc:
            errors.append(redact_r2_values(f"cc: {exc}", common_env))
            status = "partial" if video_ok else "failed"
            on_progress(UploadProgress("done", 0, video_ok, list(errors)))
            return UploadOutcome(
                status=status,
                video_total=len(items),
                video_ok=video_ok,
                cc_total=video_ok,
                cc_ok=0,
                video_manifest=video_manifest,
                cc_manifest=None,
                errors=errors,
                log_path=log_path,
            )

    cc_entries = (
        _read_items(cc_manifest, "subtitles", errors)
        if cc_manifest is not None
        else []
    )
    cc_ok = len(cc_entries)
    video_total = len(items)
    cc_total = len(successful_items)
    if video_ok == video_total == cc_ok:
        status = "succeeded"
    elif video_ok == 0:
        status = "failed"
    else:
        status = "partial"
    on_progress(UploadProgress("done", cc_ok, cc_total, list(errors)))
    return UploadOutcome(
        status=status,
        video_total=video_total,
        video_ok=video_ok,
        cc_total=cc_total,
        cc_ok=cc_ok,
        video_manifest=video_manifest,
        cc_manifest=cc_manifest,
        errors=errors,
        log_path=log_path,
    )
