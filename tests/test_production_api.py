from __future__ import annotations

import json
import threading
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from video_workflow.web.production_api import (
    ProductionApi,
    ProductionApiError,
    ProductionAuthError,
)


class _State:
    def __init__(self) -> None:
        self.authorization: list[str | None] = []
        self.pages: list[int] = []
        self.uploads: list[tuple[str, bytes]] = []
        self.lesson_status = 200
        self.cc_status = 201
        self.cc_body: dict = {
            "data": {"inserted": 1, "updated": 0, "skipped": 0, "total": 1}
        }


def _multipart_file(content_type: str, body: bytes) -> tuple[str, bytes]:
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
        + body
    )
    parts = list(message.iter_parts())
    assert len(parts) == 1
    part = parts[0]
    assert part.get_param("name", header="content-disposition") == "manifest"
    return part.get_filename(), part.get_payload(decode=True)


@pytest.fixture
def production_stub():
    state = _State()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *args):
            return

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            state.authorization.append(self.headers.get("Authorization"))
            parsed = urlparse(self.path)
            assert parsed.path == "/api/v1/admin/lessons"
            query = parse_qs(parsed.query)
            assert query["courseId"] == ["601"]
            assert query["limit"] == ["200"]
            page = int(query["page"][0])
            state.pages.append(page)
            if state.lesson_status != 200:
                self._json(state.lesson_status, {"message": "không được phép"})
                return
            self._json(
                200,
                {
                    "data": {
                        "items": [{"id": f"lesson-{page}", "order": page}],
                        "total": 2,
                        "totalPages": 2,
                    }
                },
            )

        def do_POST(self):
            state.authorization.append(self.headers.get("Authorization"))
            length = int(self.headers["Content-Length"])
            filename, payload = _multipart_file(
                self.headers["Content-Type"], self.rfile.read(length)
            )
            state.uploads.append((filename, payload))
            if self.path == "/api/v1/admin/bulk-upload/import-manifest":
                self._json(201, {"data": {"jobId": "job-123", "videoCount": 1}})
                return
            assert self.path == "/api/v1/admin/bulk-upload/import-cc-manifest"
            self._json(state.cc_status, state.cc_body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}/api/v1"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_list_lessons_sends_bearer_token_and_reads_every_page(
    production_stub,
) -> None:
    lessons = ProductionApi(production_stub.url, "tok-test").list_lessons(601)

    assert lessons == [
        {"id": "lesson-1", "order": 1},
        {"id": "lesson-2", "order": 2},
    ]
    assert production_stub.pages == [1, 2]
    assert production_stub.authorization == ["Bearer tok-test", "Bearer tok-test"]


def test_auth_error_uses_fixed_vietnamese_message(production_stub) -> None:
    production_stub.lesson_status = 401

    with pytest.raises(
        ProductionAuthError, match="^Token admin không hợp lệ hoặc đã hết hạn$"
    ):
        ProductionApi(production_stub.url, "bad-token").list_lessons(601)


def test_import_manifests_send_manifest_field_with_exact_bytes(
    production_stub, tmp_path: Path
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b'{"videos":[{"courseId":601}]}')
    api = ProductionApi(production_stub.url, "tok-test")

    assert api.import_video_manifest(manifest) == "job-123"
    assert api.import_cc_manifest(manifest) == {
        "inserted": 1,
        "updated": 0,
        "skipped": 0,
        "total": 1,
    }
    assert production_stub.uploads == [
        ("manifest.json", manifest.read_bytes()),
        ("manifest.json", manifest.read_bytes()),
    ]


def test_cc_error_keeps_backend_detail_and_redacts_echoed_token(
    production_stub, tmp_path: Path
) -> None:
    token = "tok-SECRET"
    production_stub.cc_status = 400
    production_stub.cc_body = {
        "message": f"header Bearer {token} không hợp lệ",
        "missingTargets": ["601/lesson/video/vi"],
    }
    manifest = tmp_path / "cc.json"
    manifest.write_text("{}", encoding="utf-8")

    with pytest.raises(ProductionApiError) as caught:
        ProductionApi(production_stub.url, token).import_cc_manifest(manifest)

    assert token not in str(caught.value)
    assert "***" in str(caught.value)
    assert caught.value.detail["missingTargets"] == ["601/lesson/video/vi"]


def test_rejects_control_characters_in_token_without_echoing_token() -> None:
    token = "tok-SECRET\nextra"

    with pytest.raises(ProductionAuthError) as caught:
        ProductionApi("http://127.0.0.1:1/api/v1", token)

    assert str(caught.value) == "Token admin không hợp lệ hoặc đã hết hạn"
    assert token not in str(caught.value)


def test_request_value_error_is_wrapped_without_secret_text() -> None:
    token = "tok-SECRET"

    def invalid_header(_request, **_kwargs):
        raise ValueError(f"Invalid header value Bearer {token}")

    api = ProductionApi(
        "http://127.0.0.1:1/api/v1", token, opener=invalid_header
    )
    with pytest.raises(ProductionApiError) as caught:
        api.list_lessons(601)

    assert str(caught.value) == "Không gửi được yêu cầu tới backend production"
    assert token not in str(caught.value)
