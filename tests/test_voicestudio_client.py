from __future__ import annotations

import json
import threading
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from video_workflow.voicestudio_client import (
    VoiceStudioClient,
    VoiceStudioConfig,
    VoiceStudioError,
    VoiceStudioOutOfMemory,
)


WAV = b"RIFF\x04\x00\x00\x00WAVE"


class _State:
    profiles: object = [{"id": "599fc048", "name": "Teacher_male", "kind": "clone"}]
    generate_status = 200
    generate_content_type = "audio/wav"
    generate_body = WAV
    interrupt_generate = False
    form: dict[str, str]
    paths: list[str]

    def __init__(self) -> None:
        self.form = {}
        self.paths = []


def _parse_form(content_type: str, body: bytes) -> dict[str, str]:
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
    )
    return {
        part.get_param("name", header="content-disposition"): part.get_content()
        for part in message.iter_parts()
    }


@pytest.fixture
def http_stub():
    state = _State()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *args):
            return

        def do_GET(self):
            state.paths.append(self.path)
            if self.path != "/profiles":
                self.send_error(404)
                return
            body = json.dumps(state.profiles).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            state.paths.append(self.path)
            if self.path.startswith("/system/flush-memory"):
                body = b'{"flushed":true,"unloaded_model":false}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            length = int(self.headers.get("Content-Length", "0"))
            request_body = self.rfile.read(length)
            state.form = _parse_form(self.headers["Content-Type"], request_body)
            body = state.generate_body
            self.send_response(state.generate_status)
            self.send_header("Content-Type", state.generate_content_type)
            self.send_header("X-Audio-Id", "audio-123")
            self.send_header("X-Gen-Time", "1.25")
            self.send_header("X-OmniVoice-Routing", "cuda")
            if state.interrupt_generate:
                self.send_header("Content-Length", str(len(body) + 100))
            else:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            if state.interrupt_generate:
                self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_generate_sends_literal_semicolons_and_fixed_quality(
    http_stub, tmp_path: Path
) -> None:
    destination = tmp_path / "x.partial"
    config = VoiceStudioConfig(api_url=http_stub.url)

    metadata = VoiceStudioClient(http_stub.url).generate(
        "A (0; 6; 3).", destination, config
    )

    assert http_stub.form == {
        "text": "A (0; 6; 3).",
        "profile_id": "599fc048",
        "engine": "omnivoice",
        "language": "Vietnamese",
        "num_step": "32",
        "seed": "42",
        "max_chunk_chars": "150",
        "postprocess_output": "true",
        "crossfade_ms": "50",
    }
    assert destination.read_bytes() == WAV
    assert metadata.audio_id == "audio-123"
    assert metadata.generation_seconds == 1.25
    assert metadata.routing_headers == {"X-OmniVoice-Routing": "cuda"}


def test_require_profile_matches_identifier_not_display_name(http_stub) -> None:
    client = VoiceStudioClient(http_stub.url)

    assert client.require_profile("599fc048").name == "Teacher_male"
    with pytest.raises(VoiceStudioError, match="profile ID Teacher_male was not found"):
        client.require_profile("Teacher_male")


def test_flush_memory_uses_unload_model_false(http_stub) -> None:
    result = VoiceStudioClient(http_stub.url).flush_memory()

    assert result["flushed"] is True
    assert http_stub.paths[-1] == "/system/flush-memory?unload_model=false"


def test_oom_response_raises_typed_error_with_bounded_detail(
    http_stub, tmp_path: Path
) -> None:
    http_stub.generate_status = 500
    http_stub.generate_content_type = "application/json"
    http_stub.generate_body = (b'{"detail":"CUDA out of memory ' + b"x" * 20_000 + b'"}')

    with pytest.raises(VoiceStudioOutOfMemory) as caught:
        VoiceStudioClient(http_stub.url).generate(
            "Một", tmp_path / "oom.partial", VoiceStudioConfig(api_url=http_stub.url)
        )

    assert len(str(caught.value)) <= 8_300
    assert not (tmp_path / "oom.partial").exists()


def test_huge_html_error_is_truncated_and_never_written_to_destination(
    http_stub, tmp_path: Path
) -> None:
    http_stub.generate_status = 502
    http_stub.generate_content_type = "text/html"
    http_stub.generate_body = b"failure-" + b"z" * 100_000
    destination = tmp_path / "error.partial"

    with pytest.raises(VoiceStudioError) as caught:
        VoiceStudioClient(http_stub.url).generate(
            "Một", destination, VoiceStudioConfig(api_url=http_stub.url)
        )

    assert "failure-" in str(caught.value)
    assert len(str(caught.value)) <= 8_300
    assert not destination.exists()


@pytest.mark.parametrize(
    ("content_type", "body"),
    [("application/json", WAV), ("audio/wav", b"not a wave")],
)
def test_rejects_200_non_wav_content_type_or_invalid_riff(
    http_stub, tmp_path: Path, content_type: str, body: bytes
) -> None:
    http_stub.generate_content_type = content_type
    http_stub.generate_body = body
    destination = tmp_path / "bad.partial"

    with pytest.raises(VoiceStudioError, match="WAV"):
        VoiceStudioClient(http_stub.url).generate(
            "Một", destination, VoiceStudioConfig(api_url=http_stub.url)
        )

    assert not destination.exists()


def test_interrupted_download_leaves_only_partial_for_caller_cleanup(
    http_stub, tmp_path: Path
) -> None:
    http_stub.interrupt_generate = True
    destination = tmp_path / "cut.partial"

    with pytest.raises(VoiceStudioError, match="download"):
        VoiceStudioClient(http_stub.url).generate(
            "Một", destination, VoiceStudioConfig(api_url=http_stub.url)
        )

    assert destination.exists()
    assert destination.read_bytes() == WAV


def test_profile_json_is_bounded_before_parsing(http_stub) -> None:
    http_stub.profiles = ["x" * (1024 * 1024 + 1)]

    with pytest.raises(VoiceStudioError, match="too large"):
        VoiceStudioClient(http_stub.url).list_profiles()
