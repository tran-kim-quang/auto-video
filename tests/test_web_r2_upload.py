from __future__ import annotations

import json
from pathlib import Path

from video_workflow.web.r2_upload import run_r2_upload
from video_workflow.web.upload_export import ExportItem


class FakeProcess:
    def __init__(self, lines: list[str], returncode: int) -> None:
        self.stdout = iter(lines)
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode


class FakePopen:
    def __init__(self, scenarios: list[tuple[list[str], int]]) -> None:
        self.scenarios = list(scenarios)
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        lines, returncode = self.scenarios.pop(0)
        return FakeProcess(lines, returncode)


def _items(tmp_path: Path, count: int = 1) -> list[ExportItem]:
    items = []
    for number in range(1, count + 1):
        output = tmp_path / f"leaf-{number}" / "output"
        output.mkdir(parents=True)
        video = output / f"source-{number}.mp4"
        vtt = output / "vi.vtt"
        video.write_bytes(f"video-{number}".encode())
        vtt.write_text(f"WEBVTT\n\n{number}\n", encoding="utf-8")
        items.append(
            ExportItem(
                relpath=f"root/Toan6/Bài {number}_X/T{number}_Y",
                triple=f"Toan6/Bài {number}_X/T{number}_Y",
                video=video,
                vtt=vtt,
            )
        )
    return items


def _manifest(path: Path, key: str, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({key: values}), encoding="utf-8")


def test_runs_video_then_cc_with_env(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    export = tmp_path / "export"
    video_manifest = tmp_path / "video.json"
    cc_manifest = tmp_path / "cc.json"
    items = _items(tmp_path)
    _manifest(video_manifest, "videos", [{"relativePath": f"{items[0].triple}.mp4"}])
    _manifest(cc_manifest, "subtitles", [{"relativePath": f"{items[0].triple}/vi.vtt"}])
    popen = FakePopen(
        [
            ([f"Manifest: {video_manifest}\n"], 0),
            ([f"Manifest: {cc_manifest}\n"], 0),
        ]
    )

    outcome = run_r2_upload(
        repo,
        export,
        items,
        dry_run=True,
        log_path=tmp_path / "upload.log",
        on_progress=lambda _progress: None,
        popen=popen,
    )

    assert outcome.status == "succeeded"
    assert len(popen.calls) == 2
    video_argv, video_kwargs = popen.calls[0]
    cc_argv, cc_kwargs = popen.calls[1]
    assert video_argv == ["node", "scripts/local-bulk-upload.mjs", "--dry-run"]
    assert cc_argv == ["node", "scripts/local-cc-upload.mjs", "--dry-run"]
    assert video_kwargs["env"]["SOURCE_DIR"] == str(export / "video")
    assert cc_kwargs["env"]["CC_SOURCE_DIR"] == str(export / "cc")
    assert video_kwargs["env"]["OUTPUT_DIR"] == str(export / "dry-run-output")
    assert cc_kwargs["env"]["OUTPUT_DIR"] == str(export / "dry-run-output")
    assert video_kwargs["cwd"] == repo
    assert cc_kwargs["cwd"] == repo
    assert video_kwargs["encoding"] == "utf-8"


def test_relative_paths_are_resolved_before_upload_repo_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    items = _items(tmp_path)
    video_manifest = tmp_path / "video.json"
    cc_manifest = tmp_path / "cc.json"
    _manifest(video_manifest, "videos", [{"relativePath": f"{items[0].triple}.mp4"}])
    _manifest(cc_manifest, "subtitles", [{"relativePath": f"{items[0].triple}/vi.vtt"}])
    popen = FakePopen(
        [
            ([f"Manifest: {video_manifest}\n"], 0),
            ([f"Manifest: {cc_manifest}\n"], 0),
        ]
    )
    monkeypatch.chdir(tmp_path)

    run_r2_upload(
        Path("repo"),
        Path("export"),
        items,
        dry_run=True,
        log_path=Path("upload.log"),
        on_progress=lambda _progress: None,
        popen=popen,
    )

    _, video_kwargs = popen.calls[0]
    _, cc_kwargs = popen.calls[1]
    assert video_kwargs["cwd"] == repo
    assert video_kwargs["env"]["SOURCE_DIR"] == str(tmp_path / "export" / "video")
    assert cc_kwargs["env"]["CC_SOURCE_DIR"] == str(tmp_path / "export" / "cc")
    assert video_kwargs["env"]["OUTPUT_DIR"] == str(
        tmp_path / "export" / "dry-run-output"
    )


def test_no_manifest_means_failed_and_skips_cc(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    popen = FakePopen([(["fatal error\n"], 1)])

    outcome = run_r2_upload(
        repo,
        tmp_path / "export",
        _items(tmp_path),
        dry_run=False,
        log_path=tmp_path / "upload.log",
        on_progress=lambda _progress: None,
        popen=popen,
    )

    assert outcome.status == "failed"
    assert outcome.video_manifest is None
    assert len(popen.calls) == 1


def test_partial_video_only_uploads_cc_for_successes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    export = tmp_path / "export"
    items = _items(tmp_path, 2)
    video_manifest = tmp_path / "video.json"
    cc_manifest = tmp_path / "cc.json"
    _manifest(video_manifest, "videos", [{"relativePath": f"{items[0].triple}.mp4"}])
    _manifest(cc_manifest, "subtitles", [{"relativePath": f"{items[0].triple}/vi.vtt"}])
    popen = FakePopen(
        [
            (["  [ERROR] broken second video\n", f"Manifest: {video_manifest}\n"], 1),
            ([f"Manifest: {cc_manifest}\n"], 0),
        ]
    )

    outcome = run_r2_upload(
        repo,
        export,
        items,
        dry_run=True,
        log_path=tmp_path / "upload.log",
        on_progress=lambda _progress: None,
        popen=popen,
    )

    assert outcome.status == "partial"
    assert outcome.video_ok == 1
    assert (export / "cc" / items[0].triple / "vi.vtt").is_file()
    assert not (export / "cc" / items[1].triple).exists()


def test_log_has_output_and_no_r2_values(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sekret")
    repo = tmp_path / "repo"
    repo.mkdir()
    log_path = tmp_path / "upload.log"
    popen = FakePopen([(["normal output\n", "[ERROR] token=sekret\n"], 1)])

    outcome = run_r2_upload(
        repo,
        tmp_path / "export",
        _items(tmp_path),
        dry_run=False,
        log_path=log_path,
        on_progress=lambda _progress: None,
        popen=popen,
    )

    log = log_path.read_text(encoding="utf-8")
    assert "normal output" in log
    assert "sekret" not in log
    assert all("sekret" not in error for error in outcome.errors)


def test_cc_setup_failure_preserves_video_manifest_and_successes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    items = _items(tmp_path)
    video_manifest = tmp_path / "video.json"
    _manifest(video_manifest, "videos", [{"relativePath": f"{items[0].triple}.mp4"}])
    popen = FakePopen([([f"Manifest: {video_manifest}\n"], 0)])
    progress = []

    def fail_cc_tree(*_args, **_kwargs):
        raise OSError("cannot copy subtitle")

    monkeypatch.setattr("video_workflow.web.r2_upload.build_cc_tree", fail_cc_tree)

    outcome = run_r2_upload(
        repo,
        tmp_path / "export",
        items,
        dry_run=True,
        log_path=tmp_path / "upload.log",
        on_progress=progress.append,
        popen=popen,
    )

    assert outcome.status == "partial"
    assert outcome.video_manifest == video_manifest
    assert (outcome.video_total, outcome.video_ok) == (1, 1)
    assert (outcome.cc_total, outcome.cc_ok) == (1, 0)
    assert any(item.video_manifest == video_manifest for item in progress)


def test_progress_parsed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    items = _items(tmp_path)
    video_manifest = tmp_path / "video.json"
    cc_manifest = tmp_path / "cc.json"
    _manifest(video_manifest, "videos", [{"relativePath": f"{items[0].triple}.mp4"}])
    _manifest(cc_manifest, "subtitles", [{"relativePath": f"{items[0].triple}/vi.vtt"}])
    popen = FakePopen(
        [
            (["[2/3] Toan6/Bài 1_X/T1_Y.mp4\n", f"Manifest: {video_manifest}\n"], 0),
            ([f"Manifest: {cc_manifest}\n"], 0),
        ]
    )
    progress = []

    run_r2_upload(
        repo,
        tmp_path / "export",
        items,
        dry_run=True,
        log_path=tmp_path / "upload.log",
        on_progress=progress.append,
        popen=popen,
    )

    assert any(
        item.phase == "video" and item.current == 2 and item.total == 3
        for item in progress
    )
