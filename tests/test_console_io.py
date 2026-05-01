"""Tests for the console encoding fallback."""
from __future__ import annotations

import io
import sys

from igla.console_io import _byte_fallback_readline, _clean_interactive_line, safe_readline


def test_byte_fallback_decodes_cyrillic_bytes(monkeypatch) -> None:
    payload = "Прочитай файл\n".encode()
    fake_stdin = io.BytesIO(payload)
    # ``sys.stdin`` may not exist as a real text wrapper in tests, but the
    # fallback only needs ``.buffer``. We construct a tiny stand-in.

    class _Stdin:
        buffer = fake_stdin

    monkeypatch.setattr(sys, "stdin", _Stdin())
    assert _byte_fallback_readline() == "Прочитай файл"


def test_safe_readline_uses_input_when_available(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda: "hello")
    assert safe_readline("> ") == "hello"


def test_safe_readline_returns_empty_on_eof(monkeypatch) -> None:
    def _eof() -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    assert safe_readline() == ""


def test_safe_readline_falls_back_on_unicode_error(monkeypatch) -> None:
    def _bad() -> str:
        raise UnicodeDecodeError("utf-8", b"\xd1", 0, 1, "invalid start byte")

    monkeypatch.setattr("builtins.input", _bad)

    class _Stdin:
        buffer = io.BytesIO(b"ok\n")

    monkeypatch.setattr(sys, "stdin", _Stdin())
    assert safe_readline() == "ok"


def test_clean_interactive_line_applies_backspace_and_delete() -> None:
    assert _clean_interactive_line("abc\x7fd") == "abd"
    assert _clean_interactive_line("xy\b\bfind README.md") == "find README.md"


def test_clean_interactive_line_drops_ansi_sequences() -> None:
    assert _clean_interactive_line("\x1b[31mREADME.md\x1b[0m") == "README.md"
