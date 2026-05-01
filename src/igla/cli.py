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
from .planner.llm_client import LLMClient, LMStudioClient, OllamaClient
from .policies.constitution import load_constitution
from .protocol.task import TaskStatus
from .state_reset import StateResetError, reset_workspace_state
from .tools.builtin import build_default_toolset


def _build_settings(args: argparse.Namespace) -> IglaSettings:
    workspace = Path(args.workspace).resolve() if args.workspace else None
    settings = load_settings(workspace)
    overrides: dict[str, object] = {}
    lm_overrides: dict[str, object] = {}
    ollama_overrides: dict[str, object] = {}
    provider = getattr(args, "llm_provider", None) or settings.llm_provider
    if getattr(args, "model", None):
        if provider == "ollama":
            ollama_overrides["model"] = args.model
        else:
            lm_overrides["model"] = args.model
    if getattr(args, "lm_url", None):
        lm_overrides["base_url"] = args.lm_url
    if getattr(args, "lm_model", None):
        lm_overrides["model"] = args.lm_model
    if getattr(args, "lm_key", None):
        lm_overrides["api_key"] = args.lm_key
    if getattr(args, "no_schema", False):
        lm_overrides["use_json_schema_response"] = False
        ollama_overrides["use_json_schema_response"] = False
    if getattr(args, "ollama_url", None):
        ollama_overrides["base_url"] = args.ollama_url
    if getattr(args, "ollama_model", None):
        ollama_overrides["model"] = args.ollama_model
    if getattr(args, "ollama_key", None):
        ollama_overrides["api_key"] = args.ollama_key
    if getattr(args, "ollama_keep_alive", None):
        ollama_overrides["keep_alive"] = args.ollama_keep_alive
    if getattr(args, "llm_provider", None):
        overrides["llm_provider"] = args.llm_provider
    if lm_overrides:
        overrides["lm_studio"] = settings.lm_studio.model_copy(update=lm_overrides)
    if ollama_overrides:
        overrides["ollama"] = settings.ollama.model_copy(update=ollama_overrides)
    if overrides:
        settings = settings.model_copy(update=overrides)
    return settings


def _make_llm(settings: IglaSettings) -> LLMClient:
    if settings.llm_provider == "ollama":
        return OllamaClient(
            base_url=settings.ollama.base_url,
            api_key=settings.ollama.api_key,
            model=settings.ollama.model,
            timeout_s=settings.ollama.request_timeout_s,
            temperature=settings.ollama.temperature,
            top_p=settings.ollama.top_p,
            max_tokens=settings.ollama.max_tokens,
            use_json_schema_response=settings.ollama.use_json_schema_response,
            repeat_penalty=settings.ollama.repeat_penalty,
            keep_alive=settings.ollama.keep_alive,
        )
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
        cli = PlainCLI(settings=settings, llm=llm, rtlog=getattr(args, "rtlog", False))
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
        cli = PlainCLI(settings=settings, llm=llm, rtlog=getattr(args, "rtlog", False))
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
    json_schema_enabled = (
        settings.ollama.use_json_schema_response
        if settings.llm_provider == "ollama"
        else settings.lm_studio.use_json_schema_response
    )
    console.print(
        Panel(
            f"workspace: {settings.paths.workspace}\n"
            f"state_dir: {settings.paths.state_dir}\n"
            f"llm_provider: {settings.llm_provider}\n"
            f"LM Studio: {settings.lm_studio.base_url} ({settings.lm_studio.model})\n"
            f"Ollama: {settings.ollama.base_url} ({settings.ollama.model})\n"
            f"json_schema: {json_schema_enabled}",
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
        build_default_toolset(
            ask_user_channel=_DummyAsk(),
            workspace_root=str(settings.paths.workspace),
            receipts=kernel.receipts,
            rollback=kernel.rollback,
            work_log=kernel.work_log,
        )
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
    parser.add_argument(
        "--llm-provider",
        choices=["lmstudio", "ollama"],
        help="LLM backend provider (default: $IGLA_LLM_PROVIDER or lmstudio)",
    )
    parser.add_argument("--model", help="Model name for the selected provider")
    parser.add_argument("--lm-url", help="LM Studio base URL (e.g. http://127.0.0.1:1234/v1)")
    parser.add_argument("--lm-model", help="LM Studio model name")
    parser.add_argument("--lm-key", help="LM Studio API key (default: 'lm-studio')")
    parser.add_argument("--ollama-url", help="Ollama base URL (default: http://127.0.0.1:11434)")
    parser.add_argument("--ollama-model", help="Ollama model name from `ollama list`")
    parser.add_argument("--ollama-key", help="Optional Ollama API key for non-local endpoints")
    parser.add_argument("--ollama-keep-alive", help="Ollama keep_alive value (default: 30m)")
    parser.add_argument(
        "--no-schema",
        action="store_true",
        help="Disable JSON Schema response_format (use json_object fallback)",
    )
    parser.add_argument(
        "--rtlog",
        action="store_true",
        help="Real-time log: print each LLM request/response to the terminal",
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
