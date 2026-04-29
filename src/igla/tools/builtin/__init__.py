"""Built-in tools used by the MVP runtime.

The set is intentionally tiny:

* ``ask_user`` — solicit input from the user via the chat channel.
* ``read_file`` — read a workspace file and emit a FileSnapshot artifact +
  a read receipt (consumed later by ``read_before_write``).
* ``noop_observe`` — declarative no-op used by the planner to log a
  decision/observation without touching the world.

The complex multi-step affordances (``ask_user_clarification``,
``todo_branch``, ``todo_complete``, ``declare_task_done``) are NOT tools —
they are first-class actions handled directly by the planner loop and
recorded as events. This keeps the protocol cleanly separated:
``ToolInvocation`` is for "did something to the world", and the action
proposals are for "navigated the TODO/plan".
"""
from .ask_user import AskUserTool, AskUserChannel, ConsoleAskUserChannel
from .noop_observe import NoopObserveTool
from .read_file import ReadFileTool

__all__ = [
    "AskUserChannel",
    "AskUserTool",
    "ConsoleAskUserChannel",
    "NoopObserveTool",
    "ReadFileTool",
]


def build_default_toolset(
    *,
    ask_user_channel: AskUserChannel,
    workspace_root: str,
    receipts,  # ReceiptManager
) -> list:
    """Construct the default in-process tool instances."""
    return [
        AskUserTool(channel=ask_user_channel),
        ReadFileTool(workspace_root=workspace_root, receipts=receipts),
        NoopObserveTool(),
    ]
