from __future__ import annotations

import threading
from collections import deque
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from ..voicestudio_client import VoiceStudioClient


class GpuWaitCancelled(RuntimeError):
    pass


class GpuArbiter:
    def __init__(self, slots: int = 1) -> None:
        if slots <= 0:
            raise ValueError("slots must be positive")
        self._slots = slots
        self._condition = threading.Condition()
        self._turn_queue: deque[object] = deque()
        self._turn_holders = 0
        self._interactive_queue: deque[object] = deque()
        self._job_request_queue: deque[object] = deque()
        self._request_holders = 0

    @staticmethod
    def _cancelled(cancel_event: threading.Event | None) -> bool:
        return cancel_event is not None and cancel_event.is_set()

    @contextmanager
    def job_turn(
        self,
        *,
        front: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[None]:
        token = object()
        acquired = False
        with self._condition:
            if front:
                self._turn_queue.appendleft(token)
            else:
                self._turn_queue.append(token)
            while True:
                if self._cancelled(cancel_event):
                    self._turn_queue.remove(token)
                    self._condition.notify_all()
                    raise GpuWaitCancelled("Đã huỷ khi chờ lượt GPU")
                if (
                    self._turn_holders < self._slots
                    and self._turn_queue[0] is token
                ):
                    self._turn_queue.popleft()
                    self._turn_holders += 1
                    acquired = True
                    break
                self._condition.wait(timeout=0.05)
        try:
            yield
        finally:
            if acquired:
                with self._condition:
                    self._turn_holders -= 1
                    self._condition.notify_all()

    @contextmanager
    def request(
        self,
        *,
        interactive: bool,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[None]:
        token = object()
        queue = self._interactive_queue if interactive else self._job_request_queue
        acquired = False
        with self._condition:
            queue.append(token)
            while True:
                if self._cancelled(cancel_event):
                    queue.remove(token)
                    self._condition.notify_all()
                    raise GpuWaitCancelled("Đã huỷ khi chờ GPU")
                next_token = (
                    self._interactive_queue[0]
                    if self._interactive_queue
                    else self._job_request_queue[0]
                    if self._job_request_queue
                    else None
                )
                if self._request_holders < self._slots and next_token is token:
                    queue.popleft()
                    self._request_holders += 1
                    acquired = True
                    break
                self._condition.wait(timeout=0.05)
        try:
            yield
        finally:
            if acquired:
                with self._condition:
                    self._request_holders -= 1
                    self._condition.notify_all()

    def turn_holders(self) -> int:
        with self._condition:
            return self._turn_holders


class GatedVoiceStudioClient:
    def __init__(
        self,
        client: VoiceStudioClient,
        arbiter: GpuArbiter,
        *,
        interactive: bool,
    ) -> None:
        self._client = client
        self._arbiter = arbiter
        self._interactive = interactive

    def generate(self, *args: Any, **kwargs: Any) -> Any:
        with self._arbiter.request(interactive=self._interactive):
            return self._client.generate(*args, **kwargs)

    def flush_memory(self) -> dict[str, Any]:
        with self._arbiter.request(interactive=self._interactive):
            return self._client.flush_memory()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)
