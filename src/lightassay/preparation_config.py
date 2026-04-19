"""Preparation adapter config loader with strict validation.

See docs/preparation_protocol.md for the full specification.
No defaults, no guessed values, no normalization.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from .errors import PreparationConfigError

_REQUIRED_FIELDS = {"adapter", "provider", "model"}


@dataclass
class PreparationConfig:
    """Validated preparation adapter configuration.

    Two invocation modes coexist:

    - File-backed (loaded from JSON): ``adapter`` is an absolute path to
      an executable script; ``command`` is ``None``.
    - Command-backed (constructed in-memory, e.g. by ``backends``):
      ``command`` is a full argv list, ``adapter`` is a human-readable
      command label, and ``env`` carries any extra environment entries
      required to launch the subprocess honestly.
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


def load_preparation_config(path: str) -> PreparationConfig:
    """Load and validate a preparation config JSON file.

    Raises PreparationConfigError on any violation.
    Returns a PreparationConfig with the adapter path resolved to an absolute path.
    """
    if not os.path.isfile(path):
        raise PreparationConfigError(f"Preparation config file not found: {path!r}")

    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise PreparationConfigError(
            f"Preparation config file is not valid JSON: {path!r}: {exc}"
        ) from None

    if not isinstance(data, dict):
        raise PreparationConfigError(
            f"Preparation config must be a JSON object, got {type(data).__name__}"
        )

    # Check for unknown keys.
    unknown = set(data.keys()) - _REQUIRED_FIELDS
    if unknown:
        raise PreparationConfigError(
            f"Preparation config has unknown fields: {', '.join(sorted(unknown))}. "
            f"Only {', '.join(sorted(_REQUIRED_FIELDS))} are allowed."
        )

    # Check all required fields are present and are non-empty strings.
    for field in sorted(_REQUIRED_FIELDS):
        if field not in data:
            raise PreparationConfigError(f"Preparation config missing required field: {field!r}")
        value = data[field]
        if not isinstance(value, str):
            raise PreparationConfigError(
                f"Preparation config field {field!r} must be a string, got {type(value).__name__}"
            )
        if not value.strip():
            raise PreparationConfigError(
                f"Preparation config field {field!r} must be a non-empty string"
            )

    # Resolve adapter path relative to config file directory.
    config_dir = os.path.dirname(os.path.abspath(path))
    adapter_raw = data["adapter"]
    if os.path.isabs(adapter_raw):
        adapter_resolved = adapter_raw
    else:
        adapter_resolved = os.path.normpath(os.path.join(config_dir, adapter_raw))

    return PreparationConfig(
        adapter=adapter_resolved,
        provider=data["provider"],
        model=data["model"],
    )
