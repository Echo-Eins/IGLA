"""Plain CLI behavior."""
from __future__ import annotations

from igla.chat.plain import PlainCLI
from igla.planner.llm_client import OfflineCannedClient
from igla.protocol.task import TaskStatus


def test_plain_cli_run_once_prints_copyable_timeline(make_settings, capsys) -> None:
    settings = make_settings()
    llm = OfflineCannedClient(
        [
            {
                "action": "declare_task_done",
                "summary": "done",
                "reason": "simple request completed",
            }
        ]
    )
    cli = PlainCLI(settings=settings, llm=llm)

    outcome = cli.run_once("hello")

    assert outcome.status is TaskStatus.DONE
    out = capsys.readouterr().out
    assert "TASK task_" in out
    assert "GOAL hello" in out
    assert "STATUS done" in out
    assert "EVENTS" in out
    assert "llm_request_sent actor=planner" in out
