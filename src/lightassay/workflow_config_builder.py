"""Build in-memory ``WorkflowConfig`` objects from execution shapes.

Quickstart must not force the user to hand-author workflow config JSON.
Given a bootstrap-resolved :class:`ExecutionShape`, :func:`build_workflow_config`
returns a fully-validated :class:`WorkflowConfig` the runner can use
directly. Optional LLM-under-test metadata (provider / model) can be
supplied by the caller when the target actually is an LLM call; it is
never invented by the builder.

The builder only wraps existing validators — it does not relax any of
the structural invariants enforced by ``workflow_config.load_workflow_config``.
"""

from __future__ import annotations

import json
import os

from .adapter_pack import (
    CommandDriverConfig,
    HttpDriverConfig,
    PythonCallableDriverConfig,
)
from .bootstrap import (
    EXEC_SHAPE_COMMAND,
    EXEC_SHAPE_HTTP,
    EXEC_SHAPE_PYTHON,
    ExecutionShape,
)
from .errors import WorkflowConfigError
from .workflow_config import LLMMetadata, WorkflowConfig, load_workflow_config


def build_workflow_config(
    shape: ExecutionShape,
    *,
    workflow_id: str,
    llm_metadata: LLMMetadata | None = None,
    workspace_root: str | None = None,
) -> WorkflowConfig:
    """Turn a bootstrap ``ExecutionShape`` into a validated ``WorkflowConfig``.

    ``llm_metadata`` is optional and only meaningful when the target
    itself is an LLM call; for plain Python / HTTP / command targets it
    should be omitted rather than filled with placeholders.
    """
    if not isinstance(workflow_id, str) or not workflow_id.strip():
        raise WorkflowConfigError(
            "Generated workflow config field 'workflow_id' must be a non-empty string"
        )

    if shape.type == EXEC_SHAPE_PYTHON:
        if not shape.module or not shape.function:
            raise WorkflowConfigError(
                "Generated python-callable workflow requires 'module' and 'function'."
            )
        driver = PythonCallableDriverConfig(module=shape.module, function=shape.function)
    elif shape.type == EXEC_SHAPE_HTTP:
        if not shape.url or not shape.method:
            raise WorkflowConfigError("Generated http workflow requires 'url' and 'method'.")
        driver = HttpDriverConfig(
            url=shape.url,
            method=shape.method,
            headers=dict(shape.headers) if shape.headers is not None else None,
            timeout_seconds=shape.timeout_seconds,
        )
    elif shape.type == EXEC_SHAPE_COMMAND:
        if not shape.command:
            raise WorkflowConfigError(
                "Generated command workflow requires a non-empty 'command' list."
            )
        abs_root = os.path.abspath(workspace_root) if workspace_root else None
        driver = CommandDriverConfig(command=list(shape.command), config_dir=abs_root)
    else:
        raise WorkflowConfigError(
            f"Cannot build workflow config for unknown execution shape type {shape.type!r}"
        )

    return WorkflowConfig(
        workflow_id=workflow_id,
        adapter=None,
        driver=driver,
        llm_metadata=llm_metadata or LLMMetadata(provider=None, model=None),
    )


def write_workflow_config(
    shape: ExecutionShape,
    *,
    workflow_id: str,
    llm_metadata: LLMMetadata | None = None,
    path: str,
) -> WorkflowConfig:
    """Serialize a generated workflow config to *path* and return the loaded config.

    The file is written so the runner can compute ``workflow_config_sha256``
    and so the generated config is auditable. The parent directory must
    already exist.
    """
    data: dict = {
        "workflow_id": workflow_id,
        "driver": _shape_to_driver_json(shape),
    }
    if llm_metadata is not None and not llm_metadata.is_empty():
        md: dict = {}
        if llm_metadata.provider:
            md["provider"] = llm_metadata.provider
        if llm_metadata.model:
            md["model"] = llm_metadata.model
        data["llm_metadata"] = md
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return load_workflow_config(path)


def _shape_to_driver_json(shape: ExecutionShape) -> dict:
    if shape.type == EXEC_SHAPE_PYTHON:
        if not shape.module or not shape.function:
            raise WorkflowConfigError(
                "Generated python-callable workflow requires 'module' and 'function'."
            )
        return {"type": shape.type, "module": shape.module, "function": shape.function}
    if shape.type == EXEC_SHAPE_HTTP:
        if not shape.url or not shape.method:
            raise WorkflowConfigError("Generated http workflow requires 'url' and 'method'.")
        payload: dict = {"type": shape.type, "url": shape.url, "method": shape.method}
        if shape.headers is not None:
            payload["headers"] = dict(shape.headers)
        if shape.timeout_seconds is not None:
            payload["timeout_seconds"] = shape.timeout_seconds
        return payload
    if shape.type == EXEC_SHAPE_COMMAND:
        if not shape.command:
            raise WorkflowConfigError(
                "Generated command workflow requires a non-empty 'command' list."
            )
        return {"type": shape.type, "command": list(shape.command)}
    raise WorkflowConfigError(
        f"Cannot serialize workflow config for unknown execution shape type {shape.type!r}"
    )
