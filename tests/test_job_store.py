from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from video_workflow.job_models import GlobalSettings, JobRecord, JobStatus
from video_workflow.json_store import JsonStore


def _job(tmp_path: Path) -> JobRecord:
    return JobRecord.new(
        pptx=tmp_path / "bài giảng.pptx",
        script=tmp_path / "bài giảng.txt",
        artifact_directory=tmp_path / "output" / "bài giảng",
        output_name="bài giảng.mp4",
        logo=tmp_path / "logo.png",
        outro=tmp_path / "outro.mp4",
        voicestudio_api_url="http://127.0.0.1:3900",
        profile_id="599fc048",
        backend_override=None,
        inference_config_version="tts-v1",
        input_signature={"inputs": {"pptx": {"sha256": "abc"}}},
    )


def test_v1_settings_migrate_assets_and_add_fixed_voicestudio_defaults(
    tmp_path: Path,
) -> None:
    store = JsonStore(tmp_path / "data")
    store.root.mkdir(parents=True)
    (store.root / "settings.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "logo": str(tmp_path / "logo.png"),
                "outro": str(tmp_path / "outro.mp4"),
            }
        ),
        encoding="utf-8",
    )

    settings = store.load_settings()

    assert settings.logo == tmp_path / "logo.png"
    assert settings.outro == tmp_path / "outro.mp4"
    assert settings.voicestudio_api_url == "http://127.0.0.1:3900"
    assert settings.profile_id == "599fc048"
    assert settings.inference_config_version == "tts-v1"
    assert json.loads((store.root / "settings.json").read_text())["schema_version"] == 2


def test_v1_jobs_are_archived_and_new_v2_queue_starts_empty(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "data")
    store.root.mkdir(parents=True)
    legacy = {
        "schema_version": 1,
        "jobs": [{"id": "legacy", "kind": "merge", "source_media": "old.mp4"}],
    }
    (store.root / "jobs.json").write_text(json.dumps(legacy), encoding="utf-8")

    assert store.load_jobs() == []

    archives = list(store.root.glob("jobs.legacy-v1-*.json"))
    assert len(archives) == 1
    archive = json.loads(archives[0].read_text(encoding="utf-8"))
    assert archive["migration_reason"] == "legacy source-media jobs are not narration jobs"
    assert archive["legacy_payload"] == legacy
    assert json.loads((store.root / "jobs.json").read_text()) == {
        "schema_version": 2,
        "jobs": [],
    }


def test_v2_job_round_trip_captures_inputs_assets_profile_and_signature(
    tmp_path: Path,
) -> None:
    store = JsonStore(tmp_path / "data")
    settings = GlobalSettings(
        logo=tmp_path / "ảnh logo.png",
        outro=tmp_path / "video kết thúc.mp4",
        backend_override=tmp_path / "backend/main.py",
    )
    jobs = [_job(tmp_path), replace(_job(tmp_path), id="two", status=JobStatus.FAILED)]

    store.save_settings(settings)
    store.save_jobs(jobs)

    assert store.load_settings() == settings
    assert store.load_jobs() == jobs
    loaded = store.load_jobs()[0]
    assert loaded.pptx.name == "bài giảng.pptx"
    assert loaded.script.name == "bài giảng.txt"
    assert loaded.logo.name == "logo.png"
    assert loaded.outro.name == "outro.mp4"
    assert loaded.profile_id == "599fc048"
    assert loaded.input_signature == {"inputs": {"pptx": {"sha256": "abc"}}}


def test_failed_atomic_replace_keeps_previous_jobs_json(
    tmp_path: Path, monkeypatch
) -> None:
    store = JsonStore(tmp_path / "data")
    original = [_job(tmp_path)]
    store.save_jobs(original)

    monkeypatch.setattr(
        "video_workflow.json_store.os.replace",
        lambda *_args: (_ for _ in ()).throw(OSError("disk busy")),
    )
    with pytest.raises(OSError, match="disk busy"):
        store.save_jobs([replace(original[0], id="changed")])

    assert json.loads((store.root / "jobs.json").read_text())["jobs"][0]["id"] == original[0].id


def test_corrupt_v2_jobs_are_preserved_and_reported(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "data")
    store.root.mkdir(parents=True)
    (store.root / "jobs.json").write_text('{"jobs": [', encoding="utf-8")

    assert store.load_jobs() == []
    assert any(store.root.glob("jobs.json.corrupt-*"))
    assert store.warnings and "jobs.json" in store.warnings[0]
