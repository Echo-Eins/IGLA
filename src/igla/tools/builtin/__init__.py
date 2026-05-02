"""Built-in tools used by the MVP runtime.

The set is intentionally small but split along risk lines:

Read-only / discovery:
* ``ask_user``         — solicit input from the user via the chat channel.
* ``read_file``        — read a workspace file; emits a FileReadReceipt
                          (consumed by ``read_before_write``).
* ``list_dir``         — list workspace directory contents with depth.
* ``find_files``       — workspace-bounded filename discovery.
* ``search_text``      — workspace-bounded fixed-string search.
* ``verify_file``      — receipt-gated file verifier (syntax/lint/pytest).
* ``read_task_log``    — read the model's own per-task work history.
* ``noop_observe``     — declarative no-op for the planner.

Mutating (require backup + receipt invariants):
* ``copy_file``        — copy a workspace file to a new path, optional append.
* ``patch_file``       — edit a workspace file with mandatory backup.
* ``restore_file``     — revert a prior patch_file invocation.

The complex multi-step affordances (``ask_user_clarification``,
``todo_branch``, ``todo_complete``, ``declare_task_done``) are NOT tools —
they are first-class actions handled directly by the planner loop and
recorded as events. This keeps the protocol cleanly separated:
``ToolInvocation`` is for "did something to the world", and the action
proposals are for "navigated the TODO/plan".
"""
from ...kernel.receipt_manager import ReceiptManager
from ...kernel.rollback_manager import RollbackManager
from ...kernel.task_work_log import TaskWorkLog
from ..base import Tool
from .ask_user import AskUserChannel, AskUserTool, ConsoleAskUserChannel
from .copy_file import CopyFileTool
from .list_dir import ListDirTool
from .noop_observe import NoopObserveTool
from .patch_file import PatchFileTool
from .read_file import ReadFileTool
from .read_task_log import ReadTaskLogTool
from .restore_file import RestoreFileTool
from .search import FindFilesTool, SearchTextTool
from .verify_file import VerifyFileTool

__all__ = [
    "AskUserChannel",
    "AskUserTool",
    "ConsoleAskUserChannel",
    "CopyFileTool",
    "FindFilesTool",
    "ListDirTool",
    "NoopObserveTool",
    "PatchFileTool",
    "ReadFileTool",
    "ReadTaskLogTool",
    "RestoreFileTool",
    "SearchTextTool",
    "VerifyFileTool",
]


def build_default_toolset(
    *,
    ask_user_channel: AskUserChannel,
    workspace_root: str,
    receipts: ReceiptManager,
    rollback: RollbackManager,
    work_log: TaskWorkLog,
) -> list[Tool]:
    """Construct the default in-process tool instances."""
    return [
        AskUserTool(channel=ask_user_channel),
        FindFilesTool(workspace_root=workspace_root),
        ListDirTool(workspace_root=workspace_root),
        ReadFileTool(workspace_root=workspace_root, receipts=receipts),
        SearchTextTool(workspace_root=workspace_root),
        CopyFileTool(
            workspace_root=workspace_root,
            receipts=receipts,
            rollback=rollback,
        ),
        PatchFileTool(
            workspace_root=workspace_root,
            receipts=receipts,
            rollback=rollback,
        ),
        RestoreFileTool(
            workspace_root=workspace_root,
            receipts=receipts,
            rollback=rollback,
        ),
        VerifyFileTool(
            workspace_root=workspace_root,
            receipts=receipts,
        ),
        ReadTaskLogTool(work_log=work_log),

        NoopObserveTool(),
    ]
