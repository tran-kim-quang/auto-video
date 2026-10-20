from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4


DEFAULT_BACKEND_API = "https://api.pyramid.edu.vn/api/v1"


class ProductionApiError(RuntimeError):
    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.detail = detail


class ProductionAuthError(ProductionApiError):
    pass


def redact_token(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


class ProductionApi:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 30.0,
        opener=urllib.request.urlopen,
    ) -> None:
        if any(ord(character) < 32 or ord(character) == 127 for character in token):
            raise ProductionAuthError(
                "Token admin không hợp lệ hoặc đã hết hạn"
            )
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._opener = opener

    def _error_detail(self, body: bytes) -> tuple[Any, str]:
        text = body.decode("utf-8", errors="replace")
        try:
            detail = json.loads(text)
        except json.JSONDecodeError:
            detail = text
        if isinstance(detail, dict):
            message = detail.get("message") or detail.get("detail") or text
        else:
            message = text
        return detail, str(message)

    def _request(self, request: urllib.request.Request) -> Any:
        try:
            response = self._opener(request, timeout=self.timeout)
            try:
                status = getattr(response, "status", response.getcode())
                body = response.read()
            finally:
                response.close()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            if exc.code in (401, 403):
                raise ProductionAuthError(
                    "Token admin không hợp lệ hoặc đã hết hạn"
                ) from exc
            detail, message = self._error_detail(body)
            raise ProductionApiError(
                redact_token(f"Backend trả HTTP {exc.code}: {message}", self.token),
                detail=detail,
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ProductionApiError(
                redact_token(
                    f"Không kết nối được backend production: {exc}", self.token
                )
            ) from exc
        except ValueError as exc:
            raise ProductionApiError(
                "Không gửi được yêu cầu tới backend production"
            ) from exc

        if not 200 <= status < 300:
            if status in (401, 403):
                raise ProductionAuthError(
                    "Token admin không hợp lệ hoặc đã hết hạn"
                )
            detail, message = self._error_detail(body)
            raise ProductionApiError(
                redact_token(f"Backend trả HTTP {status}: {message}", self.token),
                detail=detail,
            )
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProductionApiError(
                redact_token(
                    f"Backend production trả dữ liệu JSON không hợp lệ: {exc}",
                    self.token,
                )
            ) from exc

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

    def list_lessons(self, course_id: int) -> list[dict[str, Any]]:
        lessons: list[dict[str, Any]] = []
        page = 1
        total_pages = 1
        while page <= total_pages:
            query = urllib.parse.urlencode(
                {"courseId": course_id, "limit": 200, "page": page}
            )
            request = urllib.request.Request(
                f"{self.base_url}/admin/lessons?{query}", headers=self._headers()
            )
            payload = self._request(request)
            data = payload["data"]
            lessons.extend(data.get("items", []))
            total_pages = int(data.get("totalPages") or 1)
            page += 1
        return lessons

    def _multipart_request(self, endpoint: str, path: Path) -> Any:
        path = Path(path)
        boundary = f"----auto-video-{uuid4().hex}"
        filename = path.name.replace('"', "")
        body = b"".join(
            [
                f"--{boundary}\r\n".encode(),
                (
                    'Content-Disposition: form-data; name="manifest"; '
                    f'filename="{filename}"\r\n'
                ).encode(),
                b"Content-Type: application/json\r\n\r\n",
                path.read_bytes(),
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
        )
        headers = self._headers()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        request = urllib.request.Request(
            f"{self.base_url}{endpoint}", data=body, headers=headers, method="POST"
        )
        return self._request(request)

    def import_video_manifest(self, path: Path) -> str:
        payload = self._multipart_request(
            "/admin/bulk-upload/import-manifest", path
        )
        return str(payload["data"]["jobId"])

    def import_cc_manifest(self, path: Path) -> dict[str, Any]:
        payload = self._multipart_request(
            "/admin/bulk-upload/import-cc-manifest", path
        )
        return dict(payload["data"])
