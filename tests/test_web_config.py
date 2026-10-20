from __future__ import annotations

from pathlib import Path

from video_workflow.job_models import GlobalSettings
from video_workflow.json_store import JsonStore
from video_workflow.web import config
from video_workflow.web.config import (
    WebLayout,
    build_status,
    default_root,
    init_settings,
    lan_urls,
    parse_args,
    upload_readiness,
)


def test_layout_ensure_creates_five_directories(tmp_path: Path) -> None:
    layout = WebLayout(tmp_path).ensure()

    assert all(
        path.is_dir()
        for path in (
            layout.uploads,
            layout.work,
            layout.tts_cache,
            layout.state,
            layout.logs,
        )
    )


def test_default_root_is_video_and_cc() -> None:
    assert default_root() == Path.home() / "video_and_cc"


def test_init_settings_copies_legacy_and_applies_overrides(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    logo = tmp_path / "legacy-logo.png"
    outro = tmp_path / "legacy-outro.mp4"
    JsonStore(legacy).save_settings(GlobalSettings(logo=logo, outro=outro))
    layout = WebLayout(tmp_path / "new").ensure()

    copied = init_settings(layout, logo=None, outro=None, legacy_root=legacy)

    assert copied.logo == logo
    assert copied.outro == outro
    override = tmp_path / "override.png"
    updated = init_settings(layout, logo=override, outro=None, legacy_root=legacy)
    assert updated.logo == override
    assert WebLayout(layout.root).ensure().state.joinpath("settings.json").is_file()


def test_parse_args_defaults(monkeypatch) -> None:
    monkeypatch.delenv("PYRAMID_BACKEND_API", raising=False)
    args = parse_args([])

    assert (args.host, args.port, args.max_active, args.gpu_slots) == (
        "0.0.0.0",
        8080,
        3,
        1,
    )
    assert args.backend_api == "https://api.pyramid.edu.vn/api/v1"


def test_parse_args_max_reference_seconds_default() -> None:
    assert parse_args([]).max_reference_seconds == 7.0
    assert parse_args(["--max-reference-seconds", "6.5"]).max_reference_seconds == 6.5


def test_parse_args_upload_repo_from_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PYRAMID_UPLOAD_REPO", str(tmp_path / "upload-repo"))

    assert parse_args([]).upload_repo == tmp_path / "upload-repo"
    assert parse_args(["--upload-repo", str(tmp_path / "explicit")]).upload_repo == tmp_path / "explicit"


def test_upload_readiness_reports_missing_configuration() -> None:
    assert upload_readiness(None) == (
        False,
        "Chưa cấu hình --upload-repo / PYRAMID_UPLOAD_REPO",
    )


def test_upload_readiness_reports_missing_node_modules(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").touch()
    monkeypatch.setattr(config.shutil, "which", lambda _name: "/usr/bin/node")

    assert upload_readiness(repo) == (
        False,
        "Repo upload chưa cài thư viện (thiếu node_modules)",
    )


def test_upload_readiness_reports_node_missing(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    (repo / "node_modules").mkdir(parents=True)
    (repo / ".env").touch()
    monkeypatch.setattr(config.shutil, "which", lambda _name: None)

    assert upload_readiness(repo) == (False, "Không tìm thấy node trên PATH")


def test_lan_urls_excludes_loopback() -> None:
    assert all("127." not in url for url in lan_urls(8080))


def test_build_status_ready_requires_existing_assets_and_services(
    tmp_path: Path, monkeypatch
) -> None:
    missing = GlobalSettings(
        logo=tmp_path / "missing.png",
        outro=tmp_path / "missing.mp4",
    )
    assert build_status(missing, lambda: True, 8080)["ready"] is False

    logo = tmp_path / "logo.png"
    outro = tmp_path / "outro.mp4"
    logo.write_bytes(b"logo")
    outro.write_bytes(b"outro")
    monkeypatch.setattr(config.shutil, "which", lambda name: f"/usr/bin/{name}")

    status = build_status(GlobalSettings(logo=logo, outro=outro), lambda: True, 8080)

    assert status["ready"] is True
    assert status["logo"] == "logo.png"
    assert status["outro"] == "outro.mp4"


def test_build_status_includes_max_reference_seconds(tmp_path: Path) -> None:
    settings = GlobalSettings(
        logo=tmp_path / "missing.png",
        outro=tmp_path / "missing.mp4",
    )

    status = build_status(
        settings,
        lambda: True,
        8080,
        max_reference_seconds=6.5,
    )

    assert status["max_reference_seconds"] == 6.5


def test_build_status_includes_last_output_root(tmp_path: Path) -> None:
    output_root = str(tmp_path / "Video bài giảng")

    status = build_status(
        GlobalSettings(),
        lambda: False,
        8080,
        last_output_root=output_root,
    )

    assert status["last_output_root"] == output_root
