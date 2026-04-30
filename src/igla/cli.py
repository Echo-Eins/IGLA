"""IGLA CLI entry point.

Subcommands:
* ``igla chat``   — plain interactive chat (default).
* ``igla run``    — plain one-shot request.
* ``igla rich-chat`` — old Rich REPL.
* ``igla doctor`` — print effective settings + available tools, then exit.
* ``igla version`` — print version.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .chat.plain import PlainCLI
from .chat.repl import ChatREPL
from .config import IglaSettings, load_settings
from .console_io import attach_utf8_buffer, force_utf8_stdio
from .kernel.kernel import Kernel
from .motivation.rule import load_rules
from .planner.llm_client import LMStudioClient
from .policies.constitution import load_constitution
from .protocol.task import TaskStatus
from .state_reset import StateResetError, reset_workspace_state
from .tools.builtin import (
    AskUserTool,
    FindFilesTool,
    NoopObserveTool,
    ReadFileTool,
    SearchTextTool,
)


def _build_settings(args: argparse.Namespace) -> IglaSettings:
    workspace = Path(args.workspace).resolve() if args.workspace else None
    settings = load_settings(workspace)
    overrides: dict[str, object] = {}
    lm_overrides: dict[str, object] = {}
    if args.lm_url:
        lm_overrides["base_url"] = args.lm_url
    if args.lm_model:
        lm_overrides["model"] = args.lm_model
    if args.lm_key:
        lm_overrides["api_key"] = args.lm_key
    if args.no_schema:
        lm_overrides["use_json_schema_response"] = False
    if lm_overrides:
        overrides["lm_studio"] = settings.lm_studio.model_copy(update=lm_overrides)
    if overrides:
        settings = settings.model_copy(update=overrides)
    return settings


def _make_llm(settings: IglaSettings) -> LMStudioClient:
    return LMStudioClient(
        base_url=settings.lm_studio.base_url,
        api_key=settings.lm_studio.api_key,
        model=settings.lm_studio.model,
        timeout_s=settings.lm_studio.request_timeout_s,
        temperature=settings.lm_studio.temperature,
        top_p=settings.lm_studio.top_p,
        max_tokens=settings.lm_studio.max_tokens,
        use_json_schema_response=settings.lm_studio.use_json_schema_response,
        repeat_penalty=settings.lm_studio.repeat_penalty,
    )


def cmd_chat(args: argparse.Namespace) -> int:
    settings = _build_settings(args)
    llm = _make_llm(settings)
    try:
        cli = PlainCLI(settings=settings, llm=llm)
        cli.run_loop()
    finally:
        llm.close()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    request = " ".join(args.request).strip()
    if not request:
        print("error: run requires a request", file=sys.stderr)
        return 2
    settings = _build_settings(args)
    llm = _make_llm(settings)
    try:
        cli = PlainCLI(settings=settings, llm=llm)
        outcome = cli.run_once(request)
    finally:
        llm.close()
    return 0 if outcome.status is TaskStatus.DONE else 1


def cmd_reset_state(args: argparse.Namespace) -> int:
    settings = _build_settings(args)
    try:
        result = reset_workspace_state(
            workspace=settings.paths.workspace,
            state_dir=settings.paths.state_dir,
        )
    except StateResetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if result.removed:
        print(f"removed state_dir: {result.state_dir}")
    else:
        print(f"state_dir did not exist: {result.state_dir}")
    return 0


def cmd_rich_chat(args: argparse.Namespace) -> int:
    settings = _build_settings(args)
    llm = _make_llm(settings)
    try:
        repl = ChatREPL(settings=settings, llm=llm)
        repl.run()
    finally:
        llm.close()
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = _build_settings(args)
    console = Console()
    console.print(
        Panel(
            f"workspace: {settings.paths.workspace}\n"
            f"state_dir: {settings.paths.state_dir}\n"
            f"LM Studio: {settings.lm_studio.base_url} ({settings.lm_studio.model})\n"
            f"json_schema: {settings.lm_studio.use_json_schema_response}",
            title="settings",
            border_style="blue",
        )
    )
    constitution = load_constitution(settings.paths.constitution_file)
    table = Table(title="constitution")
    table.add_column("id")
    table.add_column("rule")
    table.add_column("enabled")
    for entry in constitution.entries:
        table.add_row(entry.id, entry.rule_id, "yes" if entry.enabled else "no")
    console.print(table)
    rules = load_rules(settings.paths.motivation_file)
    table = Table(title="motivation rules")
    table.add_column("id")
    table.add_column("priority")
    table.add_column("triggers")
    table.add_column("effects")
    for rule in rules:
        triggers = ", ".join(t.on_event or t.on_action_kind or "always" for t in rule.triggers)
        effects = ", ".join(e.kind for e in rule.effects)
        table.add_row(rule.id, str(rule.priority), triggers or "—", effects or "—")
    console.print(table)
    kernel = Kernel(settings)

    # Register the default toolset so the doctor shows a useful inventory.
    class _DummyAsk:
        def ask(self, *, question: str, prompt_label: str | None = None) -> str:
            return ""

    kernel.registry.register_many(
        [
            AskUserTool(channel=_DummyAsk()),
            FindFilesTool(workspace_root=str(settings.paths.workspace)),
            ReadFileTool(
                workspace_root=str(settings.paths.workspace),
                receipts=kernel.receipts,
            ),
            SearchTextTool(workspace_root=str(settings.paths.workspace)),
            NoopObserveTool(),
        ]
    )
    table = Table(title="registered tools")
    table.add_column("name")
    table.add_column("version")
    table.add_column("risk")
    table.add_column("capabilities")
    for manifest in kernel.registry.list_tools():
        table.add_row(
            manifest.name,
            manifest.version,
            manifest.risk_level,
            ", ".join(manifest.capabilities),
        )
    console.print(table)
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    print(__version__)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="igla", description="IGLA action runtime")
    parser.add_argument("--workspace", help="Workspace root (default: $IGLA_WORKSPACE or CWD)")
    parser.add_argument("--lm-url", help="LM Studio base URL (e.g. http://127.0.0.1:1234/v1)")
    parser.add_argument("--lm-model", help="LM Studio model name")
    parser.add_argument("--lm-key", help="LM Studio API key (default: 'lm-studio')")
    parser.add_argument(
        "--no-schema",
        action="store_true",
        help="Disable JSON Schema response_format (use json_object fallback)",
    )

    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("chat", help="Start plain interactive chat (default)").set_defaults(
        func=cmd_chat
    )
    run = sub.add_parser("run", help="Run one plain request and print copyable output")
    run.add_argument("request", nargs=argparse.REMAINDER, help="Request text")
    run.set_defaults(func=cmd_run)
    sub.add_parser("reset-state", help="Remove workspace .igla runtime state").set_defaults(
        func=cmd_reset_state
    )
    sub.add_parser("rich-chat", help="Start the old Rich panel chat").set_defaults(
        func=cmd_rich_chat
    )
    sub.add_parser("doctor", help="Print settings, constitution, motivation, tools").set_defaults(
        func=cmd_doctor
    )
    sub.add_parser("version", help="Print version").set_defaults(func=cmd_version)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Make sure user input survives a misconfigured locale (LC_ALL=C,
    # ASCII-only stdio, etc.). Without this, Cyrillic / emoji from input()
    # raises UnicodeDecodeError before the REPL even sees the line.
    force_utf8_stdio()
    attach_utf8_buffer()

    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        # Default subcommand is ``chat``.
        args.func = cmd_chat
        args.cmd = "chat"
    try:
        return int(args.func(args))
    except KeyboardInterrupt:  # pragma: no cover
        print("\naborted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
