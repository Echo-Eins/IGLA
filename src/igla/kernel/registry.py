"""Tool registry.

The registry holds ``(manifest, tool)`` pairs. Tools are looked up by:

* exact ``(name, version)``;
* by capability + constraints (returns the highest-version match);
* by name only (returns the highest version).

Versions are compared as PEP-440 lite (``MAJOR.MINOR.PATCH`` numerics with
optional pre-release). For the MVP we only need monotonic numeric compare.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from ..protocol.manifest import ToolManifest
from .errors import KernelError, ToolNotFoundError

# avoid circular import with tools.base
if TYPE_CHECKING:  # pragma: no cover
    from ..tools.base import Tool


def _parse_version(version: str) -> tuple[int, ...]:
    parts = version.split("-", 1)[0].split(".")
    out: list[int] = []
    for part in parts:
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def _normalize_version_ref(version: str | None) -> str | None:
    if version is None:
        return None
    if len(version) > 1 and version[0] in {"v", "V"} and version[1].isdigit():
        return version[1:]
    return version


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[tuple[str, str], Tool] = {}
        self._manifests: dict[tuple[str, str], ToolManifest] = {}

    # --- registration ---------------------------------------------------

    def register(self, tool: Tool) -> None:
        manifest = tool.manifest
        key = (manifest.name, manifest.version)
        if key in self._manifests:
            raise KernelError(f"tool already registered: {manifest.name}@{manifest.version}")
        self._tools[key] = tool
        self._manifests[key] = manifest

    def register_many(self, tools: Iterable[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    # --- lookup ---------------------------------------------------------

    def get(self, name: str, version: str | None = None) -> ToolManifest:
        manifest = self._lookup_manifest(name, version)
        if manifest is None:
            raise ToolNotFoundError(f"tool not found: {name}@{version or '*'}")
        return manifest

    def get_tool(self, name: str, version: str | None = None) -> Tool:
        manifest = self.get(name, version)
        return self._tools[(manifest.name, manifest.version)]

    def list_tools(self) -> list[ToolManifest]:
        return list(self._manifests.values())

    def has(self, name: str, version: str | None = None) -> bool:
        return self._lookup_manifest(name, version) is not None

    def resolve(self, capability: str, constraints: dict[str, Any] | None = None) -> ToolManifest:
        del constraints  # constraints support is intentionally minimal in MVP
        candidates = [m for m in self._manifests.values() if capability in m.capabilities]
        if not candidates:
            raise ToolNotFoundError(f"no tool advertises capability: {capability}")
        candidates.sort(key=lambda m: _parse_version(m.version), reverse=True)
        return candidates[0]

    # --- internals ------------------------------------------------------

    def _lookup_manifest(self, name: str, version: str | None) -> ToolManifest | None:
        version = _normalize_version_ref(version)
        matches = [m for k, m in self._manifests.items() if k[0] == name]
        if not matches:
            return None
        if version is not None:
            exact = self._manifests.get((name, version))
            if exact is not None:
                return exact
            # Model sent a valid-format but wrong version string — fall back to
            # the highest registered version rather than hard-failing. The policy
            # engine already enforces that the *name* is known; the version is
            # advisory (the model frequently hallucinates it).
        matches.sort(key=lambda m: _parse_version(m.version), reverse=True)
        return matches[0]
