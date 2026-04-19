"""Semantic adapter config loader with strict validation.

See docs/semantic_adapter_spec.md for the full specification.
No defaults, no guessed values, no normalization.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from .errors import SemanticConfigError

_REQUIRED_FIELDS = {"adapter", "provider", "model"}


@dataclass
class SemanticConfig:
    """Validated semantic adapter configuration.

    Two invocation modes coexist (file-backed or command-backed); see
    :class:`lightassay.preparation_config.PreparationConfig` for the
    full model.
    """

    adapter: str
    provider: str
    model: str
    command: list[str] | None = None
    env: dict[str, str] | None = None

    def invocation(self) -> list[str]:
        """Return the argv list used to invoke the adapter subprocess."""
        if self.command:
            return list(self.command)
        return [self.adapter]

    def subprocess_env(self) -> dict[str, str] | None:
        """Return the merged environment for adapter subprocess execution."""
        if not self.env:
            return None
        merged = os.environ.copy()
        merged.update(self.env)
        return merged


def load_semantic_config(path: str) -> SemanticConfig:
    """Load and validate a semantic config JSON file.

    Raises SemanticConfigError on any violation.
    Returns a SemanticConfig with the adapter path resolved to an absolute path.
    """
    if not os.path.isfile(path):
        raise SemanticConfigError(f"Semantic config file not found: {path!r}")

    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise SemanticConfigError(
            f"Semantic config file is not valid JSON: {path!r}: {exc}"
        ) from None

    if not isinstance(data, dict):
        raise SemanticConfigError(
            f"Semantic config must be a JSON object, got {type(data).__name__}"
        )

    # Check for unknown keys.
    unknown = set(data.keys()) - _REQUIRED_FIELDS
    if unknown:
        raise SemanticConfigError(
            f"Semantic config has unknown fields: {', '.join(sorted(unknown))}. "
            f"Only {', '.join(sorted(_REQUIRED_FIELDS))} are allowed."
        )

    # Check all required fields are present and are non-empty strings.
    for field in sorted(_REQUIRED_FIELDS):
        if field not in data:
            raise SemanticConfigError(f"Semantic config missing required field: {field!r}")
        value = data[field]
        if not isinstance(value, str):
            raise SemanticConfigError(
                f"Semantic config field {field!r} must be a string, got {type(value).__name__}"
            )
        if not value.strip():
            raise SemanticConfigError(f"Semantic config field {field!r} must be a non-empty string")

    # Resolve adapter path relative to config file directory.
    config_dir = os.path.dirname(os.path.abspath(path))
    adapter_raw = data["adapter"]
    if os.path.isabs(adapter_raw):
        adapter_resolved = adapter_raw
    else:
        adapter_resolved = os.path.normpath(os.path.join(config_dir, adapter_raw))

    return SemanticConfig(
        adapter=adapter_resolved,
        provider=data["provider"],
        model=data["model"],
    )
