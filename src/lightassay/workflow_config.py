"""Workflow config loader with strict validation.

See docs/workflow_config_spec.md for the full specification.
No defaults, no guessed values, no normalization.

The config describes an execution binding — a runnable callable / HTTP
endpoint / command — plus optional LLM-under-test metadata. Execution
binding and LLM metadata are deliberately separate: a workflow whose
target is a plain Python callable does not need fake ``provider`` /
``model`` placeholder strings.

Execution binding shape — exactly one of:

- ``adapter`` (string): legacy raw executable path, resolved relative
  to the config file directory.
- ``driver`` (object): first-party adapter driver config with an
  explicit ``type`` tag. See docs/adapter_pack_spec.md.

LLM metadata shape (all optional):

- Top-level ``provider`` / ``model`` strings (legacy);
- or an ``llm_metadata`` object ``{"provider": ..., "model": ...}`` with
  the same two optional string fields. When both forms are present the
  ``llm_metadata`` values win for any slot they set.

Either form is accepted; neither is required.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from .adapter_pack import CommandDriverConfig, DriverConfig, validate_driver_config
from .errors import WorkflowConfigError

_REQUIRED = {"workflow_id"}
_LLM_TOP_LEVEL_FIELDS = {"provider", "model"}
_ALLOWED_FIELDS = _REQUIRED | _LLM_TOP_LEVEL_FIELDS | {"adapter", "driver", "llm_metadata"}
_ALLOWED_LLM_METADATA_FIELDS = {"provider", "model"}


@dataclass
class LLMMetadata:
    """Optional LLM-under-test metadata.

    Provider and model are optional strings. Either slot may be empty
    when the target is not an LLM at all (a plain Python callable, HTTP
    endpoint, or command) — quickstart no longer invents placeholder
    values for these.
    """

    provider: str | None
    model: str | None

    def is_empty(self) -> bool:
        return not (self.provider or self.model)


@dataclass
class WorkflowConfig:
    """Validated workflow configuration.

    Exactly one of ``adapter`` or ``driver`` is set; the other is ``None``.
    ``llm_metadata`` is always present; its fields may both be ``None``
    when the workflow under test is not an LLM call.
    """

    workflow_id: str
    adapter: str | None  # Resolved absolute path (legacy executable)
    driver: DriverConfig | None  # First-party driver config
    llm_metadata: LLMMetadata

    @property
    def provider(self) -> str | None:
        """Optional provider metadata for the workflow under test."""
        return self.llm_metadata.provider

    @property
    def model(self) -> str | None:
        """Optional model metadata for the workflow under test."""
        return self.llm_metadata.model


def load_workflow_config(path: str) -> WorkflowConfig:
    """Load and validate a workflow config JSON file.

    Raises WorkflowConfigError on any violation.
    Returns a WorkflowConfig with the adapter path resolved to an absolute
    path (when ``adapter`` is used) or with a validated DriverConfig (when
    ``driver`` is used).
    """
    if not os.path.isfile(path):
        raise WorkflowConfigError(f"Workflow config file not found: {path!r}")

    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise WorkflowConfigError(
            f"Workflow config file is not valid JSON: {path!r}: {exc}"
        ) from None

    if not isinstance(data, dict):
        raise WorkflowConfigError(
            f"Workflow config must be a JSON object, got {type(data).__name__}"
        )

    # Check for unknown keys.
    unknown = set(data.keys()) - _ALLOWED_FIELDS
    if unknown:
        raise WorkflowConfigError(
            f"Workflow config has unknown fields: {', '.join(sorted(unknown))}. "
            f"Only {', '.join(sorted(_ALLOWED_FIELDS))} are allowed."
        )

    # Required fields.
    for field in sorted(_REQUIRED):
        if field not in data:
            raise WorkflowConfigError(f"Workflow config missing required field: {field!r}")
        value = data[field]
        if not isinstance(value, str):
            raise WorkflowConfigError(
                f"Workflow config field {field!r} must be a string, got {type(value).__name__}"
            )
        if not value.strip():
            raise WorkflowConfigError(f"Workflow config field {field!r} must be a non-empty string")

    # Optional top-level LLM metadata fields.
    for field in _LLM_TOP_LEVEL_FIELDS:
        if field in data:
            value = data[field]
            if not isinstance(value, str) or not value.strip():
                raise WorkflowConfigError(
                    f"Workflow config field {field!r} must be a non-empty string when provided"
                )

    llm_metadata = _parse_llm_metadata(data)

    # Exactly one of adapter / driver must be present.
    has_adapter = "adapter" in data
    has_driver = "driver" in data

    if has_adapter and has_driver:
        raise WorkflowConfigError(
            "Workflow config must have exactly one of 'adapter' or 'driver', not both."
        )
    if not has_adapter and not has_driver:
        raise WorkflowConfigError(
            "Workflow config missing required field: must have either "
            "'adapter' (executable path) or 'driver' (first-party driver config)."
        )

    # ── Legacy adapter path ────────────────────────────────────────────
    if has_adapter:
        adapter_val = data["adapter"]
        if not isinstance(adapter_val, str):
            raise WorkflowConfigError(
                f"Workflow config field 'adapter' must be a string, "
                f"got {type(adapter_val).__name__}"
            )
        if not adapter_val.strip():
            raise WorkflowConfigError("Workflow config field 'adapter' must be a non-empty string")

        config_dir = os.path.dirname(os.path.abspath(path))
        if os.path.isabs(adapter_val):
            adapter_resolved = adapter_val
        else:
            adapter_resolved = os.path.normpath(os.path.join(config_dir, adapter_val))

        return WorkflowConfig(
            workflow_id=data["workflow_id"],
            adapter=adapter_resolved,
            driver=None,
            llm_metadata=llm_metadata,
        )

    # ── First-party driver ─────────────────────────────────────────────
    driver_val = data["driver"]
    try:
        driver_config = validate_driver_config(driver_val)
    except ValueError as exc:
        raise WorkflowConfigError(f"Workflow config field 'driver' is invalid: {exc}") from None

    # Inject config-origin for command drivers so that the subprocess
    # runs with cwd=config_dir and relative paths in the command array
    # resolve against the config file location, not the caller's cwd.
    if isinstance(driver_config, CommandDriverConfig):
        config_dir = os.path.dirname(os.path.abspath(path))
        driver_config = CommandDriverConfig(
            command=driver_config.command,
            config_dir=config_dir,
        )

    return WorkflowConfig(
        workflow_id=data["workflow_id"],
        adapter=None,
        driver=driver_config,
        llm_metadata=llm_metadata,
    )


def _parse_llm_metadata(data: dict) -> LLMMetadata:
    """Extract optional LLM metadata from either the legacy top-level
    ``provider`` / ``model`` keys or the dedicated ``llm_metadata``
    object. The dedicated object wins for any slot it sets.
    """
    top_provider = data.get("provider")
    top_model = data.get("model")

    nested_provider: str | None = None
    nested_model: str | None = None
    if "llm_metadata" in data:
        nested = data["llm_metadata"]
        if not isinstance(nested, dict):
            raise WorkflowConfigError("Workflow config field 'llm_metadata' must be a JSON object")
        unknown = set(nested.keys()) - _ALLOWED_LLM_METADATA_FIELDS
        if unknown:
            raise WorkflowConfigError(
                f"Workflow config 'llm_metadata' has unknown fields: "
                f"{', '.join(sorted(unknown))}. "
                f"Only {', '.join(sorted(_ALLOWED_LLM_METADATA_FIELDS))} are allowed."
            )
        for key in ("provider", "model"):
            if key in nested:
                value = nested[key]
                if not isinstance(value, str) or not value.strip():
                    raise WorkflowConfigError(
                        f"Workflow config 'llm_metadata.{key}' must be a "
                        "non-empty string when provided"
                    )
        nested_provider = nested.get("provider")
        nested_model = nested.get("model")

    return LLMMetadata(
        provider=nested_provider if nested_provider is not None else top_provider,
        model=nested_model if nested_model is not None else top_model,
    )
