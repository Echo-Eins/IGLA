"""Chat REPL — the user-facing surface of IGLA.

The REPL is intentionally thin: it owns the I/O channel and the loop,
delegates everything else to the kernel + planner.
"""
from .repl import ChatREPL, ChatTranscript

__all__ = ["ChatREPL", "ChatTranscript"]
