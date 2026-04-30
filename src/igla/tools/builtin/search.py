"""Workspace-bounded discovery tools.

These are pure read-only tools used before asking the user for file paths.
They intentionally do not shell out to grep/find: the MVP keeps discovery
deterministic, schema-validated, and constrained to the configured workspace.
"""
from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterator
from pathlib import Path

from ...protocol.invocation import ToolInvocation
from ...protocol.manifest import (
    PolicyRequirements,
    ResourceLimits,
    SandboxSpec,
    ToolManifest,
    VerifierSpec,
)
from ...protocol.result import ToolResult
from ..base import Tool

_ALWAYS_SKIP_DIRS = {
    ".git",
    ".igla",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "dist",
}

_MAX_LINE_CHARS = 500


_FIND_FILES_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "anyOf": [{"required": ["query"]}, {"required": ["glob"]}],
    "properties": {
        "query": {"type": "string", "minLength": 1},
        "glob": {"type": "string", "minLength": 1},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
        "include_hidden": {"type": "boolean"},
        "case_sensitive": {"type": "boolean"},
    },
}

_FIND_FILES_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["workspace_root", "matches", "count", "truncated"],
    "properties": {
        "workspace_root": {"type": "string"},
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "relative_path", "name", "size_bytes"],
                "properties": {
                    "path": {"type": "string"},
                    "relative_path": {"type": "string"},
                    "name": {"type": "string"},
                    "size_bytes": {"type": "integer", "minimum": 0},
                },
            },
        },
        "count": {"type": "integer", "minimum": 0},
        "truncated": {"type": "boolean"},
    },
}

_SEARCH_TEXT_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["query"],
    "properties": {
        "query": {"type": "string", "minLength": 1},
        "path_glob": {"type": "string", "minLength": 1},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
        "max_file_bytes": {"type": "integer", "minimum": 1, "maximum": 10_000_000},
        "include_hidden": {"type": "boolean"},
        "case_sensitive": {"type": "boolean"},
    },
}

_SEARCH_TEXT_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "workspace_root",
        "matches",
        "count",
        "truncated",
        "searched_files",
        "skipped_files",
    ],
    "properties": {
        "workspace_root": {"type": "string"},
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "relative_path", "line_number", "line"],
                "properties": {
                    "path": {"type": "string"},
                    "relative_path": {"type": "string"},
                    "line_number": {"type": "integer", "minimum": 1},
                    "line": {"type": "string"},
                },
            },
        },
        "count": {"type": "integer", "minimum": 0},
        "truncated": {"type": "boolean"},
        "searched_files": {"type": "integer", "minimum": 0},
        "skipped_files": {"type": "integer", "minimum": 0},
    },
}


class FindFilesTool(Tool):
    """Find files by basename substring and/or glob inside the workspace."""

    def __init__(self, *, workspace_root: str) -> None:
        super().__init__(
            ToolManifest(
                id="tool.core.find_files",
                name="find_files",
                namespace="core.fs",
                version="1.0.0",
                description=(
                    "Find files inside the configured workspace by filename query "
                    "and/or glob. Use this before asking the user where a file is."
                ),
                summary="Find workspace files by name or glob.",
                capabilities=["fs.find", "fs.discover", "core.find_files"],
                risk_level="read_only",
                side_effects=False,
                input_schema=_FIND_FILES_INPUT_SCHEMA,
                output_schema=_FIND_FILES_OUTPUT_SCHEMA,
                policies=PolicyRequirements(),
                resources=ResourceLimits(timeout_seconds=30, max_file_size_mb=10),
                sandbox=SandboxSpec(profile="read_only_file_access"),
                verifier=VerifierSpec(type="schema_validation"),
            )
        )
        self._workspace = Path(workspace_root).resolve()

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        query = invocation.input.get("query")
        glob_pattern = str(invocation.input.get("glob", "*"))
        max_results = int(invocation.input.get("max_results", 50))
        include_hidden = bool(invocation.input.get("include_hidden", False))
        case_sensitive = bool(invocation.input.get("case_sensitive", False))

        matches: list[dict[str, object]] = []
        truncated = False

        for path, rel in _iter_workspace_files(self._workspace, include_hidden=include_hidden):
            if query and not _contains(path.name, str(query), case_sensitive=case_sensitive):
                continue
            if not _glob_matches(rel, glob_pattern, case_sensitive=case_sensitive):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            matches.append(
                {
                    "path": str(path),
                    "relative_path": rel,
                    "name": path.name,
                    "size_bytes": size,
                }
            )
            if len(matches) >= max_results:
                truncated = True
                break

        return ToolResult(
            invocation_id=invocation.invocation_id,
            task_id=invocation.task_id,
            step_id=invocation.step_id,
            status="success",
            output={
                "workspace_root": str(self._workspace),
                "matches": matches,
                "count": len(matches),
                "truncated": truncated,
            },
        )


class SearchTextTool(Tool):
    """Fixed-string text search across workspace files."""

    def __init__(self, *, workspace_root: str) -> None:
        super().__init__(
            ToolManifest(
                id="tool.core.search_text",
                name="search_text",
                namespace="core.fs",
                version="1.0.0",
                description=(
                    "Search for a fixed text string inside workspace files. "
                    "Use path_glob to narrow the search, for example docs/*.md."
                ),
                summary="Search text inside workspace files.",
                capabilities=["fs.grep", "fs.search_text", "core.search_text"],
                risk_level="read_only",
                side_effects=False,
                input_schema=_SEARCH_TEXT_INPUT_SCHEMA,
                output_schema=_SEARCH_TEXT_OUTPUT_SCHEMA,
                policies=PolicyRequirements(),
                resources=ResourceLimits(timeout_seconds=60, max_file_size_mb=10),
                sandbox=SandboxSpec(profile="read_only_file_access"),
                verifier=VerifierSpec(type="schema_validation"),
            )
        )
        self._workspace = Path(workspace_root).resolve()

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        query = str(invocation.input["query"])
        path_glob = str(invocation.input.get("path_glob", "*"))
        max_results = int(invocation.input.get("max_results", 50))
        max_file_bytes = int(invocation.input.get("max_file_bytes", 1_000_000))
        include_hidden = bool(invocation.input.get("include_hidden", False))
        case_sensitive = bool(invocation.input.get("case_sensitive", False))

        needle = query if case_sensitive else query.lower()
        matches: list[dict[str, object]] = []
        searched_files = 0
        skipped_files = 0
        truncated = False

        for path, rel in _iter_workspace_files(self._workspace, include_hidden=include_hidden):
            if not _glob_matches(rel, path_glob, case_sensitive=case_sensitive):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                skipped_files += 1
                continue
            if size > max_file_bytes:
                skipped_files += 1
                continue
            try:
                data = path.read_bytes()
            except OSError:
                skipped_files += 1
                continue
            if b"\x00" in data:
                skipped_files += 1
                continue

            searched_files += 1
            text = data.decode("utf-8", errors="replace")
            haystack_lines = text.splitlines()
            for line_no, line in enumerate(haystack_lines, start=1):
                comparable = line if case_sensitive else line.lower()
                if needle not in comparable:
                    continue
                matches.append(
                    {
                        "path": str(path),
                        "relative_path": rel,
                        "line_number": line_no,
                        "line": _truncate_line(line),
                    }
                )
                if len(matches) >= max_results:
                    truncated = True
                    break
            if truncated:
                break

        return ToolResult(
            invocation_id=invocation.invocation_id,
            task_id=invocation.task_id,
            step_id=invocation.step_id,
            status="success",
            output={
                "workspace_root": str(self._workspace),
                "matches": matches,
                "count": len(matches),
                "truncated": truncated,
                "searched_files": searched_files,
                "skipped_files": skipped_files,
            },
        )


def _iter_workspace_files(workspace: Path, *, include_hidden: bool) -> Iterator[tuple[Path, str]]:
    """Yield resolved regular files that stay inside workspace."""
    for root_raw, dir_names, file_names in os.walk(workspace, topdown=True):
        root = Path(root_raw)
        dir_names[:] = [
            name
            for name in dir_names
            if name not in _ALWAYS_SKIP_DIRS and (include_hidden or not name.startswith("."))
        ]
        for file_name in sorted(file_names):
            candidate = root / file_name
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if not _is_inside(resolved, workspace):
                continue
            if not resolved.is_file():
                continue
            try:
                rel = resolved.relative_to(workspace).as_posix()
            except ValueError:
                continue
            if not include_hidden and _has_hidden_part(rel):
                continue
            yield resolved, rel


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _has_hidden_part(relative_path: str) -> bool:
    return any(part.startswith(".") for part in relative_path.split("/"))


def _contains(value: str, query: str, *, case_sensitive: bool) -> bool:
    if case_sensitive:
        return query in value
    return query.lower() in value.lower()


def _glob_matches(relative_path: str, pattern: str, *, case_sensitive: bool) -> bool:
    if pattern in {"*", "**", "**/*"}:
        return True
    value = relative_path if case_sensitive else relative_path.lower()
    pat = pattern if case_sensitive else pattern.lower()
    name = value.rsplit("/", 1)[-1]
    return fnmatch.fnmatch(value, pat) or fnmatch.fnmatch(name, pat)


def _truncate_line(line: str) -> str:
    clean = line.strip()
    if len(clean) <= _MAX_LINE_CHARS:
        return clean
    return clean[: _MAX_LINE_CHARS - 3] + "..."
