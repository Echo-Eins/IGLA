"""IGLA — Iteratively-Generated Local Analysis.

Public surface is intentionally tiny: the runtime is composed via Kernel,
not via direct imports. See docs/02-architecture.md for layering rules.
"""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("igla")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
