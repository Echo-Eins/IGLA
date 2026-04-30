"""Console encoding helpers.

On some Linux setups (especially containers, minimal locales, or when the
user's ``$LC_ALL`` is C/POSIX) Python's stdio falls back to ASCII or
windows-1251, and ``input()`` then fails to decode Cyrillic / emoji bytes
with ``UnicodeDecodeError: invalid continuation byte``.

We never want IGLA to crash on user input. This module provides:

* ``force_utf8_stdio`` — reconfigure stdin/stdout/stderr to UTF-8 with
  ``errors="replace"``. Safe to call repeatedly.
* ``safe_readline`` — read one line from a TTY, surviving any encoding error
  by falling back to a raw byte read + permissive decode. Returns ``""`` on
  EOF.

Importing ``readline`` (when available) installs GNU readline as the line
editor for ``input()``: that is what gives the user backspace, arrow keys,
and history. Without this import, terminals that do not pre-process line
edits send raw control bytes through to Python and the user sees garbled
input. We import it eagerly, but tolerate platforms (Windows without
pyreadline) that don't ship the module.
"""
from __future__ import annotations

import io
import re
import sys
from contextlib import suppress

with suppress(ImportError):
    import readline  # noqa: F401  — side effect: enables line editing in input()


_ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def force_utf8_stdio() -> None:
    """Force stdin/stdout/stderr to UTF-8 with replace-on-error.

    Uses ``TextIOWrapper.reconfigure`` (Python 3.7+). Failures are silent
    because the streams may already be detached or non-text.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            with suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")


def safe_readline(prompt: str = "") -> str:
    """Read one line, never raising on bad encoding.

    Order of attempts:
    1. ``input(prompt)`` — fast path.
    2. If that raises ``UnicodeDecodeError`` or ``UnicodeError``: drop to the
       underlying ``sys.stdin.buffer`` and decode with ``errors="replace"``.
    3. EOF / KeyboardInterrupt → return ``""``.
    """
    try:
        if prompt:
            try:
                sys.stdout.write(prompt)
                sys.stdout.flush()
            except Exception:  # noqa: BLE001
                pass
        return _clean_interactive_line(input())
    except (EOFError, KeyboardInterrupt):
        return ""
    except UnicodeError:
        return _byte_fallback_readline()
    except OSError:
        # Some terminals (or detached stdio) raise OSError on read; treat
        # as EOF to keep the REPL responsive.
        return ""


def _byte_fallback_readline() -> str:
    buf = getattr(sys.stdin, "buffer", None)
    if buf is None:
        return ""
    try:
        raw = buf.readline()
    except Exception:  # noqa: BLE001
        return ""
    if not raw:
        return ""
    text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
    return _clean_interactive_line(text)


def _clean_interactive_line(text: str) -> str:
    """Apply minimal terminal-edit semantics to a captured input line.

    Some debug terminals leak control bytes into stdin instead of applying
    canonical line editing. Keeping those bytes corrupts task text (for
    example, a deleted prefix can survive as part of the next request).
    """
    if not text:
        return text

    text = _ANSI_CSI_RE.sub("", text)
    chars: list[str] = []
    for ch in text:
        if ch in ("\b", "\x7f"):
            if chars:
                chars.pop()
            continue
        if ch == "\t":
            chars.append(ch)
            continue
        if ord(ch) < 32 or ch == "\x1b":
            continue
        chars.append(ch)
    return "".join(chars)


def attach_utf8_buffer() -> None:
    """If stdin/stdout are still wrapped with the wrong encoding (e.g. inside
    some terminals on Python <3.11), replace them with TextIOWrapper(UTF-8).

    This is the second-line fix used when ``reconfigure`` is not supported by
    the current stream. Idempotent: skips if already UTF-8.
    """
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        encoding = getattr(stream, "encoding", "") or ""
        if encoding.lower().replace("_", "-") == "utf-8":
            continue
        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            continue
        try:
            new = io.TextIOWrapper(
                buffer,
                encoding="utf-8",
                errors="replace",
                line_buffering=True,
                write_through=True,
            )
            setattr(sys, name, new)
        except Exception:  # noqa: BLE001
            pass
