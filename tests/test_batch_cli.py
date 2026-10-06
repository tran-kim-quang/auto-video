from __future__ import annotations

from pathlib import Path

from video_workflow import batch_cli
from video_workflow.batch_models import LessonResult
from video_workflow.batch_workflow import BatchReport


def test_cli_returns_nonzero_and_prints_summary_when_a_lesson_fails(
    capsys, monkeypatch
) -> None:
    def run(_request, *, on_result):
        results = [
            LessonResult("Van8/T8", "completed", Path("/tmp/T8.mp4")),
            LessonResult("Van12/Bài 1/T9", "failed", error="low confidence"),
        ]
        for result in results:
            on_result(result)
        return BatchReport(results=results)

    monkeypatch.setattr(batch_cli, "run_batch", run)

    exit_code = batch_cli.main([])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "Van8/T8: completed" in output
    assert "Van12/Bài 1/T9: failed - low confidence" in output
    assert "completed=1 skipped=0 failed=1" in output


def test_cli_defaults_to_approved_paths_and_has_no_force_option(monkeypatch) -> None:
    requests = []
    monkeypatch.setattr(
        batch_cli,
        "run_batch",
        lambda request, **_kwargs: requests.append(request) or BatchReport(results=[]),
    )

    assert batch_cli.main([]) == 0

    assert requests[0].source_root == Path("/home/meconlonton/Documents/gen_video")
    assert requests[0].assets_dir == Path(
        "/home/meconlonton/work/auto-video/test/logo_and_outro"
    )
    assert requests[0].fps == 24
