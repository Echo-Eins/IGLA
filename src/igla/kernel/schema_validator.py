"""JSON Schema validator wrapper.

Validates ``ToolInvocation.input`` and ``ToolResult.output`` against the
schemas declared in ``ToolManifest``. We use ``jsonschema`` (Draft 2020-12).
"""
from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from ..protocol.manifest import ToolManifest
from .errors import ToolValidationError


class SchemaValidator:
    """Validates inputs and outputs against tool manifests' JSON Schemas.

    Empty schemas (``{}``) are treated as "no constraint", which lets very
    simple tools skip the boilerplate. Use a ``"type": "object"`` schema to
    actually require an object.
    """

    def validate_input(self, manifest: ToolManifest, input_data: dict[str, Any]) -> None:
        self._validate(manifest.input_schema, input_data, where="input")

    def validate_output(self, manifest: ToolManifest, output_data: dict[str, Any]) -> None:
        self._validate(manifest.output_schema, output_data, where="output")

    @staticmethod
    def _validate(schema: dict[str, Any], value: dict[str, Any], *, where: str) -> None:
        if not schema:
            return
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(value), key=lambda e: list(e.absolute_path))
        if not errors:
            return
        messages = [
            f"{'.'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
            for err in errors
        ]
        raise ToolValidationError(
            f"{where} schema validation failed",
            errors=messages,
        )

    @staticmethod
    def explain(error: ValidationError) -> str:  # pragma: no cover - utility
        path = ".".join(str(p) for p in error.absolute_path) or "<root>"
        return f"{path}: {error.message}"
