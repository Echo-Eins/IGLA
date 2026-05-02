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


def test_plain_cli_rtlog_prints_turn_and_model_tool_call(make_settings, capsys) -> None:
    settings = make_settings()
    (settings.paths.workspace / "README.md").write_text("# Readme\n", encoding="utf-8")
    llm = OfflineCannedClient(
        [
            {
                "action": "tool_invocation",
                "tool_name": "find_files",
                "tool_version": "1.0.0",
                "input": {"query": "README.md", "max_results": 20},
                "reason": "locate requested file",
            }
        ]
    )
    cli = PlainCLI(settings=settings, llm=llm, rtlog=True)

    outcome = cli.run_once("find README.md")

    assert outcome.status is TaskStatus.DONE
    out = capsys.readouterr().out
    assert "[LLM ->] 3 messages" in out
    assert "[TURN ->]" in out
    assert "[MODEL <-] action=tool_invocation  tool=find_files" in out
    assert '[PARAMS <-] {"query":"README.md","max_results":20}' in out
    assert "[REASON <-] locate requested file" in out


def test_plain_cli_reset_state_removes_poisoned_runtime(make_settings, capsys) -> None:
    settings = make_settings()
    poison = settings.paths.state_dir / "poison.txt"
    poison.parent.mkdir(parents=True, exist_ok=True)
    poison.write_text("bad state", encoding="utf-8")
    cli = PlainCLI(settings=settings, llm=OfflineCannedClient([]))

    should_exit = cli._handle_command("/reset-state")

    assert should_exit is False
    assert not poison.exists()
    assert settings.paths.state_dir.exists()
    out = capsys.readouterr().out
    assert "STATE_RESET removed" in out
