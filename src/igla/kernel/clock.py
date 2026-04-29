"""Time abstraction.

A pluggable clock keeps tests deterministic and makes it easy to swap in a
monotonic / mocked source when the kernel grows scheduled actions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(tz=timezone.utc)


class FixedClock:
    """Test helper: returns the same value every time."""

    def __init__(self, value: datetime) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value


class StepClock:
    """Test helper: advances by ``step`` every call."""

    def __init__(self, start: datetime, step_seconds: float = 1.0) -> None:
        self._current = start
        self._step = step_seconds

    def now(self) -> datetime:
        current = self._current
        from datetime import timedelta

        self._current = current + timedelta(seconds=self._step)
        return current
