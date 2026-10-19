from __future__ import annotations

import threading
import time
from pathlib import Path

from video_workflow.web.gpu import (
    GatedVoiceStudioClient,
    GpuArbiter,
    GpuWaitCancelled,
)


def _thread(target) -> threading.Thread:
    thread = threading.Thread(target=target)
    thread.start()
    return thread


def test_job_turn_is_exclusive_and_fifo() -> None:
    arbiter = GpuArbiter(slots=1)
    release = threading.Event()
    order: list[str] = []

    def worker(name: str) -> None:
        with arbiter.job_turn():
            order.append(name)
            if name == "A":
                release.wait(2)

    a = _thread(lambda: worker("A"))
    while order != ["A"]:
        time.sleep(0.005)
    b = _thread(lambda: worker("B"))
    time.sleep(0.03)
    c = _thread(lambda: worker("C"))
    time.sleep(0.03)
    release.set()
    for thread in (a, b, c):
        thread.join(2)

    assert order == ["A", "B", "C"]


def test_front_jumps_queue() -> None:
    arbiter = GpuArbiter()
    release = threading.Event()
    order: list[str] = []

    def worker(name: str, *, front: bool = False) -> None:
        with arbiter.job_turn(front=front):
            order.append(name)
            if name == "A":
                release.wait(2)

    a = _thread(lambda: worker("A"))
    while order != ["A"]:
        time.sleep(0.005)
    b = _thread(lambda: worker("B"))
    time.sleep(0.03)
    d = _thread(lambda: worker("D", front=True))
    time.sleep(0.03)
    release.set()
    for thread in (a, b, d):
        thread.join(2)

    assert order == ["A", "D", "B"]


def test_interactive_request_served_before_waiting_job_request() -> None:
    arbiter = GpuArbiter()
    release = threading.Event()
    order: list[str] = []

    def worker(name: str, interactive: bool) -> None:
        with arbiter.request(interactive=interactive):
            order.append(name)
            if name == "A":
                release.wait(2)

    a = _thread(lambda: worker("A", False))
    while order != ["A"]:
        time.sleep(0.005)
    b = _thread(lambda: worker("B", False))
    time.sleep(0.03)
    i = _thread(lambda: worker("I", True))
    time.sleep(0.03)
    release.set()
    for thread in (a, b, i):
        thread.join(2)

    assert order == ["A", "I", "B"]


def test_interactive_request_runs_while_job_holds_turn() -> None:
    arbiter = GpuArbiter()
    entered = threading.Event()

    def request() -> None:
        with arbiter.request(interactive=True):
            entered.set()

    with arbiter.job_turn():
        thread = _thread(request)
        assert entered.wait(1)
        thread.join(1)


def test_cancel_while_waiting_raises() -> None:
    arbiter = GpuArbiter()
    cancel = threading.Event()
    caught: list[BaseException] = []

    with arbiter.job_turn():
        def wait() -> None:
            try:
                with arbiter.job_turn(cancel_event=cancel):
                    pass
            except BaseException as exc:
                caught.append(exc)

        thread = _thread(wait)
        time.sleep(0.05)
        cancel.set()
        thread.join(2)

    assert len(caught) == 1
    assert isinstance(caught[0], GpuWaitCancelled)


def test_slots_two_allows_two_turns() -> None:
    arbiter = GpuArbiter(slots=2)
    release = threading.Event()
    entered = threading.Barrier(3)

    def worker() -> None:
        with arbiter.job_turn():
            entered.wait(2)
            release.wait(2)

    one = _thread(worker)
    two = _thread(worker)
    entered.wait(2)
    assert arbiter.turn_holders() == 2
    release.set()
    one.join(2)
    two.join(2)


def test_gated_client_wraps_generate(tmp_path: Path) -> None:
    events: list[str] = []

    class RecordingArbiter(GpuArbiter):
        def request(self, *, interactive, cancel_event=None):
            base = super().request(
                interactive=interactive,
                cancel_event=cancel_event,
            )

            class Guard:
                def __enter__(self):
                    base.__enter__()
                    events.append(f"enter:{interactive}")

                def __exit__(self, *args):
                    events.append("exit")
                    return base.__exit__(*args)

            return Guard()

    class Client:
        marker = "delegated"

        def generate(self, *args, **kwargs):
            events.append("generate")
            return "result"

    gated = GatedVoiceStudioClient(Client(), RecordingArbiter(), interactive=True)

    assert gated.generate("text", tmp_path / "x.wav", object()) == "result"
    assert gated.marker == "delegated"
    assert events == ["enter:True", "generate", "exit"]
