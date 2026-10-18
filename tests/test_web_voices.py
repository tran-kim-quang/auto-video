from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from video_workflow.job_models import JobStatus
from video_workflow.voicestudio_client import VoiceStudioConfig
from video_workflow.web.gpu import GpuArbiter
from video_workflow.web.state import WebJob, WebStore
from video_workflow.web.voices import (
    DEFAULT_PREVIEW_TEXT,
    VoiceInUseError,
    VoiceInputError,
    VoiceService,
)


class _Client:
    def __init__(self) -> None:
        self.records = [
            {"id": "other", "name": "Anh Bình", "kind": "clone"},
            {"id": "default", "name": "Cô An", "kind": "clone"},
        ]
        self.created: list[dict] = []
        self.deleted: list[str] = []
        self.flushed = 0
        self.generated: list[tuple[str, str]] = []

    def list_profile_records(self):
        return self.records

    def create_profile(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "newvoice", "name": kwargs["name"], "kind": "clone"}

    def delete_profile(self, profile_id):
        self.deleted.append(profile_id)

    def fetch_profile_audio(self, profile_id):
        return b"audio", "audio/wav"

    def flush_memory(self):
        self.flushed += 1
        return {}

    def generate(self, text, destination, config):
        self.generated.append((text, config.profile_id))
        Path(destination).write_bytes(b"preview")
        return object()


def _service(tmp_path: Path, client: _Client, jobs=(), arbiter=None):
    store = WebStore(tmp_path / "state")
    store.set_default_voice("default")
    return VoiceService(
        client=lambda: client,
        arbiter=arbiter or GpuArbiter(),
        store=store,
        config=VoiceStudioConfig,
        active_jobs=lambda: jobs,
    )


def test_list_puts_default_first(tmp_path: Path) -> None:
    service = _service(tmp_path, _Client())

    voices = service.list_voices()

    assert [voice.id for voice in voices] == ["default", "other"]
    assert voices[0].is_default is True


def test_create_with_transcript_does_not_take_job_turn(tmp_path: Path) -> None:
    client = _Client()
    arbiter = GpuArbiter()
    service = _service(tmp_path, client, arbiter=arbiter)
    release = threading.Event()
    entered = threading.Event()

    def hold() -> None:
        with arbiter.job_turn():
            entered.set()
            release.wait(2)

    holder = threading.Thread(target=hold)
    holder.start()
    assert entered.wait(1)
    started = time.monotonic()
    try:
        voice = service.create(
            name="Cô Lan",
            audio=b"audio",
            filename="lan.wav",
            ref_text="xin chào",
        )
    finally:
        release.set()
        holder.join(2)

    assert time.monotonic() - started < 1
    assert voice.id == "newvoice"


def test_create_without_transcript_waits_for_turn_and_flushes(tmp_path: Path) -> None:
    client = _Client()
    arbiter = GpuArbiter()
    service = _service(tmp_path, client, arbiter=arbiter)
    release = threading.Event()
    held = threading.Event()

    def hold() -> None:
        with arbiter.job_turn():
            held.set()
            release.wait(2)

    holder = threading.Thread(target=hold)
    holder.start()
    assert held.wait(1)
    creator = threading.Thread(
        target=lambda: service.create(
            name="Cô Lan",
            audio=b"audio",
            filename="lan.wav",
            ref_text="",
        )
    )
    creator.start()
    time.sleep(0.05)
    assert client.created == []
    release.set()
    creator.join(2)
    holder.join(2)

    assert len(client.created) == 1
    assert client.flushed == 1


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"name": " ", "audio": b"x", "filename": "a.wav"}, "Cần nhập tên giọng"),
        ({"name": "A", "audio": b"x", "filename": "a.ogg"}, "Chỉ nhận file .wav, .mp3, .m4a"),
        ({"name": "A", "audio": b"x" * (20 * 1024 * 1024 + 1), "filename": "a.wav"}, "File ghi âm vượt quá 20 MB"),
    ],
)
def test_create_validation_messages(tmp_path: Path, kwargs, message: str) -> None:
    with pytest.raises(VoiceInputError, match=message):
        _service(tmp_path, _Client()).create(ref_text="text", **kwargs)


def test_preview_uses_profile_and_default_text(tmp_path: Path) -> None:
    client = _Client()
    service = _service(tmp_path, client)
    destination = tmp_path / "preview.wav"

    assert service.preview("other", "", destination) == destination
    assert client.generated == [(DEFAULT_PREVIEW_TEXT, "other")]


def test_delete_default_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path, _Client())

    with pytest.raises(VoiceInUseError, match="Không xoá được giọng mặc định"):
        service.delete("default")


def test_delete_in_use_rejected(tmp_path: Path) -> None:
    client = _Client()
    jobs = [
        replace(
            WebJob.new(batch_id="b", leaf_relpath=f"leaf-{i}", profile_id="other", script_chars=1),
            status=JobStatus.WAITING,
        )
        for i in range(2)
    ]
    service = _service(tmp_path, client, jobs)

    with pytest.raises(
        VoiceInUseError,
        match="Giọng đang được dùng bởi 2 bài trong hàng đợi",
    ):
        service.delete("other")
    assert client.deleted == []


def test_delete_unused_calls_client(tmp_path: Path) -> None:
    client = _Client()
    service = _service(tmp_path, client)

    service.delete("other")

    assert client.deleted == ["other"]
