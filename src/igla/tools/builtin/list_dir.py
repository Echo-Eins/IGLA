"""``list_dir`` — workspace-bounded directory listing with depth support.

Lists the contents of a directory inside the configured workspace, recursing
up to a requested *depth*.  When the requested depth exceeds ``_DEPTH_LIMIT``
the call is rejected by the ``list_dir_depth_limit`` constitution predicate
before the tool even runs, so the model always receives a structured error
with a clear hint rather than a silent truncation.

Output is a flat, DFS-ordered list of entries.  Each directory entry carries
``file_count`` / ``dir_count`` for its *direct* children and ``expanded: true``
when it was recursed into.  Unexpanded directories (at the depth boundary)
still show direct-child counts so the model can decide whether to drill down.

Path semantics:
* Relative paths are resolved against ``workspace_root``.
* Absolute paths that escape the workspace are rejected.
* Symlinks that resolve outside the workspace are skipped silently.

Depth semantics:
* ``depth=0`` — list only direct contents of the target directory (dirs shown
  with counts but NOT expanded further).
* ``depth=1`` — expand one level; sub-directories are expanded but their
  children are not.
* Default ``depth`` is 1.
* ``depth > _DEPTH_LIMIT`` is rejected by policy; use multiple calls with
  smaller depths to explore large trees.
"""
from __future__ import annotations

from pathlib import Path

from ...protocol.invocation import ToolInvocation
from ...protocol.manifest import (
    PolicyRequirements,
    ResourceLimits,
    SandboxSpec,
    ToolManifest,
    VerifierSpec,
)
from ...protocol.result import ToolError, ToolResult
from ..base import Tool

_DEPTH_LIMIT = 4
_MAX_ENTRIES = 500

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

_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "path": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Relative path inside the workspace to list. "
                "Omit or use '.' for the workspace root."
            ),
        },
        "depth": {
            "type": "integer",
            "minimum": 0,
            "maximum": 10,
            "description": (
                f"Recursion depth (0=direct contents only, 1=one level of sub-dirs, …). "
                f"Default: 1. "
                f"Policy limit: {_DEPTH_LIMIT}. Requesting depth > {_DEPTH_LIMIT} "
                f"returns a DEPTH_LIMIT_EXCEEDED rejection before the tool runs."
            ),
        },
        "include_hidden": {
            "type": "boolean",
            "description": (
                "Include hidden entries (names starting with '.'). Default: false."
            ),
        },
    },
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "workspace_root",
        "root",
        "depth",
        "depth_limit",
        "depth_truncated",
        "total_files",
        "total_dirs",
        "total_size_bytes",
        "entries",
        "truncated",
    ],
    "properties": {
        "workspace_root": {"type": "string"},
        "root": {
            "type": "string",
            "description": "Relative path of the listed directory.",
        },
        "depth": {"type": "integer", "minimum": 0},
        "depth_limit": {
            "type": "integer",
            "minimum": 0,
            "description": "Policy depth limit for reference.",
        },
        "depth_truncated": {
            "type": "boolean",
            "description": (
                "True when at least one directory was not expanded "
                "because it reached the requested depth."
            ),
        },
        "total_files": {
            "type": "integer",
            "minimum": 0,
            "description": "Total number of files found across all expanded directories.",
        },
        "total_dirs": {
            "type": "integer",
            "minimum": 0,
            "description": "Total number of directories found (including unexpanded ones).",
        },
        "total_size_bytes": {
            "type": "integer",
            "minimum": 0,
            "description": "Cumulative size of all files found.",
        },
        "entries": {
            "type": "array",
            "description": (
                "Flat DFS-ordered list of directory and file entries. "
                "Indentation level is given by depth_level. "
                "For dirs: file_count/dir_count are DIRECT (non-recursive) children; "
                "expanded=true means sub-entries follow in the list."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["relative_path", "type", "depth_level"],
                "properties": {
                    "relative_path": {"type": "string"},
                    "type": {"type": "string", "enum": ["dir", "file"]},
                    "depth_level": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "0 = direct child of the listed root.",
                    },
                    "size_bytes": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "File size, or recursive total for expanded dirs.",
                    },
                    "file_count": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Direct file children (dirs only).",
                    },
                    "dir_count": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Direct sub-directory children (dirs only).",
                    },
                    "expanded": {
                        "type": "boolean",
                        "description": "True when this dir was recursed into (dirs only).",
                    },
                },
            },
        },
        "truncated": {
            "type": "boolean",
            "description": "True when output was capped at the entry limit.",
        },
    },
}


class ListDirTool(Tool):
    """List workspace directories with configurable depth."""

    def __init__(self, *, workspace_root: str) -> None:
        super().__init__(
            ToolManifest(
                id="tool.core.list_dir",
                name="list_dir",
                namespace="core.fs",
                version="1.0.0",
                description=(
                    f"List directory contents inside the workspace. "
                    f"Supports depth recursion up to {_DEPTH_LIMIT} levels. "
                    f"Each directory entry shows direct file/dir counts so the model "
                    f"can decide whether to drill deeper. "
                    f"Use before read_file to understand project structure."
                ),
                summary="List workspace directory contents with depth support.",
                capabilities=["fs.list", "fs.discover", "core.list_dir"],
                risk_level="read_only",
                side_effects=False,
                input_schema=_INPUT_SCHEMA,
                output_schema=_OUTPUT_SCHEMA,
                policies=PolicyRequirements(),
                resources=ResourceLimits(timeout_seconds=30, max_file_size_mb=0),
                sandbox=SandboxSpec(profile="read_only_file_access"),
                verifier=VerifierSpec(type="schema_validation"),
            )
        )
        self._workspace = Path(workspace_root).resolve()

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        raw_path = str(invocation.input.get("path") or ".")
        depth = int(invocation.input.get("depth", 1))
        include_hidden = bool(invocation.input.get("include_hidden", False))

        target = self._resolve(raw_path)
        if target is None:
            return _failure(
                invocation,
                code="PATH_OUTSIDE_WORKSPACE",
                message=f"path escapes workspace: {raw_path}",
            )
        if not target.exists():
            return _failure(
                invocation,
                code="DIR_NOT_FOUND",
                message=f"directory not found: {raw_path}",
            )
        if not target.is_dir():
            return _failure(
                invocation,
                code="NOT_A_DIRECTORY",
                message=f"not a directory: {raw_path}",
            )

        try:
            root_rel = target.relative_to(self._workspace).as_posix()
        except ValueError:
            root_rel = "."

        entries: list[dict] = []
        state = {
            "total_files": 0,
            "total_dirs": 0,
            "total_size_bytes": 0,
            "depth_truncated": False,
            "truncated": False,
        }
        _walk(target, 0, depth, self._workspace, include_hidden, entries, state)

        return ToolResult(
            invocation_id=invocation.invocation_id,
            task_id=invocation.task_id,
            step_id=invocation.step_id,
            status="success",
            output={
                "workspace_root": str(self._workspace),
                "root": root_rel,
                "depth": depth,
                "depth_limit": _DEPTH_LIMIT,
                "depth_truncated": state["depth_truncated"],
                "total_files": state["total_files"],
                "total_dirs": state["total_dirs"],
                "total_size_bytes": state["total_size_bytes"],
                "entries": entries,
                "truncated": state["truncated"],
            },
        )

    def _resolve(self, raw_path: str) -> Path | None:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = self._workspace / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        try:
            resolved.relative_to(self._workspace)
        except ValueError:
            return None
        return resolved


def _walk(
    path: Path,
    level: int,
    max_depth: int,
    workspace: Path,
    include_hidden: bool,
    entries: list[dict],
    state: dict[str, object],
) -> tuple[int, int, int]:
    """DFS walk one directory level.

    Returns ``(direct_file_count, direct_dir_count, recursive_size_bytes)``
    so the caller can fill in the parent directory's entry counts.
    """
    if state["truncated"]:
        return 0, 0, 0

    try:
        raw_children = sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    except (PermissionError, OSError):
        return 0, 0, 0

    dirs: list[Path] = []
    files: list[Path] = []
    for child in raw_children:
        name = child.name
        if not include_hidden and name.startswith("."):
            continue
        if child.is_symlink():
            try:
                resolved = child.resolve()
                try:
                    resolved.relative_to(workspace)
                except ValueError:
                    continue  # symlink escapes workspace
            except OSError:
                continue
        if child.is_dir():
            if name in _ALWAYS_SKIP_DIRS:
                continue
            dirs.append(child)
        elif child.is_file():
            files.append(child)

    direct_file_count = len(files)
    direct_dir_count = len(dirs)
    recursive_size = 0

    # Directories first (alphabetical), then files (alphabetical)
    for d in dirs:
        state["total_dirs"] += 1
        expanded = level < max_depth

        rel = _safe_rel(d, workspace)

        if not expanded:
            state["depth_truncated"] = True
            # Show counts of immediate children without recursing
            imm_files, imm_dirs = _count_immediate(d, include_hidden)
            if len(entries) < _MAX_ENTRIES:
                entries.append(
                    {
                        "relative_path": rel,
                        "type": "dir",
                        "depth_level": level,
                        "expanded": False,
                        "file_count": imm_files,
                        "dir_count": imm_dirs,
                        "size_bytes": 0,
                    }
                )
            else:
                state["truncated"] = True
        elif len(entries) < _MAX_ENTRIES:
            idx = len(entries)
            entries.append(
                {
                    "relative_path": rel,
                    "type": "dir",
                    "depth_level": level,
                    "expanded": True,
                    "file_count": 0,
                    "dir_count": 0,
                    "size_bytes": 0,
                }
            )
            sub_files, sub_dirs, sub_size = _walk(
                d, level + 1, max_depth, workspace, include_hidden, entries, state
            )
            entries[idx]["file_count"] = sub_files
            entries[idx]["dir_count"] = sub_dirs
            entries[idx]["size_bytes"] = sub_size
            recursive_size += sub_size
        else:
            state["truncated"] = True

    for f in files:
        try:
            size = f.stat().st_size
        except OSError:
            size = 0
        state["total_files"] += 1
        state["total_size_bytes"] += size
        recursive_size += size
        if len(entries) < _MAX_ENTRIES:
            entries.append(
                {
                    "relative_path": _safe_rel(f, workspace),
                    "type": "file",
                    "depth_level": level,
                    "size_bytes": size,
                }
            )
        else:
            state["truncated"] = True

    return direct_file_count, direct_dir_count, recursive_size


def _count_immediate(path: Path, include_hidden: bool) -> tuple[int, int]:
    """Count direct files and dirs without recursing."""
    try:
        children = list(path.iterdir())
    except (PermissionError, OSError):
        return 0, 0
    files = sum(
        1
        for c in children
        if c.is_file() and (include_hidden or not c.name.startswith("."))
    )
    dirs = sum(
        1
        for c in children
        if c.is_dir()
        and c.name not in _ALWAYS_SKIP_DIRS
        and (include_hidden or not c.name.startswith("."))
    )
    return files, dirs


def _safe_rel(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace).as_posix()
    except (ValueError, OSError):
        return path.name


def _failure(invocation: ToolInvocation, *, code: str, message: str) -> ToolResult:
    return ToolResult(
        invocation_id=invocation.invocation_id,
        task_id=invocation.task_id,
        step_id=invocation.step_id,
        status="failed",
        error=ToolError(
            kind="FileSystemError",
            code=code,
            message=message,
            retryable=code not in {"PATH_OUTSIDE_WORKSPACE", "NOT_A_DIRECTORY"},
            requires_diagnosis=False,
        ),
    )
